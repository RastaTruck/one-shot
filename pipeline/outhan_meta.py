"""Outhan 项目专属字段操作（存在 videos.meta_json 里）。

[AI-NOTE] 2026-05-07: 不改 schema，所有 Outhan 特有字段塞 meta_json["outhan"] 子树。
[AI-NOTE] 2026-05-08: 步骤 1 合并选题+首帧后，原"风格描述"字段（style）已废弃——主题文本
应自带风格表达。此字段在 DEFAULT_OUTHAN 仅保留为向后兼容（老 meta_json 读出来空字符串）。
schema 形态:
  meta_json = {
    "outhan": {
      "theme": str,                        # 主题（用户文本 OR 视频总结的回填，含风格描述）
      "target_duration": int,              # 目标总时长（秒）
      "video_engine": "jimeng" | "vidu",   # 视频引擎默认
      "video_kind": "dialogue" | "silent", # 有对白/旁白 vs 纯画面
                                           #   dialogue → 台本同时出 prompt + script，配音步骤必走
                                           #   silent   → 台本只出 prompt，配音步骤可跳过，装配时不替换音轨
      "voice_id": str,                     # 项目默认 Fish Audio reference_id；空 → Fish S1 默认
      "first_frame_asset_id": str | null,  # 当前选定的首帧 asset id
      "first_frame_prompt": str | null,    # 生成首帧时的 prompt
      "final_video_asset_id": str | null,  # 步骤 5 装配后的成片 asset id
      "current_step": int,                 # 1-5 当前步骤
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
    "target_duration": 60,
    "video_engine": "jimeng",
    "video_kind": "dialogue",       # 默认有对白，老项目读出来也是它（兼容）
    "voice_id": "",                 # 空 → 用 Fish S1 默认音色；段级 voice_id 可 override
    "script_hint": "",              # [AI-NOTE] 2026-05-08: 台本生成的用户补充指引（关键词/约束/避免点）
    "first_frame_asset_id": None,
    "first_frame_prompt": None,
    "final_video_asset_id": None,   # 装配后写入；让 update_outhan_data 不用绕底层
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
    update_outhan_data(project_id, current_step=max(1, min(5, step)))


def step_path(project_id: str, step: int) -> str:
    """步骤号 → URL 路径段。

    [AI-NOTE] 2026-05-08: 5 步模型——把选题+首帧合并成 step 1（选题信息直接驱动首帧）。
    旧 6 步映射已废弃。
    """
    names = {1: "topic", 2: "script", 3: "video", 4: "audio", 5: "package"}
    return names.get(step, "topic")


# ============== segments ===================

def get_segments(project_id: str) -> list[dict[str, Any]]:
    meta = _read_meta(project_id)
    segs = list(meta.get("outhan_segments", []))
    # [AI-NOTE] 2026-05-08: 老段没 lines 字段时，即时 wrap script → lines[0]
    # （读时 wrap，不写回 DB；只有用户主动改 lines 时才写）
    return [_ensure_lines(s) for s in segs]


def _ensure_lines(seg: dict[str, Any]) -> dict[str, Any]:
    """老段兼容：没 lines 字段时，把 script + audio_asset_id 包成单条 line。"""
    if "lines" in seg and isinstance(seg["lines"], list):
        return seg
    s = dict(seg)
    script = (s.get("script") or "").strip()
    s["lines"] = [{
        "id": "L0",
        "text": script,
        "voice_id": "",                           # 空 → 跟段级 voice_id；段级也空就 S1 默认
        "audio_asset_id": s.get("audio_asset_id"),
        "start_at": 0.0,
        "duration_hint": float(s.get("duration") or 5),
    }] if script or s.get("audio_asset_id") else []
    return s


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
    [AI-NOTE] 2026-05-08: 同步清 lines.audio_asset_id（保留 text/voice_id/start_at）。
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
        for ln in s.get("lines") or []:
            ln["audio_asset_id"] = None
        if s.get("index") == 0:
            s["first_frame_asset_id"] = project_first_frame
        else:
            s["first_frame_asset_id"] = None
    meta["outhan_segments"] = segs
    _write_meta(project_id, meta)


# ============== lines (segment 内的多句配音) ===================

def _next_line_id(seg: dict[str, Any]) -> str:
    """生成段内唯一 line id（L0, L1, L2...），跳过已有 id。"""
    used = {ln.get("id") for ln in (seg.get("lines") or [])}
    n = 0
    while f"L{n}" in used:
        n += 1
    return f"L{n}"


def update_line(
    project_id: str, seg_index: int, line_id: str, **fields: Any
) -> dict[str, Any] | None:
    """更新段内某条 line 的字段。返回更新后的 line；段或 line 不存在返回 None。"""
    meta = _read_meta(project_id)
    segs = meta.get("outhan_segments", [])
    target_seg = None
    for s in segs:
        if s.get("index") == seg_index:
            target_seg = s
            break
    if target_seg is None:
        return None
    target_seg = _ensure_lines(target_seg)
    target_line = None
    for ln in target_seg["lines"]:
        if ln.get("id") == line_id:
            ln.update(fields)
            target_line = ln
            break
    if target_line is None:
        return None
    # 写回（target_seg 是 dict 的引用，但 _ensure_lines 可能已经 wrap 出新 dict——
    # 找原段写回 lines）
    for s in segs:
        if s.get("index") == seg_index:
            s["lines"] = target_seg["lines"]
            break
    meta["outhan_segments"] = segs
    _write_meta(project_id, meta)
    return target_line


def add_line(
    project_id: str, seg_index: int, *,
    text: str = "", voice_id: str = "",
    start_at: float = 0.0, duration_hint: float = 2.0,
) -> dict[str, Any] | None:
    """在段尾追加一条 line。返回新 line。"""
    meta = _read_meta(project_id)
    segs = meta.get("outhan_segments", [])
    for i, s in enumerate(segs):
        if s.get("index") == seg_index:
            s = _ensure_lines(s)
            new_line = {
                "id": _next_line_id(s),
                "text": text,
                "voice_id": voice_id,
                "audio_asset_id": None,
                "start_at": float(start_at),
                "duration_hint": float(duration_hint),
            }
            s["lines"].append(new_line)
            segs[i] = s
            meta["outhan_segments"] = segs
            _write_meta(project_id, meta)
            return new_line
    return None


def remove_line(project_id: str, seg_index: int, line_id: str) -> bool:
    """删除段内某条 line。返回是否删除成功。"""
    meta = _read_meta(project_id)
    segs = meta.get("outhan_segments", [])
    for i, s in enumerate(segs):
        if s.get("index") == seg_index:
            s = _ensure_lines(s)
            before = len(s["lines"])
            s["lines"] = [ln for ln in s["lines"] if ln.get("id") != line_id]
            if len(s["lines"]) == before:
                return False
            segs[i] = s
            meta["outhan_segments"] = segs
            _write_meta(project_id, meta)
            return True
    return False


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
            for ln in s.get("lines") or []:
                ln["audio_asset_id"] = None
            if s.get("index") == index + 1:
                s["first_frame_asset_id"] = None
    meta["outhan_segments"] = segs
    _write_meta(project_id, meta)
