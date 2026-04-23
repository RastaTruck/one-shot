"""公共依赖: 模板渲染器、当前项目查询等。"""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from pipeline import state


TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------- 步骤元数据（侧边栏 + 流水线渲染用） ----------

STEPS = [
    {"key": "topic", "label": "① 选题", "icon": "lightbulb"},
    {"key": "script", "label": "② 文案", "icon": "file-text"},
    {"key": "shots", "label": "③ 分镜", "icon": "layout-grid"},
    {"key": "assets", "label": "④ 素材", "icon": "image"},
    {"key": "audio", "label": "⑤ 配音", "icon": "mic"},
    {"key": "edit", "label": "⑥ 剪辑", "icon": "film"},
]

# 状态 → 流水线进度（已完成到哪一步）
STATUS_TO_STEP_INDEX = {
    "draft": -1,
    "topic_locked": 0,
    "script_drafting": 0,
    "script_pending_review": 1,
    "script_locked": 1,
    "shot_table_generating": 2,
    "shot_table_locked": 2,
    "assets_producing": 3,
    "assets_ready": 3,
    "subtitle_done": 4,
    "editing": 5,
    "cut_pending_review": 5,
    "cut_approved": 5,
    "packaging": 5,
    "ready_to_publish": 5,
}


def get_video_or_404(video_id: str) -> state.VideoRecord:
    v = state.get_video(video_id)
    if not v:
        raise HTTPException(status_code=404, detail=f"视频项目不存在: {video_id}")
    return v


def render(request: Request, template: str, **ctx):
    """渲染模板，自动注入通用上下文。"""
    base_ctx = {
        "request": request,
        "steps": STEPS,
    }
    base_ctx.update(ctx)
    return templates.TemplateResponse(template, base_ctx)


def get_step_index(status: str) -> int:
    return STATUS_TO_STEP_INDEX.get(status, -1)
