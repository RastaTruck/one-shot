"""LLM 角度生成 · 基于信号 + 赛道画像，产出 3-4 个候选选题角度。

调用链：信号标题/摘要 + 赛道画像 → call_json_with_fallback (Gemini → DeepSeek → Claude)
       → JSON 数组 → 解析 + 兜底

LLM 不可用（没密钥/网络挂）时返回 None，让上层 forge.py 退化到 mock 占位。
"""
from __future__ import annotations

import json
from typing import Optional

from pipeline.tools import llm_client

from .track_profiles import TRACK_PROFILES, all_profiles_brief


_SYSTEM_PROMPT = """你是中文金融视频选题专家。

给定一条**新闻热点信号** + 两个**赛道画像**，生成 3-4 个**候选选题角度**。

硬性要求：
1. **不复述原标题**——独立生成内容立意，不能"标题党改写"
2. **每个角度匹配一个赛道**，分布建议：金融科普 2 个 + 经济分析 1-2 个
3. **每个角度必须给制作窗口**：
   - "口播" = 6 小时内能制作的快速口播（突发热点用）
   - "深度" = 3-7 天深度内容（持续话题用）
   - "长青" = 季度级常青知识（无时效压力）
4. **避开赛道雷区**——尤其是荐股 / 内幕 / 民族情绪 / 末日论
5. **headline 不超过 35 字**，要有钩子但不标题党

输出严格 JSON 数组（不要包装在对象里，不要 markdown 代码块）：
[
  {
    "label": "角度类型，4-6 字（如：概念拆解 / 反常识切入 / 操作避坑 / 局势推演 / 政策解读）",
    "track": "finance_kp 或 macro_analysis",
    "headline": "立意标题，一句话",
    "why_fit": "为什么这个角度对该赛道受众有效，一句话",
    "window": "口播 或 深度 或 长青"
  }
]

只输出 JSON 数组本身，不要任何解释文字。"""


def _build_user_prompt(signal: dict) -> str:
    title = signal.get("title") or ""
    summary = signal.get("summary") or ""
    source = signal.get("source") or ""
    return (
        f"【信号】\n"
        f"来源：{source}\n"
        f"标题：{title}\n"
        f"摘要：{summary[:300] if summary else '（无）'}\n\n"
        f"【两个候选赛道画像】\n{all_profiles_brief()}\n\n"
        f"请按上述硬性要求生成 3-4 个候选角度。"
    )


def _validate_and_clean(raw: list, signal_id: str) -> list[dict]:
    """清洗 LLM 输出：track 兜底 + window 限定枚举 + headline 截断。"""
    valid_tracks = set(TRACK_PROFILES.keys())
    valid_windows = {"口播", "深度", "长青"}
    cleaned: list[dict] = []
    for i, a in enumerate(raw):
        if not isinstance(a, dict):
            continue
        track = a.get("track") or "finance_kp"
        if track not in valid_tracks:
            track = "finance_kp"
        window = a.get("window") or "深度"
        if window not in valid_windows:
            window = "深度"
        headline = (a.get("headline") or "").strip()[:50]
        if not headline:
            continue
        label = (a.get("label") or "未分类")[:10]
        why_fit = (a.get("why_fit") or "")[:100]
        cleaned.append({
            "id": f"{signal_id}_a{i+1}",
            "track": track,
            "label": label,
            "headline": headline,
            "why_fit": why_fit,
            "window": window,
            "supporting_refs": [],
        })
    return cleaned[:4]


def generate_angles(signal: dict) -> Optional[dict]:
    """根据信号生成候选角度。返回 {provider, cost, angles} 或 None。"""
    signal_id = signal.get("signal_id") or "unknown"
    user = _build_user_prompt(signal)
    result = llm_client.call_json_with_fallback(_SYSTEM_PROMPT, user, timeout=60)
    if result is None:
        return None
    provider, content_str, cost = result
    # LLM 可能在 JSON 外包了 markdown 代码块——剥掉
    s = content_str.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:].strip()
    try:
        raw = json.loads(s)
        # 兼容两种格式：直接数组 / 包在 {"angles": [...]} 里
        if isinstance(raw, dict):
            raw = raw.get("angles") or raw.get("data") or []
        if not isinstance(raw, list):
            return None
        angles = _validate_and_clean(raw, signal_id)
        if not angles:
            return None
        return {"provider": provider, "cost": cost, "angles": angles}
    except (json.JSONDecodeError, TypeError):
        return None
