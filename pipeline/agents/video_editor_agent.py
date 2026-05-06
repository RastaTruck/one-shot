"""剪辑 Agent · MVP · ffmpeg 合成管线。

输入：project_shot_table + /data/audio/<vid>/<sid>.mp3 + 已生成的 L3 图/视频（远程 URL）
输出：data/outputs/<vid>.mp4（9:16 · 30fps · H.264 + AAC · 烧字幕）

每段视觉源优先级：
  1. L3 video_url（如果有）
  2. L3 image_url（如果有）
  3. L0 背景（asset_library BG_xxx 的 file url）
  4. 纯色 fallback（金橙底）

字幕：按每段的 actual audio 时长 + shot.text 直接生成 SRT，烧录到最终视频。

进度：通过 callback 或全局 dict 暴露（本 MVP 直接 return 结果）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from pipeline import shot_table as _st
from pipeline.asset_library import AssetLibrary
from pipeline.tools import ffmpeg_client as ffm

# 字幕去标点：保留汉字/字母/数字/空格/百分号/货币符号，其余全部去掉
_SUBTITLE_STRIP_RE = re.compile(
    r'[，。！？、；：（）【】〔〕「」『』""''《》〈〉…—·\-,.!?;:()\[\]{}\'"、]+'
)


def _clean_subtitle(text: str) -> str:
    """去掉标点符号，保留汉字、数字、字母、空格、%、$、¥ 等。"""
    return _SUBTITLE_STRIP_RE.sub("", text).strip()


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
CLIPS_DIR = DATA_DIR / "outputs" / "_clips"
OUTPUTS_DIR = DATA_DIR / "outputs"

# 每个 shot 音频末尾追加的静音时长（秒）
# 给听众留反应时间，句子之间自然呼吸感；别改太大，>0.5s 会显得拖沓
SHOT_PAUSE_SEC: float = 0.20


@dataclass
class ComposeLog:
    steps: list[dict] = field(default_factory=list)

    def add(self, phase: str, shot_id: str | None, status: str, **extra) -> None:
        self.steps.append({"phase": phase, "shot_id": shot_id,
                             "status": status, **extra})


def _pick_visual_url(shot: _st.Shot, library: AssetLibrary) -> tuple[str, str]:
    """旧版：单视觉源选择 (后续被 _pick_visual_layers 替代)"""
    for ins in shot.element_insets:
        if ins.video_url:
            return ins.video_url, "l3_video"
    for ins in shot.element_insets:
        if ins.image_url:
            return ins.image_url, "l3_image"
    if shot.expression_id:
        a = library.get(shot.expression_id)
        if a is not None:
            return a.file, "l1_expression"
    if shot.background_id and not shot.background_id.startswith("MISSING:"):
        a = library.get(shot.background_id)
        if a is not None:
            return a.file, "l0_bg"
    return "", "fallback"


def _pick_visual_layers(
    shot: _st.Shot,
    library: AssetLibrary,
    bible: dict | None = None,
) -> dict:
    """返回每段需要的视觉图层映射：
       {primary: {url, type}, bg: <BG url 或 None>, ip: <IP url 或 None>}
    primary 决定主轨道（L3 > 否则 L0+L1 合成）。
    bg/ip 用于 composite。

    若 bible.bg_title_url 存在 + shot.background_id == bible.bg_id，
    背景用"已 P 标题"的版本（与画风步骤所见一致）。
    """
    out = {"primary": None, "bg": None, "ip": None}
    # L3 优先做 primary
    for ins in shot.element_insets:
        if ins.video_url:
            out["primary"] = {"url": ins.video_url, "type": "l3_video"}
            return out
    for ins in shot.element_insets:
        if ins.image_url:
            out["primary"] = {"url": ins.image_url, "type": "l3_image"}
            return out
    # 否则采集 BG + IP 用于 composite
    if shot.background_id and not shot.background_id.startswith("MISSING:"):
        bg_title_url = ((bible or {}).get("bg_title_url") or "").strip()
        locked_bg_id = ((bible or {}).get("bg_id") or "").strip()
        if bg_title_url and shot.background_id == locked_bg_id:
            # bible 里存的是 web URL（带前导 /），_localize 当绝对路径会找不到文件
            out["bg"] = bg_title_url.lstrip("/")
        else:
            a = library.get(shot.background_id)
            if a is not None:
                out["bg"] = a.file
    if shot.expression_id:
        a = library.get(shot.expression_id)
        if a is not None:
            out["ip"] = a.file
    return out


def _make_fallback_image(out_path: Path, size: str = "1080x1920") -> None:
    """没视觉源时用纯金橙底图兜底。"""
    w, h = size.split("x")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=#F48A0C:s={w}x{h}:d=1",
        "-frames:v", "1",
        str(out_path),
    ], check=True)


def compose_final(
    video_id: str,
    *,
    shot_table: _st.ShotTable,
    library: AssetLibrary,
    on_log: Optional[Callable[[dict], None]] = None,
    size: str = "1080x1920",
    fps: int = 30,
) -> dict[str, Any]:
    """合成完整 mp4 · 同步阻塞（几十秒 - 几分钟）."""
    log = ComposeLog()

    def emit(phase, shot_id=None, status="ok", **extra):
        entry = {"phase": phase, "shot_id": shot_id, "status": status, **extra}
        log.steps.append(entry)
        if on_log:
            try:
                on_log(entry)
            except Exception:
                pass

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # 清理旧片段，避免 concat 拼到脏片段
    clips_dir = CLIPS_DIR / video_id
    if clips_dir.exists():
        shutil.rmtree(clips_dir)
    clips_dir.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    # 提前读 bible —— 整期视频 bg / 标题 都从画风步骤承袭
    from pipeline import state as _state
    _saved_bible = _state.load_project_style_bible(video_id) or {}
    _bible_dict = _saved_bible.get("bible") or {}

    emit("start", status="ok", n_shots=len(shot_table.shots))

    clips: list[Path] = []
    srt_segments: list[dict] = []
    cursor_time = 0.0

    def _localize(url_or_path: str, shot_id: str = "") -> Path | None:
        if not url_or_path:
            return None
        if "placehold.co" in url_or_path or "via.placeholder.com" in url_or_path:
            emit("bg_placeholder_skipped", shot_id, status="warn",
                 msg="BG 是占位 URL，走纯色 fallback")
            return None
        if url_or_path.startswith("http"):
            try:
                return ffm.download_asset(url_or_path, CACHE_DIR)
            except Exception as e:
                emit("download_fail", shot_id, status="warn", msg=str(e)[:200])
                return None
            return None
        p = Path(url_or_path)
        return p if p.exists() else None

    def _slot_to_local(slot: "_st.BeatSlot") -> Path | None:
        """BeatSlot 解析成本地文件路径。优先走 image_url（L3 兜底），否则查 asset_library。"""
        if slot.image_url:
            return _localize(slot.image_url)
        if slot.asset_id and not slot.asset_id.startswith("MISSING:"):
            a = library.get(slot.asset_id)
            if a is not None and a.file:
                return _localize(a.file)
        return None

    for shot in shot_table.shots:
        # 1. 音频路径（本地真实 mp3）
        audio_path_str = shot.audio_clip_path or ""
        if not audio_path_str or audio_path_str.startswith("mock://"):
            emit("audio_missing", shot.shot_id, status="fail",
                 msg="该段还没生成配音 · 回素材页一键全部生成")
            return {"ok": False, "error": f"{shot.shot_id} 缺配音",
                    "log": log.steps}
        audio_path = (ROOT / audio_path_str) if not Path(audio_path_str).is_absolute() else Path(audio_path_str)
        if not audio_path.exists():
            emit("audio_missing", shot.shot_id, status="fail",
                 msg=f"音频文件不存在: {audio_path}")
            return {"ok": False, "error": f"{shot.shot_id} audio 不存在", "log": log.steps}

        try:
            audio_duration = ffm.probe_duration(audio_path)
        except Exception as e:
            emit("audio_probe_fail", shot.shot_id, status="fail", msg=str(e)[:200])
            return {"ok": False, "error": f"{shot.shot_id} 音频时长读取失败", "log": log.steps}

        # 末尾追加静音：给听众 SHOT_PAUSE_SEC 的呼吸时间
        padded_audio_path = clips_dir / f"{shot.shot_id}_padded.aac"
        pad_result = ffm.pad_audio_silence(audio_path, SHOT_PAUSE_SEC, padded_audio_path)
        if pad_result["ok"] and padded_audio_path.exists():
            render_audio = padded_audio_path
            shot_duration = audio_duration + SHOT_PAUSE_SEC
        else:
            # pad 失败时静默降级，用原始音频
            render_audio = audio_path
            shot_duration = audio_duration

        clip_out = clips_dir / f"{shot.shot_id}.mp4"

        # 2. L3 优先：如果有 element_inset 带 video_url / image_url，走整张画面路径（兼容老逻辑）
        layers = _pick_visual_layers(shot, library, bible=_bible_dict)
        if layers["primary"]:
            p = layers["primary"]
            visual_local = _localize(p["url"], shot.shot_id)
            if visual_local is None:
                visual_local = clips_dir / f"{shot.shot_id}_bg.png"
                _make_fallback_image(visual_local, size=size)
            emit("pick_visual", shot.shot_id, src=p["type"], url=p["url"][:100])
            if p["type"] == "l3_video" and visual_local.suffix.lower() in (".mp4", ".mov", ".m4v", ".webm"):
                r = ffm.render_shot_clip_from_video(
                    visual_local, render_audio, clip_out,
                    duration=shot_duration, size=size, fps=fps,
                )
            else:
                r = ffm.render_shot_clip_from_image(
                    visual_local, render_audio, clip_out,
                    duration=shot_duration, size=size, fps=fps,
                )
            if not r["ok"]:
                emit("render_fail", shot.shot_id, status="fail", stderr=r["stderr"])
                return {"ok": False, "error": f"{shot.shot_id} 渲染失败", "log": log.steps}
            emit("render_ok", shot.shot_id, duration=round(shot_duration, 2),
                 clip=str(clip_out.relative_to(ROOT)))
            clips.append(clip_out)
        else:
            # 3. Beat 路径：每 shot 拆 N beats，按时长比例切 audio + 各自 dual-overlay 渲染 + concat
            shot = _st.ensure_beats(shot)  # 老 shot 自动构造 1 个 center beat
            bg_local = _localize(layers["bg"], shot.shot_id) if layers["bg"] else None

            beat_clips: list[Path] = []
            cursor_audio = 0.0
            beat_dir = clips_dir / shot.shot_id
            beat_dir.mkdir(parents=True, exist_ok=True)
            # duration_ratio 归一化（防 user 改坏数据导致总和 ≠ 1）
            total_ratio = sum(b.duration_ratio for b in shot.beats) or 1.0
            n_beats = len(shot.beats)
            for bi, beat in enumerate(shot.beats):
                # 最后一个 beat 包含停顿时长，其余 beat 只用 audio_duration 分配
                is_last = (bi == n_beats - 1)
                beat_dur = audio_duration * beat.duration_ratio / total_ratio
                if is_last:
                    beat_dur += (shot_duration - audio_duration)  # 追加停顿
                left_local = _slot_to_local(beat.left)
                right_local = _slot_to_local(beat.right)
                src_label = []
                if bg_local: src_label.append("l0_bg")
                if left_local: src_label.append(f"L:{beat.left.asset_kind}")
                if right_local: src_label.append(f"R:{beat.right.asset_kind}")
                emit("pick_visual", shot.shot_id, beat=bi, layout=beat.layout,
                     src="+".join(src_label) or "fallback", duration=round(beat_dur, 2))

                beat_out = beat_dir / f"beat_{bi:02d}.mp4"
                rb = ffm.render_beat_clip(
                    bg_local, left_local, right_local, render_audio, beat_out,
                    duration=beat_dur, audio_offset=cursor_audio,
                    size=size, fps=fps, layout=beat.layout,
                )
                if not rb["ok"]:
                    emit("render_fail", shot.shot_id, beat=bi, status="fail", stderr=rb["stderr"])
                    return {"ok": False, "error": f"{shot.shot_id} beat {bi} 渲染失败", "log": log.steps}
                beat_clips.append(beat_out)
                cursor_audio += audio_duration * beat.duration_ratio / total_ratio
            # concat beats → 一个 shot clip
            if len(beat_clips) == 1:
                # 单 beat 直接当 shot clip
                import shutil as _sh
                _sh.copy2(beat_clips[0], clip_out)
            else:
                rc = ffm.concat_clips(beat_clips, clip_out)
                if not rc["ok"]:
                    emit("concat_fail", shot.shot_id, status="fail", stderr=rc["stderr"])
                    return {"ok": False, "error": f"{shot.shot_id} beats concat 失败", "log": log.steps}
            emit("render_ok", shot.shot_id, duration=round(shot_duration, 2),
                 beats=len(shot.beats),
                 clip=str(clip_out.relative_to(ROOT)))
            clips.append(clip_out)

        # 4. 字幕：时序基于 audio_duration（停顿期间不显示字幕，自然留白）
        #         所有字幕文本统一去掉标点；超出单行宽度则顺序拆成多条 cue
        units = [u for u in (shot.subtitle_units or []) if u.strip()]
        if units:
            total_chars = sum(len(u) for u in units) or 1
            t = cursor_time
            for u in units:
                ratio = len(u) / total_chars
                seg_dur = audio_duration * ratio
                clean = _clean_subtitle(u)
                if clean:
                    # 超宽则拆成多条顺序 cue，保证每条单行
                    chunks = ffm._subtitle_split_lines(clean)
                    chunk_dur = seg_dur / len(chunks)
                    tc = t
                    for chunk in chunks:
                        srt_segments.append({"start": round(tc, 3), "end": round(tc + chunk_dur, 3), "text": chunk})
                        tc += chunk_dur
                t += seg_dur
        else:
            cues = ffm.split_text_to_cues(
                shot.text,
                start=cursor_time, end=cursor_time + audio_duration,
                max_chars=18,
                min_chars=8,
            )
            for cue in cues:
                clean = _clean_subtitle(cue["text"])
                if clean:
                    srt_segments.append({**cue, "text": clean})
        cursor_time += shot_duration  # 含停顿，时间轴向前推

    # 5. 拼接所有段
    emit("concat_start", total_clips=len(clips))
    concat_out = clips_dir / "concat.mp4"
    r = ffm.concat_clips(clips, concat_out)
    if not r["ok"]:
        emit("concat_fail", status="fail", stderr=r["stderr"])
        return {"ok": False, "error": "concat 失败", "log": log.steps}
    emit("concat_ok", duration=round(cursor_time, 2))

    # 5.5 BGM 混音：从画风 bible 读 bgm_id，对应到 /bgm/<filename>.mp3，与原音轨混合（BGM 弱声）
    bgm_id = (_bible_dict.get("bgm_id") or "").strip()
    bgm_mixed = concat_out  # 默认 = 没 BGM 直接走原 concat
    if bgm_id and bgm_id != "none":
        # bgm_id = "bgm_1" / "bgm_2" 等 → 找文件
        stem = bgm_id.removeprefix("bgm_") if bgm_id.startswith("bgm_") else bgm_id
        bgm_file = ROOT / "bgm" / f"{stem}.mp3"
        if bgm_file.exists():
            bgm_mixed = clips_dir / "with_bgm.mp4"
            r_mix = ffm.mix_bgm(concat_out, bgm_file, bgm_mixed,
                                bgm_volume=0.18)  # BGM 18% 音量（约 -15dB）
            if not r_mix["ok"]:
                emit("bgm_mix_fail", status="warn", stderr=r_mix["stderr"][:200])
                bgm_mixed = concat_out  # 失败 → 走原音
            else:
                emit("bgm_mixed", status="ok", bgm=str(bgm_file.relative_to(ROOT)))
        else:
            emit("bgm_missing", status="warn", msg=f"bgm_id={bgm_id} 但文件 {bgm_file} 不存在")

    # 6. 生成 SRT（细化后字幕条数 >> shot 数）
    srt_path = clips_dir / "subs.srt"
    srt_text = ffm.compose_srt(srt_segments)
    srt_path.write_text(srt_text, encoding="utf-8")
    emit("srt_written", segments=len(srt_segments),
         shots=len(shot_table.shots))

    # 7. 烧字幕 → 最终 mp4（输入是已混 BGM 的版本）
    final_path = OUTPUTS_DIR / f"{video_id}.mp4"
    # 字幕样式（显式 PlayResY=1920 让 MarginV 单位与输出视频一致，否则默认 PlayResY=288 会让位置偏中）
    # MarginV=300 = 距底部 15.6%，竖版常见安全区
    # FontSize/Outline 按 1920 高度比例放大
    subtitle_style = (
        "PlayResX=1080,PlayResY=1920,"
        "FontName=PingFang SC,FontSize=108,Bold=1,"
        "PrimaryColour=&HFFFFFF,OutlineColour=&H000000,BorderStyle=1,"
        "Outline=8,Shadow=2,Alignment=2,MarginV=300,WrapStyle=0"
        # WrapStyle=0 → 禁止自动换行，严格单行
    )
    r = ffm.burn_subtitles(bgm_mixed, srt_path, final_path, style=subtitle_style)
    if not r["ok"]:
        emit("burn_fail", status="fail", stderr=r["stderr"])
        return {"ok": False, "error": "字幕烧录失败", "log": log.steps}
    emit("done", status="ok",
         path=str(final_path.relative_to(ROOT)),
         bytes=final_path.stat().st_size,
         duration=round(cursor_time, 2))

    return {
        "ok": True,
        "mp4_path": str(final_path.relative_to(ROOT)),
        "mp4_url": f"/data/outputs/{video_id}.mp4",
        "duration": round(cursor_time, 2),
        "bytes": final_path.stat().st_size,
        "log": log.steps,
    }
