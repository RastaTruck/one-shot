"""画风步骤的 API 端点（独立路由，避开 app.py 巨石）。

当前路由：
- GET  /api/style/bg/list                   — 列出所有背景（含 kind=frame/full）
- POST /api/style/bg/preview-title          — 用指定短标题合成（debug/手动覆盖用）
- POST /api/style/bg/auto-render            — LLM 按文案生候选标题 + 合成第一个，返回 url + titles 数组

后续：视频渲染流接 auto-title 让每段独立 LLM 标题。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pipeline.agents.title_agent import generate_titles
from pipeline.asset_library import AssetLibrary
from PIL import Image

from pipeline.tools.title_overlay import render_titled_frame, resolve_title_bbox


router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parents[2]
_PREVIEW_DIR = _BASE_DIR / "data" / "cache" / "title_preview"
_LIB = AssetLibrary()


def _absolute_asset_url(file: str) -> str:
    if file.startswith(("http://", "https://", "/")):
        return file
    return "/" + file


@router.get("/api/style/bg/list")
async def list_backgrounds():
    """返回所有 backgrounds（合并 frame + full）给画风页 BG picker 用。"""
    _LIB.load()  # 每次刷新，便于热加修改
    items = []
    for asset in _LIB.all("backgrounds"):
        meta = asset.meta or {}
        kind = meta.get("kind") or "full"  # 老数据无 kind 视作 full
        is_remote = asset.file.startswith(("http://", "https://"))
        file_exists = True if is_remote else (_BASE_DIR / asset.file).exists()
        items.append({
            "id": asset.id,
            "url": _absolute_asset_url(asset.file),
            "description": asset.description,
            "tags": asset.tags,
            "kind": kind,                                       # frame | full
            "title_bbox": meta.get("title_bbox"),                # frame 才有（老规范）
            "spec_version": meta.get("spec_version"),            # 'plate-v1' = 走标准化 bbox
            "text_color": meta.get("text_color", "#222"),
            "stroke_color": meta.get("stroke_color", "#FFFFFF"),
            "file_exists": file_exists,
            "is_remote": is_remote,
        })
    # frame 排前面，user 优先看到能 P 文字的
    items.sort(key=lambda x: (0 if x["kind"] == "frame" else 1, x["id"]))
    return {"backgrounds": items}


class PreviewTitleReq(BaseModel):
    frame_id: str
    text: str


def _render_title_into_frame(frame_id: str, text: str) -> str:
    """共享渲染入口：拿 frame_id + 文字 → 渲染（带缓存）→ 返回相对 URL（含前导 /）。"""
    _LIB.load()
    asset = _LIB.get(frame_id)
    if asset is None or asset.kind != "backgrounds":
        raise HTTPException(404, f"未找到背景资产：{frame_id}")
    meta = asset.meta or {}
    if meta.get("kind") != "frame":
        raise HTTPException(400, f"{frame_id} 不是 kind=frame 的资产")
    frame_path = _BASE_DIR / asset.file
    if not frame_path.exists():
        raise HTTPException(404, f"框架文件不存在：{asset.file}")

    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "标题文字为空")

    try:
        with Image.open(frame_path) as im:
            image_size = im.size
        bbox = resolve_title_bbox(image_size, meta)
    except ValueError as e:
        raise HTTPException(400, f"{frame_id} {e}")

    scheme = meta.get("color_tone", "warm")  # warm | cool → 匹配背景色温
    spec_tag = meta.get("spec_version") or "legacy"
    key_src = f"{frame_id}|{spec_tag}|{scheme}|{text}|{bbox}"
    h = hashlib.md5(key_src.encode("utf-8")).hexdigest()[:12]
    out_path = _PREVIEW_DIR / f"{frame_id}_{h}.png"
    if not out_path.exists():
        try:
            render_titled_frame(
                frame_path=frame_path,
                text=text,
                title_bbox=bbox,
                output_path=out_path,
                scheme=scheme,
            )
        except RuntimeError as e:
            raise HTTPException(500, str(e))
    return "/" + out_path.relative_to(_BASE_DIR).as_posix()


@router.post("/api/style/bg/preview-title")
async def preview_title(req: PreviewTitleReq):
    """用户传入指定标题文字进行合成（debug 用，UI 主流程已不暴露）。"""
    url = _render_title_into_frame(req.frame_id, req.text)
    return JSONResponse({"url": url, "frame_id": req.frame_id, "text": req.text.strip()})


class AutoRenderReq(BaseModel):
    video_id: str
    frame_id: str
    n: int = 5  # 候选数
    pick_index: int = 0  # 取候选第几个做合成


@router.post("/api/style/bg/auto-render")
async def auto_render(req: AutoRenderReq):
    """LLM 按项目文案生 N 个候选短标题，渲染第 pick_index 个到选中 frame。

    返回：
      titles: [str]                — LLM 给的全部候选（user 可"换一个")
      picked: str                  — 当前合成用的标题
      url:    str                  — 合成图相对 URL
      provider, cost               — LLM 调用元数据
      error:  str (optional)       — LLM 失败时的兜底提示
    """
    gen = generate_titles(req.video_id, n=max(1, min(8, req.n)))
    titles = gen.get("titles") or []
    if not titles:
        raise HTTPException(
            400,
            gen.get("error") or "LLM 没给出可用标题（项目可能还没有文案/主题）",
        )
    idx = max(0, min(req.pick_index, len(titles) - 1))
    picked = titles[idx]
    url = _render_title_into_frame(req.frame_id, picked)
    return JSONResponse({
        "url": url,
        "frame_id": req.frame_id,
        "titles": titles,
        "picked": picked,
        "pick_index": idx,
        "provider": gen.get("provider"),
        "cost": gen.get("cost"),
    })
