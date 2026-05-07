"""Outhan 项目专属字段操作（存在 videos.meta_json 里）。

[AI-NOTE] 2026-05-07: 不改 schema，所有 Outhan 特有字段塞 meta_json["outhan"] 子树。
schema 形态:
  meta_json = {
    "outhan": {
      "theme": str,                        # 主题（用户文本 OR 视频总结的回填）
      "style": str,                        # 风格描述
      "target_duration": int,              # 目标总时长（秒）
      "video_engine": "jimeng" | "vidu",   # 视频引擎默认
      "first_frame_asset_id": str | null,  # 当前选定的首帧 asset id
      "first_frame_prompt": str | null,    # 生成首帧时的 prompt
      "current_step": int,                 # 1-6 当前步骤
    },
    "outhan_assets": [...]  # 见 outhan_storage.py
  }
"""
from __future__ import annotations

import json
import time
from typing import Any

from pipeline import state as _state


DEFAULT_OUTHAN = {
    "theme": "",
    "style": "",
    "target_duration": 60,
    "video_engine": "jimeng",
    "first_frame_asset_id": None,
    "first_frame_prompt": None,
    "current_step": 1,
}


def _read_meta(project_id: str) -> dict[str, Any]:
    rec = _state.get_video(project_id)
    if rec is None:
        return {}
    return dict(rec.meta) if rec.meta else {}


def _write_meta(project_id: str, meta: dict[str, Any]) -> None:
    with _state.get_conn() as conn:
        conn.execute(
            "UPDATE videos SET meta_json = ?, updated_at = ? WHERE video_id = ?",
            (json.dumps(meta, ensure_ascii=False), time.time(), project_id),
        )


def get_outhan_data(project_id: str) -> dict[str, Any]:
    """读项目的 Outhan 子树；缺则补默认。不写库。"""
    meta = _read_meta(project_id)
    out = dict(DEFAULT_OUTHAN)
    out.update(meta.get("outhan", {}))
    return out


def update_outhan_data(project_id: str, **fields: Any) -> dict[str, Any]:
    """合并更新 outhan 子树，返回更新后的全量子树。"""
    meta = _read_meta(project_id)
    cur = dict(DEFAULT_OUTHAN)
    cur.update(meta.get("outhan", {}))
    for k, v in fields.items():
        if k in DEFAULT_OUTHAN:
            cur[k] = v
    meta["outhan"] = cur
    _write_meta(project_id, meta)
    return cur


def set_current_step(project_id: str, step: int) -> None:
    update_outhan_data(project_id, current_step=max(1, min(6, step)))


def step_path(project_id: str, step: int) -> str:
    """步骤号 → URL 路径段。"""
    names = {1: "topic", 2: "first-frame", 3: "script", 4: "video", 5: "audio", 6: "package"}
    return names.get(step, "topic")


# ============== segments ===================

def get_segments(project_id: str) -> list[dict[str, Any]]:
    meta = _read_meta(project_id)
    return list(meta.get("outhan_segments", []))


def replace_segments(project_id: str, segments: list[dict[str, Any]]) -> None:
    meta = _read_meta(project_id)
    meta["outhan_segments"] = segments
    _write_meta(project_id, meta)


def update_segment(project_id: str, index: int, **fields: Any) -> dict[str, Any] | None:
    meta = _read_meta(project_id)
    segs = meta.get("outhan_segments", [])
    target = None
    for s in segs:
        if s.get("index") == index:
            s.update(fields)
            target = s
            break
    if target is None:
        return None
    meta["outhan_segments"] = segs
    _write_meta(project_id, meta)
    return target


def reset_all_segments(project_id: str) -> None:
    """[AI-NOTE] 2026-05-07: 全部重来——清所有段的 video/audio/tail/task，
    状态回 pending；段 0 首帧恢复为项目首帧；其余段首帧清空。保留 prompt/scene/duration/engine。
    """
    meta = _read_meta(project_id)
    segs = meta.get("outhan_segments", [])
    project_first_frame = meta.get("outhan", {}).get("first_frame_asset_id")
    for s in segs:
        s["video_asset_id"] = None
        s["video_path"] = None
        s["audio_asset_id"] = None
        s["tail_frame_asset_id"] = None
        s["task_id"] = None
        s["status"] = "pending"
        if s.get("index") == 0:
            s["first_frame_asset_id"] = project_first_frame
        else:
            s["first_frame_asset_id"] = None
    meta["outhan_segments"] = segs
    _write_meta(project_id, meta)


def cascade_reset_after(project_id: str, index: int) -> None:
    """[AI-NOTE] 2026-05-07: 重生成段 N 时，N+1 起所有段的 video/audio/tail/task 清空，
    N+1 的 first_frame 也清——接力帧链一致性。"""
    meta = _read_meta(project_id)
    segs = meta.get("outhan_segments", [])
    for s in segs:
        if s.get("index", -1) > index:
            s["video_asset_id"] = None
            s["video_path"] = None
            s["audio_asset_id"] = None
            s["tail_frame_asset_id"] = None
            s["task_id"] = None
            s["status"] = "pending"
            if s.get("index") == index + 1:
                s["first_frame_asset_id"] = None
    meta["outhan_segments"] = segs
    _write_meta(project_id, meta)
