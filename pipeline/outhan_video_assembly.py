"""Outhan 一键合成成片：视频和音频走独立时间线 concat，最后 mux。

[AI-NOTE] 2026-05-07: 复用一二布布的 ffmpeg_client._ffmpeg_binary / _run_ffmpeg。
imageio_ffmpeg 不带 ffprobe，用 `ffmpeg -i` 解析 stderr 拿时长。
[AI-NOTE] 2026-05-08 改版：视频按段自然拼，音频按段自然拼；音频长出头时用 tpad 克隆
最后一帧补齐视频，避免"音频拖着视频循环重播"的 jarring 体验。silent 模式跳过音频环节。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from pipeline import outhan_meta as _outhan_meta
from pipeline import outhan_storage as _outhan_storage
from pipeline.tools import ffmpeg_client as _ffmpeg


# 输出参数（与 Outhan 视频源一致：720p / 16:9 / 30fps）
_OUTPUT_SIZE = "1280x720"
_OUTPUT_FPS = 30

_DURATION_RE = re.compile(r"Duration:\s*(\d{1,2}):(\d{2}):(\d{2})\.(\d+)")


def _media_duration_via_ffmpeg(media_path: str | Path) -> float | None:
    """用 `ffmpeg -i input` 解析 stderr 里的 `Duration: hh:mm:ss.xx` 拿时长。
    适用视频/音频/带视频带音频的 mp4。imageio_ffmpeg 不带 ffprobe 时的替代方案。"""
    bin_ = _ffmpeg._ffmpeg_binary()
    try:
        p = subprocess.run(
            [bin_, "-hide_banner", "-i", str(media_path)],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return None
    # ffmpeg 没指定输出会以 exit 1 退出，stderr 仍包含 metadata
    m = _DURATION_RE.search(p.stderr or "")
    if not m:
        return None
    hh, mm, ss, frac = m.groups()
    frac_sec = float("0." + frac)
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + frac_sec


def _normalize_video_silent(video_path: str | Path, out_path: Path) -> dict:
    """视频去音轨 + 统一到 1280x720/30fps/H.264，便于后续 concat。
    Seedance/Vidu 出的 mp4 编码/分辨率/fps 不一定一致，必须先归一才能 concat 不出乱。"""
    vf = (
        f"scale={_OUTPUT_SIZE.replace('x', ':')}:force_original_aspect_ratio=increase,"
        f"crop={_OUTPUT_SIZE.replace('x', ':')},setsar=1,fps={_OUTPUT_FPS}"
    )
    # _run_ffmpeg 自带前缀 -hide_banner -loglevel error -y
    return _ffmpeg._run_ffmpeg([
        "-i", str(video_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-an",  # 丢弃原音轨
        str(out_path),
    ])


def _concat_files(file_paths: list[Path], out_path: Path, list_label: str) -> dict:
    """concat demuxer 串联（视频或音频通用）。要求所有输入编码一致。"""
    bin_ = _ffmpeg._ffmpeg_binary()
    list_file = out_path.parent / f"{out_path.stem}.{list_label}.txt"
    list_file.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in file_paths),
        encoding="utf-8",
    )
    cmd = [
        bin_, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(out_path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return {"ok": r.returncode == 0, "stderr": (r.stderr or "")[-800:]}
    except Exception as e:
        return {"ok": False, "stderr": str(e)[:800]}


def _err(error_type: str, msg: str, log: list[str], **extra: Any) -> dict[str, Any]:
    """统一构造错误返回。error_type ∈ {"input","ffmpeg"}：路由层据此分 400/502。"""
    return {"ok": False, "error_type": error_type, "error": msg, "log": log, **extra}


def assemble_mp4(
    project_id: str,
    *,
    on_log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """装配 N 段视频 + N 段音频成单个 mp4。

    返回 dict：
      成功：{ok=True, asset_id, path, duration, log}
      失败：{ok=False, error_type ∈ {"input","ffmpeg"}, error, log, fail_index?}
    """
    log: list[str] = []

    def _log(msg: str) -> None:
        log.append(msg)
        if on_log:
            try:
                on_log(msg)
            except Exception:
                pass

    segs = sorted(_outhan_meta.get_segments(project_id), key=lambda s: s.get("index", 0))
    if not segs:
        return _err("input", "项目还没有分段", log)

    project_data = _outhan_meta.get_outhan_data(project_id)
    is_silent = (project_data.get("video_kind") or "dialogue") == "silent"
    _log(f"视频类型：{'纯画面（无配音）' if is_silent else '有对白（含配音）'}")

    # 输入校验
    for s in segs:
        idx = s.get("index", -1)
        if not s.get("video_asset_id"):
            return _err("input", f"段 {idx + 1} 缺视频", log, fail_index=idx)
        if not is_silent and not s.get("audio_asset_id"):
            return _err("input", f"段 {idx + 1} 缺音频", log, fail_index=idx)

    tmp_dir = _outhan_storage.project_dir(project_id) / "_assembly_tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    bin_ = _ffmpeg._ffmpeg_binary()
    try:
        # 步骤 1：每段视频去音轨 + 归一编码
        norm_videos: list[Path] = []
        for s in segs:
            idx = s["index"]
            video_asset = _outhan_storage.get_asset(project_id, s["video_asset_id"])
            if not video_asset:
                return _err("input", f"段 {idx + 1} 视频资源缺失", log, fail_index=idx)
            norm_out = tmp_dir / f"vid-{idx:02d}.mp4"
            r = _normalize_video_silent(video_asset["path"], norm_out)
            if not r.get("ok"):
                return _err(
                    "ffmpeg",
                    f"段 {idx + 1} 视频归一失败：{(r.get('stderr') or '')[:300]}",
                    log, fail_index=idx,
                )
            vd = _media_duration_via_ffmpeg(norm_out) or 0
            _log(f"段 {idx + 1}: 视频归一完成（{vd:.2f}s）")
            norm_videos.append(norm_out)

        # 步骤 2：concat 视频
        videos_only = tmp_dir / "videos_only.mp4"
        r = _concat_files(norm_videos, videos_only, "vlist")
        if not r.get("ok"):
            return _err("ffmpeg", f"视频 concat 失败：{r.get('stderr', '')[:300]}", log)
        video_total = _media_duration_via_ffmpeg(videos_only) or 0
        _log(f"视频 concat 完成，总时长 {video_total:.2f}s")

        # silent 分支：拼好的视频本身就是成片（已无音轨）
        if is_silent:
            _log("纯画面模式：跳过音频 concat 与 mux")
            final_tmp = videos_only
            total_duration = video_total
        else:
            # 步骤 3：concat 音频
            audio_paths: list[Path] = []
            for s in segs:
                audio_asset = _outhan_storage.get_asset(project_id, s["audio_asset_id"])
                if not audio_asset:
                    return _err(
                        "input", f"段 {s['index'] + 1} 音频资源缺失",
                        log, fail_index=s["index"],
                    )
                audio_paths.append(Path(audio_asset["path"]))
            audio_only = tmp_dir / "audio_only.mp3"
            r = _concat_files(audio_paths, audio_only, "alist")
            if not r.get("ok"):
                return _err("ffmpeg", f"音频 concat 失败：{r.get('stderr', '')[:300]}", log)
            audio_total = _media_duration_via_ffmpeg(audio_only) or 0
            _log(f"音频 concat 完成，总时长 {audio_total:.2f}s")

            # 步骤 4：音频长 → 用 tpad 克隆最后一帧补齐视频；视频长 → 末尾静音播放
            gap = audio_total - video_total
            if gap > 0.05:
                extended = tmp_dir / "videos_extended.mp4"
                cmd = [
                    bin_, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(videos_only),
                    "-vf", f"tpad=stop_mode=clone:stop_duration={gap:.2f}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-pix_fmt", "yuv420p",
                    str(extended),
                ]
                try:
                    rr = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                    if rr.returncode != 0:
                        return _err(
                            "ffmpeg",
                            f"视频末尾补帧失败：{(rr.stderr or '')[-300:]}",
                            log,
                        )
                except Exception as e:
                    return _err("ffmpeg", f"视频末尾补帧异常：{e}", log)
                video_input = extended
                _log(f"音频比视频长 {gap:.2f}s，已用最后一帧延展视频")
            else:
                video_input = videos_only
                if abs(gap) < 0.05:
                    _log(f"视频音频时长几乎相等（差 {abs(gap):.2f}s），无需补齐")
                else:
                    _log(f"视频比音频长 {-gap:.2f}s，结尾静音播放")

            # 步骤 5：mux 视频流 + 音频流 → final.mp4
            final_tmp = tmp_dir / "final.mp4"
            cmd = [
                bin_, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(video_input),
                "-i", str(audio_only),
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k",
                str(final_tmp),
            ]
            try:
                rr = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if rr.returncode != 0:
                    return _err(
                        "ffmpeg",
                        f"final mux 失败：{(rr.stderr or '')[-300:]}",
                        log,
                    )
            except Exception as e:
                return _err("ffmpeg", f"final mux 异常：{e}", log)
            _log("final mux 完成")
            total_duration = _media_duration_via_ffmpeg(final_tmp) or max(video_total, audio_total)

        # 落盘成正式 final_video 资产
        try:
            final_bytes = final_tmp.read_bytes()
        except Exception as e:
            return _err("ffmpeg", f"读取成片失败：{e}", log)

        is_final_meta = {"is_final": True, "segments_count": len(segs)}
        if is_silent:
            is_final_meta["silent"] = True
        saved = _outhan_storage.save_asset(
            project_id, "segment_video",  # 类型表无 final_video 子目录，复用 segments 目录
            final_bytes,
            base_name=f"final-{int(time.time())}",
            metadata=is_final_meta,
        )
        # 回写 final_video_asset_id（已加进 DEFAULT_OUTHAN，不用绕底层）
        _outhan_meta.update_outhan_data(project_id, final_video_asset_id=saved["id"])
        return {
            "ok": True,
            "asset_id": saved["id"],
            "path": saved["path"],
            "duration": total_duration,
            "log": log,
        }
    finally:
        # 异常路径也清掉临时目录，避免几十 MB 半成品堆积
        shutil.rmtree(tmp_dir, ignore_errors=True)
