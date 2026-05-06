"""灵感库 · 选题入口的反面（不靠空白脑子，靠已积累的灵感）。

两个路由：
- GET  /inspiration                 — 灵感库管理 + 选择页面
- POST /api/inspiration/create-project — 基于选中的灵感创建新项目（预填 sources.notes）

数据来源：snippet_library 表（user 在文案页右侧"灵感库"面板中收藏的片段）。
本模块**只读 + 创建**，不重新设计 schema。

为什么独立成文件而非塞进 app.py：
  app.py 已 2300+ 行。这是把 routes 按域拆分的起点（后续 topic/script/style/shots/assets/edit
  也应迁出），逐步消除巨石。本模块自包含——只引 pipeline.state 和 fastapi。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from pipeline import state as _state


router = APIRouter()

# 模板目录与 app.py 共享（同一 Jinja 环境）
_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


# ---------- 灵感分类元数据 ----------

KIND_META: dict[str, dict[str, Any]] = {
    "hook":   {"label": "钩子",     "color": "rose",   "icon": "anchor"},
    "case":   {"label": "案例段落", "color": "blue",   "icon": "book-open"},
    "golden": {"label": "金句",     "color": "amber",  "icon": "quote"},
    "cta":    {"label": "CTA",      "color": "violet", "icon": "megaphone"},
}
DEFAULT_KIND_ORDER = ["hook", "case", "golden", "cta"]


def _next_video_id() -> str:
    """读现有最大 V00xx，+1。和 app.py 的 _next_video_id 行为一致——
    本模块不 import app.py 避免循环依赖，复制这点最小逻辑代价低于强行抽象。"""
    videos = _state.list_videos()
    max_n = 0
    for v in videos:
        vid = v.video_id or ""
        if vid.startswith("V") and vid[1:].isdigit():
            max_n = max(max_n, int(vid[1:]))
    return f"V{max_n + 1:04d}"


def _format_inspiration_notes(snippets: list[dict[str, Any]]) -> str:
    """把选中的灵感拼成可读的 notes 文本，按 kind 分组。"""
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for s in snippets:
        by_kind.setdefault(s["kind"], []).append(s)
    lines: list[str] = []
    for kind in DEFAULT_KIND_ORDER:
        items = by_kind.get(kind) or []
        if not items:
            continue
        label = KIND_META.get(kind, {}).get("label", kind)
        lines.append(f"【{label}】")
        for s in items:
            lines.append(f"- {s['text']}")
        lines.append("")
    return "\n".join(lines).strip()


# ---------- 路由 ----------

@router.get("/inspiration", response_class=HTMLResponse)
async def page_inspiration(request: Request):
    """列出 snippet_library 全部条目，按 kind 分组渲染。"""
    # 跨项目读：state 没有 list_all_snippets，用 list_videos 遍历汇总
    all_snippets: list[dict[str, Any]] = []
    for v in _state.list_videos():
        for s in _state.list_snippets(v.video_id):
            s = dict(s)
            s["video_title"] = v.title
            all_snippets.append(s)
    # 按 kind 分组（保持 DEFAULT_KIND_ORDER）
    grouped: list[dict[str, Any]] = []
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for s in all_snippets:
        by_kind.setdefault(s["kind"], []).append(s)
    for kind in DEFAULT_KIND_ORDER:
        items = by_kind.get(kind) or []
        if not items:
            continue
        meta = KIND_META.get(kind, {"label": kind, "color": "ink", "icon": "tag"})
        grouped.append({"kind": kind, "label": meta["label"], "color": meta["color"],
                        "icon": meta["icon"], "items": items})
    # 其余 kind（schema 自由）
    for kind, items in by_kind.items():
        if kind in DEFAULT_KIND_ORDER:
            continue
        grouped.append({"kind": kind, "label": kind, "color": "ink", "icon": "tag", "items": items})

    return _TEMPLATES.TemplateResponse("inspiration.html", {
        "request": request,
        "active_nav": "inspiration",
        "grouped": grouped,
        "total": len(all_snippets),
        "kind_meta": KIND_META,
        "current_video": None,
        "steps_view": [],
        "progress": None,
    })


@router.post("/api/inspiration/create-project")
async def api_create_project_from_inspiration(payload: dict[str, Any]) -> JSONResponse:
    """基于选中的灵感片段创建新项目，把灵感拼接到 sources.notes 里。

    Request: {"snippet_ids": [int], "title": str?}
    Response: {"video_id": str, "notes_preview": str}
    """
    snippet_ids = payload.get("snippet_ids") or []
    if not isinstance(snippet_ids, list) or not snippet_ids:
        raise HTTPException(400, "snippet_ids required (non-empty list)")

    # 收集选中的灵感（across all videos）
    chosen: list[dict[str, Any]] = []
    seen_ids = set()
    for v in _state.list_videos():
        for s in _state.list_snippets(v.video_id):
            sid = s.get("snippet_id")
            if sid in snippet_ids and sid not in seen_ids:
                chosen.append(dict(s))
                seen_ids.add(sid)
    if not chosen:
        raise HTTPException(404, "选中的灵感片段都不存在")

    # 创建新 video（沿用现有 state.create_video，title 默认从第一条灵感来）
    video_id = _next_video_id()
    title = (payload.get("title") or "").strip()
    if not title:
        first_text = chosen[0].get("text") or ""
        title = first_text[:24] if first_text else f"未命名项目 {video_id}"
    _state.create_video(video_id=video_id, title=title)

    # 把选中的灵感拼接成 notes，写入 sources
    notes = _format_inspiration_notes(chosen)
    _state.save_project_sources(video_id, {
        "keywords": "",
        "notes": notes,
        "extra": "",
        "references": [{"type": "inspiration", "snippet_id": s["snippet_id"],
                        "from_video": s["video_id"]} for s in chosen],
    })

    return JSONResponse({
        "ok": True,
        "video_id": video_id,
        "title": title,
        "notes_preview": notes[:200],
        "snippet_count": len(chosen),
    })
