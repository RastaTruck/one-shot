"""立意 → 文案 转译 · 按制作模式产出文案 + units（含强制引用）。

vs brief_generator 的区别：
- brief_generator：产出立意结构（论点 / 信息支撑 / 关键数据）
- script_translator：基于立意结构产出**真实可口播的文案**——把立意里的 info_support 和 key_data
  显式写进文案（防止"看似有立意 实际文案空泛"）

设计要点：
- prompt 硬性要求至少引用 2 条 info_support + 1 条 key_data（防止虎头蛇尾）
- 输出含 references_used 字段，记录文案里哪一句引用了哪条 doc（让 UI 能可视化引用关系）
- 三种 mode：news（口播 60s）/ yierbubu（IP 分镜稿）/ outhan（分段台本）
  · MVP 只实现 news，另两条 mode 留 stub
"""
from __future__ import annotations

import json
from typing import Optional

from pipeline.agents.news_script_segmenter import segment_script
from pipeline.tools import llm_client


_NEWS_SYSTEM = """你是中文口播视频文案专家。

基于一份完整的视频立意 + 找补资料，写一段 {duration_sec} 秒的口播文案。

【硬性要求】（违反任一条 = 不合格）：

1. **必须把立意里的"信息支撑"和"关键数据"明引进文案**：
   - 信息支撑列表里至少引用 2 条（在文案里以"根据 WSJ 报道..." / "鲍威尔说..." 这种引文形式出现）
   - 关键数据/案例至少引用 1 条（要带具体数字 / 原话 / 出处）
   - **禁止泛泛而谈** —— 每个论点都要有数据/事实/案例支撑

2. **结构**：
   - 开篇钩子（约 10 秒 / 50 字）：1 句话拉住人，常用反差 / 反常识 / 提问
   - 主体（约 30-40 秒 / 180 字）：2-3 个 info_support 引用 + 1-2 个 key_data 明引
   - 收尾（约 10 秒 / 50 字）：画龙点睛 + 引发思考

3. **句长 / 节奏**：
   - 短句为主，**每个句子 6-14 字**（这决定 TTS 自然停顿点）
   - 每个标点（。！？，：）= 一个字幕单元的边界
   - 口语化，避免书面腔（不要"综上所述" "因此" "由此可见"）

4. **风格**：{style_hint}

【输出格式】严格 JSON，不要 markdown 包裹：

{{
  "title": "视频标题，15-25 字，钩子型，不复述立意标题",
  "script": "完整口播文案，按句子用换行分隔，每行一句 6-14 字",
  "references_used": [
    {{"type": "info_support", "index": 0, "used_in_line": "文案里实际写的那一句"}},
    {{"type": "key_data",     "index": 0, "used_in_line": "文案里实际写的那一句"}}
  ]
}}

只输出 JSON 对象本身。"""


def _build_news_user_prompt(brief: dict, supplements: dict, signal: dict) -> str:
    parts: list[str] = [
        f"【立意标题】{brief.get('title', '')}",
        f"【核心论点】{brief.get('core_argument', '')}",
        f"【受众认知起点】{brief.get('audience_baseline', '')}",
        f"【情绪钩子】{brief.get('emotion_hook', '')}",
        "",
        "【信息支撑】（必须引用至少 2 条）：",
    ]
    for i, s in enumerate(brief.get("info_support", []) or []):
        ref = f"{s.get('ref_source', '')}"
        if s.get("ref_title"):
            ref += f" · 《{s['ref_title']}》"
        parts.append(f"  [info_support#{i}] {s.get('point', '')}")
        if ref.strip():
            parts.append(f"             来源：{ref}")

    parts.append("")
    parts.append("【关键数据 / 案例】（必须引用至少 1 条，带具体数字 / 原话 / 出处）：")
    for i, k in enumerate(brief.get("key_data", []) or []):
        parts.append(f"  [key_data#{i}] {k.get('label', '')}: {k.get('value', '')}")
        if k.get("source"):
            parts.append(f"             出处：{k['source']}")

    if signal and signal.get("title"):
        parts.append("")
        parts.append(f"【源信号】[{signal.get('source', '')}] {signal.get('title', '')}")

    pool = (supplements or {}).get("pool_refs") or []
    if pool:
        parts.append("")
        parts.append("【背景知识 · 找补的相关报道】（可选作背景，不强制引用）：")
        for i, r in enumerate(pool[:5]):
            parts.append(f"  [supplement#{i}] [{r.get('source', '')}] {r.get('title', '')}")
            if r.get("summary"):
                s = r.get("summary", "")[:120]
                parts.append(f"               摘要：{s}")

    parts.append("")
    parts.append("按上面的硬性要求和输出格式，写出完整口播文案。")
    return "\n".join(parts)


def _strip_codefence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.strip("`").strip()
        if s.startswith("json"):
            s = s[4:].strip()
    return s


def translate_news(
    brief: dict,
    supplements: dict,
    signal: dict,
    *,
    duration_sec: int = 60,
    style_hint: str = "口语化、有梗、节奏快、像 ZooNews 财经口播",
) -> Optional[dict]:
    """立意 → news 模式口播文案 + units。

    返回 {ok, title, script, units, references_used, provider, cost} 或 None。
    """
    system = _NEWS_SYSTEM.format(duration_sec=duration_sec, style_hint=style_hint)
    user = _build_news_user_prompt(brief, supplements or {}, signal or {})
    result = llm_client.call_json_with_fallback(system, user, timeout=120)
    if result is None:
        return None
    provider, content_str, cost = result
    try:
        data = json.loads(_strip_codefence(content_str))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    title = str(data.get("title", "") or "").strip()[:60]
    script = str(data.get("script", "") or "").strip()
    if not script:
        return None
    refs_used = data.get("references_used") or []
    if not isinstance(refs_used, list):
        refs_used = []
    cleaned_refs = []
    for r in refs_used[:10]:
        if not isinstance(r, dict):
            continue
        cleaned_refs.append({
            "type": str(r.get("type", "") or "")[:40],
            "index": r.get("index"),
            "used_in_line": str(r.get("used_in_line", "") or "")[:200],
        })
    units = segment_script(script)
    return {
        "ok": True,
        "title": title,
        "script": script,
        "units": units,
        "references_used": cleaned_refs,
        "provider": provider,
        "cost": cost,
    }


def translate_yierbubu(brief: dict, supplements: dict, signal: dict, **kwargs) -> Optional[dict]:
    """立意 → 一二布布 IP 分镜稿。MVP 暂同 news 风格，后续按 IP 模板细化。"""
    return translate_news(brief, supplements, signal,
                          duration_sec=kwargs.get("duration_sec", 90),
                          style_hint="一二布布 IP 风格 · 金融科普 · 卡通可爱、有梗、不卖课")


def translate_outhan(brief: dict, supplements: dict, signal: dict, **kwargs) -> Optional[dict]:
    """立意 → Outhan 通用单条视频台本。MVP 暂同 news 风格。"""
    return translate_news(brief, supplements, signal,
                          duration_sec=kwargs.get("duration_sec", 60),
                          style_hint="Outhan 通用风格 · 单条创意视频 · 有钩子有节奏")


def translate(brief: dict, supplements: dict, signal: dict, mode: str, **kwargs) -> Optional[dict]:
    """按 mode 分发到对应转译函数。"""
    if mode == "news":
        return translate_news(brief, supplements, signal, **kwargs)
    if mode == "yierbubu":
        return translate_yierbubu(brief, supplements, signal, **kwargs)
    if mode == "outhan":
        return translate_outhan(brief, supplements, signal, **kwargs)
    return None
