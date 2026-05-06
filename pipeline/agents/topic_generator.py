"""选题 Agent：根据用户输入的关键词/指令生成 N 个候选选题。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline import state
from pipeline.tools import llm_client

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "config" / "prompts"


def _read_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _fill(template: str, **kw: Any) -> str:
    out = template
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _ensure_video_row(video_id: str, title: str = "(选题中)") -> None:
    if state.get_video(video_id) is None:
        state.create_video(video_id, title=title)


def generate_fused_topics(
    video_id: str,
    keywords: str,
    extra: str = "",
    n_topics: int = 6,
) -> dict[str, Any]:
    """返回 {"topics": [...], "provider": str, "cost": float}"""
    _ensure_video_row(video_id)
    system = _fill(
        _read_prompt("topic_gen.txt"),
        keywords=keywords or "(未指定)",
        extra=extra or "(无)",
        n_topics=n_topics,
    )
    user = "请按上述要求生成选题列表。"
    task_id = state.start_task(video_id, agent="topic_generator", input_data={"keywords": keywords})

    result = llm_client.call_json_with_fallback(system, user)
    if result is None:
        state.finish_task(task_id, success=False, error="no LLM key configured", cost_cny=0.0)
        return {"topics": [], "provider": "none", "cost": 0.0,
                "error": "未配置 DeepSeek/Claude API key（请设置 config/.env 里的 DEEPSEEK_API_KEY）"}

    provider, content, cost = result
    try:
        parsed = json.loads(content)
        topics = parsed.get("topics") or []
        # 给 id 兜底（万一模型没返回完整 id）
        for i, t in enumerate(topics):
            if not t.get("id"):
                t["id"] = f"F{i+1:02d}"
            t.setdefault("origins", [{"sid": "src_kw", "label": f"关键词: {keywords[:20]}"}])
            t.setdefault("style_spectrum", {"serious": 70, "humor": 70, "healing": 60, "analyst": 70, "counter": 70})
            t.setdefault("best_style", "humor")
        assert topics
    except (json.JSONDecodeError, AssertionError, TypeError) as e:
        state.finish_task(task_id, success=False, error=f"parse error: {e}; content[:200]={content[:200]}", cost_cny=cost)
        return {"topics": [], "provider": provider, "cost": cost,
                "error": f"AI 返回格式异常: {e}"}

    state.finish_task(task_id, success=True, output={"n": len(topics)}, cost_cny=cost)
    with state.get_conn() as conn:
        conn.execute("UPDATE tasks SET provider = ? WHERE task_id = ?", (provider, task_id))
    return {"topics": topics, "provider": provider, "cost": cost}
