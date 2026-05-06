"""发布信息 Agent · 给已剪辑成片生成"标题 / 简介 / 话题标签"备选。

设计意图：
- user 在剪辑页要把视频发到抖音/B 站/小红书，需要现成的复制粘贴文案
- 一次生成 N 套备选（避免每次"换一个"都调 LLM，N=3 经验值）
- 备选池缓存进 ShotTable.publish_meta_pool，客户端循环切换
- 池用尽 → user 点"重新生成"再调一次 LLM
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline import shot_table as _st
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


def _build_script_summary(table: _st.ShotTable, max_chars: int = 600) -> str:
    """把分镜表里所有 shot.text 拼成内容概要，截断到 max_chars 给 LLM 看上下文。"""
    parts = []
    total = 0
    for sh in table.shots:
        line = (sh.text or "").strip()
        if not line:
            continue
        parts.append(line)
        total += len(line)
        if total > max_chars:
            break
    return "\n".join(parts)


def _build_outline(table: _st.ShotTable) -> str:
    """每段 section + 简短一句话作为大纲。"""
    parts = []
    for sh in table.shots[:8]:  # 前 8 段够 outline
        section = sh.section or "段落"
        text = (sh.text or "").strip()[:30]
        parts.append(f"[{section}] {text}")
    return "\n".join(parts)


def generate_meta_batch(
    video_id: str,
    table: _st.ShotTable,
    n: int = 3,
) -> dict[str, Any]:
    """调 LLM 生成 N 套发布信息备选。
    返回 {metas: [{title, description, tags}], provider, cost} 或 {error}。
    """
    if not table.shots:
        return {"error": "shot_table 为空", "metas": []}

    system = _fill(
        _read_prompt("publish_meta.txt"),
        topic_title=table.title or table.topic or "(未知)",
        content_outline=_build_outline(table),
        duration_sec=int(table.total_duration_estimated or 0),
        script_summary=_build_script_summary(table),
        n=n,
        N=n,  # 模板里大写小写都用了
    )
    user = "请按上述要求生成 N 套发布信息备选。"

    task_id = state.start_task(
        video_id, agent="publish_meta",
        input_data={"n": n, "title": table.title},
    )
    result = llm_client.call_json_with_fallback(system, user)
    if result is None:
        state.finish_task(task_id, success=False, error="no LLM key configured", cost_cny=0.0)
        return {"error": "未配置 LLM key", "metas": []}

    provider, content, cost = result
    try:
        parsed = json.loads(content)
        metas = parsed.get("metas") or []
        # 字段兜底
        cleaned = []
        for m in metas:
            cleaned.append({
                "title": (m.get("title") or "").strip(),
                "description": (m.get("description") or "").strip(),
                "tags": [t.strip() for t in (m.get("tags") or []) if t and t.strip()],
            })
        if not cleaned:
            raise ValueError("metas 为空")
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        state.finish_task(task_id, success=False, error=f"parse: {e}; content[:200]={content[:200]}", cost_cny=cost)
        return {"error": f"AI 返回格式异常: {e}", "metas": [], "provider": provider, "cost": cost}

    state.finish_task(task_id, success=True, output={"n": len(cleaned)}, cost_cny=cost)
    with state.get_conn() as conn:
        conn.execute("UPDATE tasks SET provider = ? WHERE task_id = ?", (provider, task_id))
    return {"metas": cleaned, "provider": provider, "cost": cost}
