"""镜头模板库 · 口播管线的"主播视频集"。

一个"系列" = 一个口播角色 / IP，挂多个机位 take（短视频片段）
后续在视频合成阶段，会按配音时长循环+随机切换这些 take 拼成长视频。

v0 设计：
- 索引文件 config/shot_templates.json
- 视频文件 data/shot_templates/{series_id}/{take_id}.mp4
- 上传/删除靠后端接口（v1）

读取是 O(N) 但单库不会大（每个系列<10 个 take）。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_INDEX_PATH = _ROOT / "config" / "shot_templates.json"
_DATA_DIR = _ROOT / "data" / "shot_templates"


def _load_index() -> dict[str, Any]:
    if not _INDEX_PATH.exists():
        return {"version": 1, "series": []}
    try:
        return json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "series": [], "error": "索引解析失败"}


def _save_index(data: dict[str, Any]) -> None:
    _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    _INDEX_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_series() -> list[dict[str, Any]]:
    """返回所有系列（含 take 列表 + url）。"""
    idx = _load_index()
    out: list[dict[str, Any]] = []
    for s in idx.get("series", []):
        s2 = dict(s)
        s2["takes"] = [_with_url(t) for t in s.get("takes", [])]
        out.append(s2)
    return out


def get_series(series_id: str) -> dict[str, Any] | None:
    for s in list_series():
        if s["id"] == series_id:
            return s
    return None


def get_take_paths(series_id: str) -> list[Path]:
    """给视频合成器用 · 返回该系列下所有 take 的绝对文件路径（按定义顺序）。"""
    s = _load_index()
    for series in s.get("series", []):
        if series["id"] == series_id:
            return [_ROOT / t["path"] for t in series.get("takes", [])]
    return []


def get_take_framings(series_id: str) -> list[str]:
    """跟 get_take_paths 顺序一致，返回每个 take 的 framing 标签。
    用来给合成器做"视角间轮换 + 视角内防重复"切镜。
    """
    s = _load_index()
    for series in s.get("series", []):
        if series["id"] == series_id:
            return [str(t.get("framing", "默认")) for t in series.get("takes", [])]
    return []


def get_takes_meta(series_id: str) -> list[dict]:
    """跟 get_take_paths 顺序一致，返回每个 take 的完整元数据
    （含 motion_curve / duration_sec / samples_per_sec）用来给嘴动匹配。
    """
    s = _load_index()
    for series in s.get("series", []):
        if series["id"] == series_id:
            return [dict(t) for t in series.get("takes", [])]
    return []


def _with_url(take: dict[str, Any]) -> dict[str, Any]:
    """把 take 的相对 path 转成可访问的 URL。"""
    out = dict(take)
    rel = out.get("path", "")
    if rel:
        # data/shot_templates/funny_zoo/take_a.mp4 → /data/shot_templates/funny_zoo/take_a.mp4
        out["url"] = "/" + rel
    return out


# === v1 上传接口（占位，先留口径） ===

def add_series(series_id: str, name: str, **fields: Any) -> dict[str, Any]:
    idx = _load_index()
    series = idx.setdefault("series", [])
    if any(s["id"] == series_id for s in series):
        raise ValueError(f"系列 id 重复: {series_id}")
    new = {
        "id": series_id,
        "name": name,
        "emoji": fields.get("emoji", "🎬"),
        "category": fields.get("category", "single"),
        "description": fields.get("description", ""),
        "takes": [],
    }
    series.append(new)
    _save_index(idx)
    (_DATA_DIR / series_id).mkdir(parents=True, exist_ok=True)
    return new


def add_take(series_id: str, take_id: str, src_path: Path, framing: str = "未标记") -> dict[str, Any]:
    """把一个上传的 mp4 文件接入指定系列。复制到目标目录、写索引。"""
    idx = _load_index()
    target_series: dict[str, Any] | None = None
    for s in idx.get("series", []):
        if s["id"] == series_id:
            target_series = s
            break
    if target_series is None:
        raise ValueError(f"系列不存在: {series_id}")
    dst_dir = _DATA_DIR / series_id
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{take_id}.mp4"
    shutil.copy(src_path, dst)
    # 简单 probe 时长（避免循环依赖，懒导入）
    try:
        from pipeline.tools import ffmpeg_client
        dur = ffmpeg_client.probe_duration(str(dst)) or 0.0
    except Exception:
        dur = 0.0
    take = {
        "id": take_id,
        "label": f"机位 {take_id}",
        "framing": framing,
        "path": f"data/shot_templates/{series_id}/{take_id}.mp4",
        "duration_sec": round(dur, 2),
    }
    target_series.setdefault("takes", []).append(take)
    _save_index(idx)
    return take
