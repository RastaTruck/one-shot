"""完整立意生成 · 信号 + 方向 + 找补内容 → 完整立意文档。

工作流：
  Step 1 · recommend_directions(signal) → AI 看信号推 2-3 个最匹配方向
  Step 2 · find_supplements(signal, direction)  → 池内全文搜 + 联网 grounding
  Step 3 · generate_brief(signal, direction, supplements) → 完整立意（用户只审不填）

对比旧版 angle_generator：
- 旧：一次 LLM 调用，AI 自由生成 4 个角度（容易模板化 / 虎头蛇尾）
- 新：多步 LLM 调用，用户先选方向，AI 在已有范式上扩写 + 找补真实数据
"""
from __future__ import annotations

import json
import re
from typing import Optional

from pipeline.tools import llm_client

from .direction_library import DIRECTIONS, brief_for_prompt, by_key, by_track
from .storage import list_recent_signals


# ============== Step 1 · 方向推荐 ==============

_RECOMMEND_SYSTEM = """你是中文金融视频选题专家。

给定一条新闻信号 + 一个方向库（金融科普 + 经济分析），从中挑选 2-3 个最适合该信号的方向。

输出严格 JSON 数组，每项格式：
{
  "key": "方向 key（从给定列表里选）",
  "reason": "为什么这个方向适合（一句话，30 字内）"
}

只输出 JSON 数组，不要任何解释。"""


def recommend_directions(signal: dict, limit: int = 3) -> Optional[list[dict]]:
    """LLM 推 2-3 个最适合该信号的方向。返回 [{key, reason}, ...] 或 None。"""
    if not signal:
        return None

    directions_brief = "\n".join(f"- {brief_for_prompt(d)}" for d in DIRECTIONS)
    user = (
        f"【信号】\n"
        f"标题：{signal.get('title') or ''}\n"
        f"摘要：{(signal.get('summary') or '')[:300]}\n\n"
        f"【方向库 14 个】\n{directions_brief}\n\n"
        f"请挑 {limit} 个最适合的方向。注意：必须从给定的 14 个 key 里选，不要新建 key。"
    )
    result = llm_client.call_json_with_fallback(_RECOMMEND_SYSTEM, user, timeout=60)
    if result is None:
        return None
    _provider, content_str, _cost = result
    try:
        raw = _strip_codefence(content_str)
        data = json.loads(raw)
        if isinstance(data, dict):
            data = data.get("recommendations") or data.get("directions") or []
        if not isinstance(data, list):
            return None
        valid_keys = {d["key"] for d in DIRECTIONS}
        out: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if key not in valid_keys:
                continue
            out.append({"key": key, "reason": (item.get("reason") or "")[:60]})
            if len(out) >= limit:
                break
        return out
    except (json.JSONDecodeError, TypeError):
        return None


# ============== Step 2 · 内容找补（池内 + 联网 hook） ==============

# 简单中文分词：抽 2-4 字的有意义词组（用于在池内做关键词搜索）
_STOPWORDS = {"的", "是", "和", "在", "了", "也", "都", "与", "对", "或", "及", "等",
              "你", "我", "他", "她", "它", "我们", "他们", "什么", "怎么", "为什么"}


def _extract_keywords(title: str, summary: str) -> list[str]:
    """从标题/摘要里抽关键词。简单版：split 中英文段，留长度 ≥ 2 的非停词。"""
    text = f"{title} {summary or ''}"
    # 切分：保留连续中文/英文 token
    tokens = re.findall(r"[一-鿿]{2,8}|[A-Za-z]{3,}", text)
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t in _STOPWORDS or len(t) < 2:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out[:8]  # 上限 8 个关键词


def find_supplements_pool(signal: dict, top_k: int = 5) -> list[dict]:
    """池内全文搜：找信号池里相关信号作为找补."""
    keywords = _extract_keywords(signal.get("title", ""), signal.get("summary", ""))
    if not keywords:
        return []
    # 用 SQL LIKE 在 title + summary 里匹配——简单粗暴够 MVP
    from .schema import get_conn
    rows: list[dict] = []
    with get_conn() as conn:
        for kw in keywords:
            r = conn.execute(
                "SELECT signal_id, source, title, url, summary, published_at "
                "FROM hotspot_signals "
                "WHERE (title LIKE ? OR summary LIKE ?) AND signal_id != ? "
                "ORDER BY fetched_at DESC LIMIT 3",
                (f"%{kw}%", f"%{kw}%", signal.get("signal_id") or ""),
            ).fetchall()
            for row in r:
                d = dict(row)
                d["matched_kw"] = kw
                rows.append(d)
    # 去重 by signal_id
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        sid = r["signal_id"]
        if sid in seen:
            continue
        seen.add(sid)
        out.append(r)
        if len(out) >= top_k:
            break
    return out


def find_supplements_web(signal: dict, top_k: int = 3) -> list[dict]:
    """[STUB] 联网搜索找补。Phase B 才接 Gemini grounding。

    当前返回空数组 → 上层等于只用池内找补，不报错。
    """
    return []  # TODO Phase B: Gemini grounding / DeepSeek search


