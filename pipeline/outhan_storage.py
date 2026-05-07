"""Outhan 项目文件存储 helper（合并自 D:\\AIvideo\\src\\lib\\file-storage.ts）。

[AI-NOTE] 2026-05-07: Outhan 项目素材按 project_id 隔离存盘：
  data/projects/<projectId>/refs/      用户上传的参考图
  data/projects/<projectId>/frames/    首帧、尾帧
  data/projects/<projectId>/segments/  分段视频
  data/projects/<projectId>/audio/     分段音频
  data/projects/<projectId>/scripts/   配音稿

资产元信息存 meta_json["outhan_assets"]: list[{id, type, path, segment_index?, metadata?, created_at}]
为避免 schema 改动，复用 videos.meta_json 列；查询是 O(N) 但单项目不会很多资产，可接受。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from pipeline import state as _state


_DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "projects"


SUBDIR_BY_TYPE = {
    "reference": ("refs", ".png"),
    "first_frame": ("frames", ".png"),
    "tail_frame": ("frames", ".png"),
    "segment_video": ("segments", ".mp4"),
    "segment_audio": ("audio", ".mp3"),
    "script": ("scripts", ".md"),
}


def project_dir(project_id: str) -> Path:
    return _DATA_ROOT / project_id


def ensure_project_dirs(project_id: str) -> None:
    root = project_dir(project_id)
    for sub in ("refs", "frames", "segments", "audio", "scripts"):
        (root / sub).mkdir(parents=True, exist_ok=True)


def _read_meta(project_id: str) -> dict[str, Any]:
    rec = _state.get_video(project_id)
    if rec is None:
        return {}
    return dict(rec.meta) if rec.meta else {}


def _write_meta(project_id: str, meta: dict[str, Any]) -> None:
    """直接写 meta_json（不走 update_video_fields，因为它的 allowed 不含 meta_json）。"""
    with _state.get_conn() as conn:
        conn.execute(
            "UPDATE videos SET meta_json = ?, updated_at = ? WHERE video_id = ?",
            (json.dumps(meta, ensure_ascii=False), time.time(), project_id),
        )


def save_asset(
    project_id: str,
    asset_type: str,
    data: bytes,
    *,
    base_name: str | None = None,
    ext: str | None = None,
    segment_index: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """落盘一个资产 + 把元信息追加进 meta_json["outhan_assets"]。

    返回 {id, path, url}。
    """
    ensure_project_dirs(project_id)
    sub_default, ext_default = SUBDIR_BY_TYPE.get(asset_type, ("refs", ".bin"))
    final_ext = ext or ext_default
    # [AI-NOTE] 2026-05-08: 防御性 —— 即使将来谁手贱把用户输入当 base_name 传进来，
    # 也不能逃出 project_dir。Path(...).name 取最后一段，剥掉所有 / \ ../。
    raw_base = base_name or uuid.uuid4().hex
    base = Path(raw_base).name or uuid.uuid4().hex
    filename = f"{base}{final_ext}"
    full_path = project_dir(project_id) / sub_default / filename
    full_path.write_bytes(data)

    asset_id = uuid.uuid4().hex
    record = {
        "id": asset_id,
        "type": asset_type,
        "path": str(full_path),
        "segment_index": segment_index,
        "metadata": metadata or {},
        "created_at": time.time(),
    }

    meta = _read_meta(project_id)
    assets = meta.setdefault("outhan_assets", [])
    assets.append(record)
    _write_meta(project_id, meta)

    return {
        "id": asset_id,
        "path": str(full_path),
        "url": f"/api/outhan/{project_id}/asset/{asset_id}",
    }


def get_asset(project_id: str, asset_id: str) -> dict[str, Any] | None:
    meta = _read_meta(project_id)
    for a in meta.get("outhan_assets", []):
        if a.get("id") == asset_id:
            return a
    return None


def list_assets(project_id: str, asset_type: str | None = None) -> list[dict[str, Any]]:
    meta = _read_meta(project_id)
    items = meta.get("outhan_assets", [])
    if asset_type is None:
        return list(items)
    return [a for a in items if a.get("type") == asset_type]


def read_asset_bytes(asset: dict[str, Any]) -> bytes:
    return Path(asset["path"]).read_bytes()


def mime_for_asset_type(asset_type: str, path: str | None = None) -> str:
    """[AI-NOTE] 2026-05-08: 优先按存盘扩展名嗅探（用户上传 jpg/webp 时返回正确 mime），
    无 path 时回退到类型默认（保留旧调用兼容）。"""
    explicit = {
        "segment_video": "video/mp4",
        "segment_audio": "audio/mpeg",
        "script": "text/markdown",
    }
    if asset_type in explicit:
        return explicit[asset_type]
    if path:
        ext = Path(path).suffix.lower()
        return {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif",
        }.get(ext, "image/png")
    return "image/png"
