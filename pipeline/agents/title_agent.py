"""标题生成 Agent · 给画风页"AI 自动 P 标题"模式提供短标题候选。

用途：
- 画风页 Block 4 第二步：用户开启"AI 自动 P 标题"后，调本 agent 拿一组候选短标题，
  逐个/随机 P 到选中的框架背景上做合成预览，决定整期视频的标题视觉。
- 后续视频实际渲染时，每段分镜按 narration 调本 agent 单段生成标题（todo: 一段一题）。

输入：video_id（从 sqlite 拿 topic + script_full）
输出：{titles: [str], provider, cost} —— 每个 title 4-8 字，钩子型/反差型/悬念型
"""
from __future__ import annotations

import json
from typing import Any

from pipeline import state as _state
from pipeline.tools import llm_client


_SYSTEM_PROMPT = """你是中国短视频领域的封面标题创作者，专门为竖屏短视频（抖音/小红书/B站）生成"标题图上的短标题文字"。

## 你的职责
看一段视频的主题和文案，给出 N 个**适合 P 在视频开头标题图上的 4-8 字短标题**。

## 标题要求
1. **字数硬约束：4-8 字**。少于 4 字不够分量，多于 8 字 P 在画面里就糊。
2. **必须有钩子**：悬念 / 反差 / 数字冲击 / 利益相关 / 情绪词。不要平铺"什么是 X"。
3. **接地气**：用网感词汇（"血亏"/"真相"/"翻车"/"避坑"/"反转"），不要书面语。
4. **避免标点**：除非"？"或"！"用作钩子，否则不带标点。
5. **不要主题党**：标题必须和文案内容相关，不能哗众取宠到货不对板。

## 输出格式（严格 json，不要 markdown 包裹）
{
  "titles": ["标题1", "标题2", "标题3", "标题4", "标题5"]
}

注意 titles 数组长度等于 N（用户传进来）。直接返回 json 对象。
"""


def _build_user_prompt(topic_title: str, topic_summary: str, script_text: str, n: int) -> str:
    return f"""## 视频主题
{topic_title}

## 主题摘要
{topic_summary or "(无)"}

## 文案前 600 字
{script_text[:600]}

## 任务
按上述系统要求，生成 {n} 个 4-8 字的钩子型短标题。
"""


def _gather_script_text(video_id: str) -> str:
    full = _state.load_script_full(video_id)
    if not full:
        return ""
    paragraphs = (full.get("full_json") or {}).get("paragraphs") or full.get("paragraphs") or []
    if isinstance(paragraphs, list):
        return "\n".join(str((p or {}).get("text") or p) for p in paragraphs if p)
    return str(paragraphs)


def generate_titles(video_id: str, n: int = 5) -> dict[str, Any]:
    """生成 N 个候选短标题。返回 {titles, provider, cost} 或 {error}。"""
    topic = _state.load_project_topic(video_id) or {}
    topic_title = topic.get("title") or ""
    topic_summary = topic.get("summary") or topic.get("hook") or ""

    if not topic_title:
        rec = _state.get_video(video_id)
        if rec:
            topic_title = rec.title or ""

    script_text = _gather_script_text(video_id)
    if not topic_title and not script_text:
        return {"error": "项目还没有主题/文案，无法生成标题", "titles": []}

    user = _build_user_prompt(topic_title, topic_summary, script_text, n)
    result = llm_client.call_json_with_fallback(_SYSTEM_PROMPT.replace("N", str(n)), user, timeout=60)
    if result is None:
        return {"error": "LLM 不可用（无密钥或网络错误）", "titles": []}

    provider, content_str, cost = result
    try:
        data = json.loads(content_str)
        titles = data.get("titles") or []
        # 严格清洗：限定 4-8 字 + 去重
        cleaned: list[str] = []
        seen: set[str] = set()
        for t in titles:
            t = (str(t or "")).strip()
            if not t or t in seen:
                continue
            if not (4 <= len(t) <= 8):
                continue
            seen.add(t)
            cleaned.append(t)
        if not cleaned:
            # LLM 没给出符合长度的，截断/填充兜底
            cleaned = [str(t).strip()[:8] for t in titles if t][:n]
        return {"titles": cleaned[:n], "provider": provider, "cost": cost}
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        return {"error": f"LLM 输出解析失败：{e}", "titles": [], "raw": content_str[:200]}