def find_supplements(signal: dict) -> dict:
    """混合找补 = 池内 + 联网。"""
    pool_refs = find_supplements_pool(signal, top_k=5)
    web_refs = find_supplements_web(signal, top_k=3)
    return {"pool_refs": pool_refs, "web_refs": web_refs}


# ============== Step 3 · 完整立意生成 ==============

_BRIEF_SYSTEM = """你是中文金融视频选题立意专家。

给定一条新闻信号 + 一个选定方向 + 找补资料，输出一份**完整可直接进入下游制作的立意文档**。

硬性要求：
1. **遵循方向的范式（pattern）** —— 立意结构对应方向的内容套路
2. **使用找补资料里的真实数据/事件** —— 引用要具体，不能虚指（如"近期某专家说"❌，"WSJ 2024-08 报道"✓）
3. **核心论点不超过 25 字**，必须能一句话讲清观点
4. **受众认知起点**写"他们已经知道什么 / 常见误区是什么"
5. **信息支撑**列 3-5 条带 ref（每条带 source / title 引用）
6. **关键数据/案例**至少 2 条（来自找补资料）
7. **情绪钩子**说明为什么受众会有共鸣（担心？好奇？反感？）
8. **不要复述原标题** —— 立意是独立内容，不是标题党改写

输出严格 JSON（不要 markdown 包裹）：
{
  "title": "立意标题，35 字内，有钩子但不标题党",
  "core_argument": "核心论点，一句话不超 25 字",
  "audience_baseline": "受众认知起点 / 误区，100 字内",
  "info_support": [
    {"point": "支撑要点 1", "ref_source": "来源", "ref_title": "引用的资料标题"},
    {"point": "支撑要点 2", "ref_source": "...", "ref_title": "..."}
  ],
  "key_data": [
    {"label": "数据点描述", "value": "具体数字/事实", "source": "出处"}
  ],
  "emotion_hook": "情绪钩子，50 字内"
}

只输出 JSON 对象本身。"""


def _strip_codefence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.strip("`").strip()
        if s.startswith("json"):
            s = s[4:].strip()
    return s


def _format_refs_for_prompt(supplements: dict) -> str:
    parts: list[str] = []
    pool = supplements.get("pool_refs") or []
    web = supplements.get("web_refs") or []
    if pool:
        parts.append("【池内相关信号（已抓取）】")
        for r in pool[:5]:
            parts.append(
                f"- [{r.get('source','')}] {r.get('title','')[:80]}"
                + (f"\n  摘要: {(r.get('summary') or '')[:120]}" if r.get('summary') else "")
            )
    if web:
        parts.append("\n【联网搜索的近期报道】")
        for r in web[:3]:
            parts.append(f"- [{r.get('source','')}] {r.get('title','')[:80]}")
    if not parts:
        return "（无找补资料，请基于信号本身合理推演）"
    return "\n".join(parts)


def generate_brief(signal: dict, direction_key: str, supplements: dict) -> Optional[dict]:
    """信号 + 方向 + 找补 → 完整立意文档。返回 {provider, cost, brief} 或 None。"""
    direction = by_key(direction_key)
    if not direction:
        return None

    refs_block = _format_refs_for_prompt(supplements)
    user = (
        f"【信号】\n"
        f"标题：{signal.get('title') or ''}\n"
        f"摘要：{(signal.get('summary') or '')[:400]}\n\n"
        f"【选定方向】\n{brief_for_prompt(direction)}\n\n"
        f"【找补资料】\n{refs_block}\n\n"
        f"按上面硬性要求输出 JSON 立意文档。"
    )
    result = llm_client.call_json_with_fallback(_BRIEF_SYSTEM, user, timeout=90)
    if result is None:
        return None
    provider, content_str, cost = result
    try:
        raw = _strip_codefence(content_str)
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        brief = {
            "title": str(data.get("title", "") or "").strip()[:60],
            "core_argument": str(data.get("core_argument", "") or "").strip()[:80],
            "audience_baseline": str(data.get("audience_baseline", "") or "").strip()[:300],
            "info_support": _clean_list_of_dicts(data.get("info_support"), 5,
                                                  keys=["point", "ref_source", "ref_title"]),
            "key_data": _clean_list_of_dicts(data.get("key_data"), 5,
                                              keys=["label", "value", "source"]),
            "emotion_hook": str(data.get("emotion_hook", "") or "").strip()[:150],
            "direction_key": direction_key,
            "direction_label": direction["label"],
            "track": direction["track"],
        }
        if not brief["title"] or not brief["core_argument"]:
            return None
        return {"provider": provider, "cost": cost, "brief": brief}
    except (json.JSONDecodeError, TypeError):
        return None


def _clean_list_of_dicts(raw, max_len: int, keys: list[str]) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        cleaned = {k: str(item.get(k, "") or "").strip()[:200] for k in keys}
        if any(cleaned.values()):
            out.append(cleaned)
        if len(out) >= max_len:
            break
    return out
