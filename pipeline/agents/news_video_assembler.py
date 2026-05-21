"""新闻口播视频合成器 · v2 语义切段 + 嘴动节奏匹配。

合成链路（三阶段）：
1. _plan_durations_by_script —— 按文案标点切段 (dur, framing)
   • 在句末标点处切，累计 3.5-9s 一段，等比对齐音频时长
2. _match_takes_to_segments —— 给每段挑 (take_idx, sub_start_sec)
   • 视角层面强制轮换（正→侧→正）
   • 视角内池子按嘴动节奏跟配音节奏的相似度排序，选最像的子窗口
3. 合成阶段 —— ffmpeg -ss 从 take 任意时间点截取，不再循环整个 take
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pipeline.tools import ffmpeg_client as _ffm

# 标点权重
_STRONG_PUNCT = set("。！？!?")     # 句末，理想切点
_WEAK_PUNCT = set("，,；;——")      # 分句，弱切点


def _probe_size(video_path: Path) -> tuple[int, int]:
    """ffprobe 拿视频尺寸，失败返回 1080x1450。"""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0 and out.stdout.strip():
            parts = out.stdout.strip().split(",")
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return 1080, 1450


def _plan_segments(audio_dur: float, n_takes: int, switch_every: float = 6.0) -> list[tuple[float, int]]:
    """v0 fallback · 等时切片。返回 [(seg_dur, take_idx), ...]"""
    segments: list[tuple[float, int]] = []
    cur = 0.0
    take_idx = 0
    while cur < audio_dur - 0.05:
        seg_dur = min(switch_every, audio_dur - cur)
        if seg_dur < 1.0 and segments:
            prev_dur, prev_ti = segments[-1]
            segments[-1] = (prev_dur + seg_dur, prev_ti)
        else:
            segments.append((seg_dur, take_idx % max(1, n_takes)))
            take_idx += 1
        cur += seg_dur
    return segments


def _split_by_punctuation(text: str) -> list[tuple[str, bool]]:
    """文案按标点切成 [(sub, is_strong_end), ...]。

    is_strong_end: 末尾是否为 。！？ 句末标点。
    弱标点 ，；—— 也会切但 is_strong_end=False（不作为优先切镜点）。
    """
    text = (text or "").strip()
    if not text:
        return []
    # 把所有标点都作为分隔，但保留在分句末尾
    all_punct = "".join(_STRONG_PUNCT | _WEAK_PUNCT)
    # 用 lookbehind 在标点后断
    pattern = re.compile(rf"(?<=[{re.escape(all_punct)}])\s*")
    parts = pattern.split(text)
    result: list[tuple[str, bool]] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        is_strong = p[-1] in _STRONG_PUNCT if p else False
        result.append((p, is_strong))
    return result


def _plan_durations_by_script(
    audio_dur: float,
    script: str,
    framings: list[str] | None,
    *,
    pace: float = 5.5,
    target_min: float = 3.5,
    target_max: float = 9.0,
) -> list[tuple[float, str]]:
    """阶段 1 · 按文案切段时长 + 决定每段视角。
    返回 [(seg_dur_sec, framing), ...]
    """
    parts = _split_by_punctuation(script)
    if not parts:
        return []

    # 按时长切段
    seg_durs: list[float] = []
    cur_dur = 0.0
    for sub, is_strong in parts:
        cur_dur += len(sub) / pace
        if (cur_dur >= target_max) or (is_strong and cur_dur >= target_min):
            seg_durs.append(cur_dur)
            cur_dur = 0.0
    if cur_dur > 0:
        if seg_durs and cur_dur < target_min * 0.6:
            seg_durs[-1] += cur_dur
        else:
            seg_durs.append(cur_dur)

    # 等比缩放对齐真实音频
    text_total = sum(seg_durs)
    if text_total > 0.1:
        scale = audio_dur / text_total
        seg_durs = [d * scale for d in seg_durs]

    # 给每段配 framing（视角轮换）
    framing_order = list(dict.fromkeys(framings or ["默认"]))
    result = [(d, framing_order[i % len(framing_order)]) for i, d in enumerate(seg_durs)]
    return result


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """简化版余弦相似度。长度不一致时按短的截。"""
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    import math
    av = a[:n]
    bv = b[:n]
    dot = sum(x * y for x, y in zip(av, bv))
    na = math.sqrt(sum(x * x for x in av)) or 1e-9
    nb = math.sqrt(sum(y * y for y in bv)) or 1e-9
    return dot / (na * nb)


def _match_takes_to_segments(
    seg_plan: list[tuple[float, str]],
    audio_rhythm_curve: list[float],
    rhythm_sps: int,
    takes_meta: list[dict],
    framings: list[str],
) -> list[tuple[float, int, float]]:
    """阶段 2 · 给每段挑 (take_idx, sub_start_sec)。

    匹配规则（按优先级）：
    1. take 的 framing 必须等于该段 framing
    2. 在符合视角的池里，按"嘴动子曲线 vs 配音节奏曲线"余弦相似度排序
    3. 选最高的，但避免连续两段用同一个 take（视角内防重复）
    4. 子窗口起点：在 take 时长允许范围内，扫描所有可能起点，挑相似度最高的

    takes_meta: 每个 take 的字典 [{id, motion_curve, samples_per_sec, duration_sec}, ...]
    audio_rhythm_curve: 配音的 RMS 曲线（10/s）
    """
    result: list[tuple[float, int, float]] = []
    last_take_in_framing: dict[str, int] = {}   # 每个 framing 上一次用的 take_idx（同视角防连续重复）
    audio_cur_sec = 0.0

    for seg_dur, want_framing in seg_plan:
        # 该段配音对应的节奏窗口
        seg_audio = _slice_curve(audio_rhythm_curve, audio_cur_sec, seg_dur, rhythm_sps)

        # 候选 take：视角匹配
        candidates = [
            (idx, t) for idx, (t, fr) in enumerate(zip(takes_meta, framings))
            if fr == want_framing
        ]
        # 同视角内防重复：去掉上次该视角用过的 take（仅当池子 ≥ 2）
        last_same_fr = last_take_in_framing.get(want_framing)
        if last_same_fr is not None and len(candidates) > 1:
            candidates = [c for c in candidates if c[0] != last_same_fr]

        # 给每个候选 take 找"最佳子窗口"
        best_idx, best_start, best_score = candidates[0][0], 0.0, -1.0
        for idx, t in candidates:
            mc = t.get("motion_curve") or []
            sps = t.get("samples_per_sec", 10)
            take_dur = t.get("duration_sec", len(mc) / max(1, sps))
            # take 比段短：从 0 开始，循环时也能用，但相似度按子段算
            # take 比段长：扫描多个起点（每 0.5s 一个候选起点）
            if take_dur <= seg_dur + 0.3:
                start_options = [0.0]
            else:
                step = 0.5
                start_options = list(_np_arange(0.0, take_dur - seg_dur + 0.01, step))
            for st in start_options:
                sub = _slice_curve(mc, st, seg_dur, sps)
                if not sub:
                    continue
                # 同长度对齐后比相似度
                score = _cosine_similarity(seg_audio, _resample(sub, len(seg_audio)))
                if score > best_score:
                    best_score = score
                    best_idx = idx
                    best_start = st

        result.append((seg_dur, best_idx, best_start))
        last_take_in_framing[want_framing] = best_idx
        audio_cur_sec += seg_dur

    return result


def _np_arange(a: float, b: float, step: float) -> list[float]:
    out = []
    x = a
    while x < b:
        out.append(x)
        x += step
    return out


def _slice_curve(curve: list[float], start_sec: float, length_sec: float, sps: int) -> list[float]:
    """从曲线（每秒 sps 点）截取 [start, start+length] 秒。"""
    s = int(round(start_sec * sps))
    n = int(round(length_sec * sps))
    s = max(0, min(s, max(0, len(curve) - 1)))
    return curve[s:s + n]


def _resample(curve: list[float], target_n: int) -> list[float]:
    """线性重采样到 target_n 点。"""
    if not curve or target_n <= 0:
        return [0.0] * target_n
    if len(curve) == target_n:
        return list(curve)
    import numpy as np
    src_idx = np.linspace(0, len(curve) - 1, target_n)
    return [float(np.interp(i, range(len(curve)), curve)) for i in src_idx]


def assemble(
    takes: list[Path],
    audio_path: Path,
    out_path: Path,
    *,
    script: str = "",
    framings: list[str] | None = None,
    takes_meta: list[dict] | None = None,
    switch_every: float = 6.0,
    target_size: tuple[int, int] | None = None,
    fps: int = 30,
    subtitle_burn: bool = False,
    subtitle_ass_style: str = "",
    chyron_enabled: bool = False,
    chyron_profile: dict | None = None,
    chyron_label: str = "",
    chyron_title: str = "",
    chyron_ticker: str = "",
    chyron_live_dot: bool = True,
    chyron_title_sub: str = "",
    chyron_station: str = "ZNN",
    chyron_time_str: str = "",
    bgm_path: Path | None = None,
    bgm_volume_db: float = -16.0,
    script_units: list[str] | None = None,
) -> dict[str, Any]:
    """主入口。

    - script 非空 + takes_meta 含 motion_curve → 走 v2 嘴动节奏匹配
    - script 非空但 takes_meta 缺 → 走 v1 语义切（仅按视角轮换）
    - script 为空 → 等时切（v0 fallback）
    """
    if not takes:
        return {"ok": False, "error": "镜头模板系列里没有 take 视频"}
    audio_path = Path(audio_path)
    if not audio_path.exists():
        return {"ok": False, "error": f"配音文件不存在: {audio_path}"}
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    audio_dur = _ffm.probe_duration(str(audio_path))
    if audio_dur < 0.1:
        return {"ok": False, "error": "配音时长读取失败或过短"}

    if target_size is None:
        target_size = _probe_size(takes[0])
    tw, th = target_size

    # 三档算法分支
    has_script = bool((script or "").strip())
    has_motion = bool(takes_meta and all(t.get("motion_curve") for t in takes_meta))
    mode = "rhythm" if (has_script and has_motion) else ("semantic" if has_script else "even")

    # plan: [(seg_dur, take_idx, sub_start_sec)]
    plan: list[tuple[float, int, float]] = []
    if mode == "rhythm":
        from pipeline.agents import audio_rhythm_analyzer as _ara
        seg_plan = _plan_durations_by_script(audio_dur, script, framings)
        # 配音节奏曲线
        rhythm = _ara.analyze_audio(audio_path)
        plan = _match_takes_to_segments(
            seg_plan,
            rhythm["rhythm_curve"],
            rhythm["samples_per_sec"],
            takes_meta or [],
            framings or [],
        )
    elif mode == "semantic":
        # 没有 motion_curve，退化为视角轮换 + take 平铺
        seg_plan = _plan_durations_by_script(audio_dur, script, framings)
        framing_order = list(dict.fromkeys(framings or ["默认"]))
        # 按视角分组 take idx
        groups: dict[str, list[int]] = {}
        for idx, fr in enumerate(framings or []):
            groups.setdefault(fr, []).append(idx)
        last_in_framing: dict[str, int] = {}
        for seg_dur, fr in seg_plan:
            cands = groups.get(fr) or list(range(len(takes)))
            last = last_in_framing.get(fr, -1)
            chosen = cands[(last + 1) % len(cands)]
            last_in_framing[fr] = chosen
            plan.append((seg_dur, chosen, 0.0))
    else:
        # 等时切
        base = _plan_segments(audio_dur, len(takes), switch_every=switch_every)
        plan = [(d, ti, 0.0) for d, ti in base]

    # 临时目录
    tmp_dir = out_path.parent / f".{out_path.stem}_tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    # 渲染每段为静音视频片段
    seg_files: list[Path] = []
    vf = (f"scale={tw}:{th}:force_original_aspect_ratio=increase,"
          f"crop={tw}:{th},setsar=1,fps={fps}")
    for i, (seg_dur, ti, sub_start) in enumerate(plan):
        seg_file = tmp_dir / f"seg_{i:03d}.mp4"
        # 若 take 比段长，用 -ss 截取；不够长则保留 stream_loop 兜底
        # -ss 放在 -i 之前是 fast seek，比放后面快很多
        args = []
        if sub_start > 0.05:
            args += ["-ss", f"{sub_start:.3f}"]
        args += ["-stream_loop", "-1", "-i", str(takes[ti])]
        args += [
            "-t", f"{seg_dur:.3f}",
            "-vf", vf,
            "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            str(seg_file),
        ]
        r = _ffm._run_ffmpeg(args)
        if not r.get("ok"):
            return {"ok": False, "error": f"段 {i} 渲染失败: {r.get('error')}", "stderr": r.get("stderr", "")[-500:]}
        seg_files.append(seg_file)

    # 拼成长静音视频
    silent_video = tmp_dir / "silent.mp4"
    r = _ffm.concat_clips(seg_files, silent_video)
    if not r.get("ok"):
        return {"ok": False, "error": f"拼接失败: {r.get('error')}", "stderr": r.get("stderr", "")[-500:]}

    # 贴配音音轨（可选 BGM amix）
    # pipeline: silent_video → audio_muxed → chyron_overlaid → subtitle_burned (= out_path)
    do_chyron = bool(chyron_enabled and chyron_profile)
    do_burn = bool(subtitle_burn and script and subtitle_ass_style)
    do_bgm = bool(bgm_path and Path(bgm_path).exists())
    # 决定第一步输出位置：有后续 pass 则写临时文件，否则直接写最终
    audio_muxed = tmp_dir / "with_audio.mp4" if (do_chyron or do_burn) else out_path

    if do_bgm:
        # 配音 + BGM amix（BGM 衰减到 bgm_volume_db，循环到配音时长，淡出 0.5s 防爆音）
        bgm_p = Path(bgm_path)
        filter_complex = (
            f"[2:a]aloop=loop=-1:size=2e9,"
            f"volume={bgm_volume_db}dB,"
            f"atrim=duration={audio_dur:.3f},"
            f"afade=t=out:st={max(0, audio_dur - 0.5):.3f}:d=0.5"
            f"[bgm];"
            f"[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=0,"
            f"alimiter=limit=0.97[a]"
        )
        args = [
            "-i", str(silent_video),
            "-i", str(audio_path),
            "-i", str(bgm_p),
            "-filter_complex", filter_complex,
            "-map", "0:v:0",
            "-map", "[a]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k",
            "-shortest",
            str(audio_muxed),
        ]
    else:
        args = [
            "-i", str(silent_video), "-i", str(audio_path),
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            "-map", "0:v:0", "-map", "1:a:0",
            "-shortest",
            str(audio_muxed),
        ]
    r = _ffm._run_ffmpeg(args)
    if not r.get("ok"):
        return {"ok": False, "error": f"贴音轨失败: {r.get('error')}", "stderr": r.get("stderr", "")[-500:]}

    # ─── chyron pass · 央视/凤凰新闻栏目条 ───
    chyron_warning = None
    chyron_applied = False
    if do_chyron:
        chyron_in = audio_muxed
        chyron_out = tmp_dir / "with_chyron.mp4" if do_burn else out_path
        try:
            r_chy = _apply_chyron(
                chyron_in, chyron_out,
                profile=chyron_profile,
                label=chyron_label,
                title=chyron_title,
                video_w=tw, video_h=th,
                ticker_text=chyron_ticker,
                live_dot=chyron_live_dot,
                title_sub=chyron_title_sub,
                station=chyron_station,
                time_str=chyron_time_str,
                audio_dur=audio_dur,
            )
            if r_chy.get("ok"):
                audio_muxed = chyron_out  # 链给字幕 pass
                chyron_applied = True
            else:
                chyron_warning = f"chyron 失败 fallback 无栏目条: {r_chy.get('error')}"
        except Exception as e:
            chyron_warning = f"chyron 异常: {type(e).__name__}: {e}"

    # 字幕烧录 pass · 用 news_subtitle_aligner 做声学吸附（librosa 静音检测）
    # [AI-NOTE] 2026-05-18: 重写——纯字数比例分配会"漂"（TTS 在标点处真停顿但字数=0）。
    # 改走：语义切分 → librosa 找静音段 → 把 cue 边界吸附到静音中点。
    # 字幕在 TTS 停顿时闪掉，跟着换气节奏出现 / 消失，不再漂。
    srt_segments_count = 0
    align_mode = "n/a"
    if do_burn:
        try:
            from pipeline.agents import news_subtitle_aligner as _aligner
            # 优先用用户审过的 script_units（在步骤 2 编辑），N 个 unit 严格对 N-1 个真停顿；
            # 没 units 则降级走 script 文本切分（旧逻辑）
            if script_units:
                cues, align_mode = _aligner.align_units_to_audio(
                    script_units, audio_path, audio_dur,
                )
            else:
                cues, align_mode = _aligner.align_cues_to_audio(
                    script, audio_path, audio_dur,
                    max_chars=14, min_chars=4,
                )
            srt_text = _ffm.compose_srt(cues)
            srt_path = tmp_dir / "subs.srt"
            srt_path.write_text(srt_text, encoding="utf-8")
            srt_segments_count = len(cues)

            # 字幕样式覆盖：用实际视频尺寸 + 抬高位置避开 chyron + 字号加大
            # chyron 占 22% 底部 (1668×0.22 ≈ 367px)，MarginV 取 410 留 ~43px 间距
            chyron_h_px = int(th * 0.22) if chyron_enabled else 0
            margin_v = chyron_h_px + 60 if chyron_enabled else 80
            forced_style = (
                f"PlayResX={tw},PlayResY={th},"
                "FontName=PingFang SC,FontSize=72,Bold=1,"
                "PrimaryColour=&HFFFFFF,OutlineColour=&H000000,"
                "BorderStyle=1,Outline=5,Shadow=2,"
                f"Alignment=2,MarginV={margin_v},WrapStyle=2"
            )
            r_burn = _ffm.burn_subtitles(audio_muxed, srt_path, out_path, style=forced_style)
            if not r_burn.get("ok"):
                # 烧字幕失败 → 用无字幕版兜底（不让整个流程失败）
                shutil.copy(audio_muxed, out_path)
                burn_warning = f"字幕烧录失败 fallback 无字幕: {r_burn.get('error')}"
            else:
                burn_warning = None
        except Exception as e:
            shutil.copy(audio_muxed, out_path)
            burn_warning = f"字幕生成异常: {type(e).__name__}: {e}"
    else:
        burn_warning = None

    # 清理临时文件
    try:
        shutil.rmtree(tmp_dir)
    except Exception:
        pass

    return {
        "ok": True,
        "out_path": str(out_path),
        "duration_sec": audio_dur,
        "segments": len(plan),
        "size": f"{tw}x{th}",
        "switch_every": switch_every,
        "mode": mode,
        "plan": [(round(d, 2), ti, round(st, 2)) for d, ti, st in plan],
        "subtitle_burned": bool(do_burn and not burn_warning),
        "subtitle_segments": srt_segments_count,
        "burn_warning": burn_warning,
        "chyron_applied": chyron_applied,
        "chyron_warning": chyron_warning,
    }


# ─── chyron 实现 ───

def _apply_chyron(
    in_video: Path,
    out_video: Path,
    *,
    profile: dict,
    label: str,
    title: str,
    video_w: int,
    video_h: int,
    ticker_text: str = "",
    live_dot: bool = True,
    title_sub: str = "",
    station: str = "ZNN",
    time_str: str = "",
    audio_dur: float = 30.0,
) -> dict[str, Any]:
    """根据 profile.engine 分发到 hyperframes / pil_overlay / ffmpeg_drawbox。"""
    engine = profile.get("engine", "ffmpeg_drawbox")
    if engine == "hyperframes":
        return _apply_chyron_hyperframes(
            in_video, out_video,
            profile=profile, label=label,
            title_main=title, title_sub=title_sub,
            station=station, time_str=time_str,
            video_w=video_w, video_h=video_h,
            duration_sec=audio_dur,
        )
    if engine == "pil_overlay":
        return _apply_chyron_znn(
            in_video, out_video,
            profile=profile, label=label,
            title_main=title, title_sub=title_sub,
            station=station, time_str=time_str,
            video_w=video_w, video_h=video_h,
        )
    return _apply_chyron_drawbox(
        in_video, out_video,
        profile=profile, label=label, title=title,
        video_w=video_w, video_h=video_h,
        ticker_text=ticker_text, live_dot=live_dot,
    )


def _apply_chyron_hyperframes(
    in_video: Path,
    out_video: Path,
    *,
    profile: dict,
    label: str,
    title_main: str,
    title_sub: str,
    station: str,
    time_str: str,
    video_w: int,
    video_h: int,
    duration_sec: float,
) -> dict[str, Any]:
    """hyperframes 渲染透明 chyron mov + ffmpeg overlay 到主视频。"""
    from pipeline.agents import news_hyperframes_renderer as _hf

    # 用本地当前时间补 time_str 默认
    import time as _t
    if not time_str:
        time_str = _t.strftime("%H:%M")

    chyron_mov = out_video.parent / f".{out_video.stem}_chyron.mov"

    # 1. hyperframes 渲染透明 mov
    variables = {
        "label": label or "DEVELOPING",
        "title_main": title_main or "",
        "title_sub": title_sub or "",
        "station": station or "ZNN",
        "time_str": time_str,
    }
    r_hf = _hf.render_chyron_overlay(
        variables=variables,
        duration_sec=duration_sec,
        out_path=chyron_mov,
        fps=30,
        template_id=profile.get("template_id", "funny_zoo"),
        timeout=int(max(120, duration_sec * 3)),
    )
    if not r_hf.get("ok"):
        return {
            "ok": False,
            "error": f"hyperframes 渲染失败: {r_hf.get('error')}",
            "stderr": (r_hf.get("stderr", "") or "")[-500:],
        }

    # 2. ffmpeg overlay 透明 mov 到主视频
    r = _ffm._run_ffmpeg([
        "-i", str(in_video),
        "-i", str(chyron_mov),
        "-filter_complex",
        "[0:v][1:v]overlay=x=0:y=0:format=auto:shortest=1[v]",
        "-map", "[v]", "-map", "0:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        str(out_video),
    ])

    try:
        chyron_mov.unlink()
    except Exception:
        pass

    if r.get("ok"):
        r["chyron_render_elapsed_sec"] = r_hf.get("elapsed_sec")
    return r


def _apply_chyron_znn(
    in_video: Path,
    out_video: Path,
    *,
    profile: dict,
    label: str,
    title_main: str,
    title_sub: str,
    station: str,
    time_str: str,
    video_w: int,
    video_h: int,
) -> dict[str, Any]:
    """PIL 画 chyron PNG → ffmpeg overlay（含滑入动效）。"""
    from pipeline.agents import news_chyron_renderer as _ncr
    import time as _time

    tmp_png = out_video.parent / f".{out_video.stem}_chyron.png"
    r_render = _ncr.render_znn_chyron(
        profile=profile,
        label=label,
        title_main=title_main,
        title_sub=title_sub,
        station=station or "ZNN",
        time_str=time_str or _time.strftime("%H:%M"),
        video_w=video_w, video_h=video_h,
        out_path=tmp_png,
    )
    if not r_render.get("ok"):
        return {"ok": False, "error": "chyron PNG 渲染失败"}

    # ffmpeg overlay 加 y 滑入动效（前 0.5s 从下方滑入）
    bar_h = r_render["chyron_h"]
    # y 表达式: 前 0.5s 从 main_h（画面外底）滑到 main_h-overlay_h，缓动
    y_expr = (
        f"if(lt(t,0.5),"
        f"main_h-overlay_h+overlay_h*pow(1-t/0.5,3),"
        f"main_h-overlay_h)"
    )

    # -loop 1 强制 PNG 作为单帧无限循环（默认 PNG 只播一帧，14s 后 chyron 会消失）
    # -shortest 让输出跟主视频等长
    r = _ffm._run_ffmpeg([
        "-i", str(in_video),
        "-loop", "1", "-i", str(tmp_png),
        "-filter_complex",
        f"[1:v]format=rgba[chy];[0:v][chy]overlay=x=0:y='{y_expr}':format=auto:shortest=1[v]",
        "-map", "[v]", "-map", "0:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(out_video),
    ])

    try:
        tmp_png.unlink()
    except Exception:
        pass

    return r


def _apply_chyron_drawbox(
    in_video: Path,
    out_video: Path,
    *,
    profile: dict,
    label: str,
    title: str,
    video_w: int,
    video_h: int,
    ticker_text: str = "",
    live_dot: bool = True,
) -> dict[str, Any]:
    """ffmpeg drawbox + drawtext 多层央视/凤凰栏目条 · 含动效。

    层次（自底向上）：
      1. 主底条（深红/深蓝）
      2. 顶部 4px 亮色高亮线
      3. 左侧 label 色块（更深红 + 顶部 3px 高亮）
      4. label 文字（淡入）
      5. 标题文字（淡入 + 字符位移效果）
      6. LIVE 红点呼吸（drawtext ● + alpha sin 周期）
      7. LIVE 文字
      8. ticker 滚动（drawtext x 随时间移动）

    动效：
      - 0-0.4s 入场：文字 alpha 从 0 → 1
      - LIVE 红点 alpha = 0.5 + 0.5*sin(2*PI*t)  (1Hz 呼吸)
      - ticker x 表达式：从右往左循环滚动
    """
    bar_h_ratio = float(profile.get("bar_height_ratio", 0.17))
    bar_h = int(video_h * bar_h_ratio)
    bar_y = video_h - bar_h

    # 主条 / 高亮线 / label 色块尺寸
    accent_h = max(4, int(bar_h * 0.025))        # 顶部高亮线
    label_pad_x = int(video_w * 0.025)
    label_w = int(video_w * 0.22)
    label_bg_y = bar_y + accent_h + int(bar_h * 0.06)
    label_bg_h = int(bar_h * 0.50)               # 给底部 ticker 留空间
    label_accent_h = max(3, int(bar_h * 0.025))

    # 标题位置
    title_x = label_pad_x + label_w + int(video_w * 0.035)
    title_fontsize = int(bar_h * 0.32)
    title_y = label_bg_y + (label_bg_h - title_fontsize) // 2

    # ticker 位置（条带最底部一行）
    ticker_y = label_bg_y + label_bg_h + int(bar_h * 0.04)
    ticker_fontsize = int(bar_h * 0.20)

    # LIVE 红点位置（标题左侧）
    live_dot_x = title_x - int(bar_h * 0.35)
    live_dot_y = title_y + int(bar_h * 0.05)
    live_dot_fontsize = int(bar_h * 0.32)

    # 颜色
    bar_color = profile.get("bar_color_hex", "CC2222")
    accent_color = "FF6666" if bar_color.startswith("C") else "FF4444"  # 主色更亮一档作高亮
    label_bg = profile.get("label_bg_hex", "8C0000")
    label_color = profile.get("label_color_hex", "FFFFFF")
    title_color = profile.get("title_color_hex", "FFFFFF")

    font = "/System/Library/Fonts/PingFang.ttc"
    label_fontsize = int(bar_h * 0.28)

    title_safe = (title or "")[:16]
    label_safe = (label or "")[:6]
    ticker_safe = (ticker_text or "").strip()

    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")

    filters = []

    # 1. 主底条
    filters.append(
        f"drawbox=x=0:y={bar_y}:w={video_w}:h={bar_h}:color=0x{bar_color}@1.0:t=fill"
    )
    # 2. 顶部高亮线
    filters.append(
        f"drawbox=x=0:y={bar_y}:w={video_w}:h={accent_h}:color=0x{accent_color}@1.0:t=fill"
    )
    # 3. 左侧 label 背景色块
    filters.append(
        f"drawbox=x={label_pad_x}:y={label_bg_y}:w={label_w}:h={label_bg_h}:color=0x{label_bg}@1.0:t=fill"
    )
    # 3b. label 块顶部细高亮线
    filters.append(
        f"drawbox=x={label_pad_x}:y={label_bg_y}:w={label_w}:h={label_accent_h}:color=0x{accent_color}@1.0:t=fill"
    )

    # 4. label 文字（淡入）
    if label_safe:
        label_text_y = label_bg_y + (label_bg_h - label_fontsize) // 2
        filters.append(
            f"drawtext=fontfile='{font}':text='{_esc(label_safe)}':"
            f"fontcolor=0x{label_color}:fontsize={label_fontsize}:"
            f"alpha='if(lt(t,0.4),t/0.4,1)':"
            f"x={label_pad_x}+{label_w}/2-text_w/2:y={label_text_y}"
        )

    # 5. LIVE 红点呼吸（在标题左侧）
    live_dot_offset = 0
    if live_dot:
        filters.append(
            f"drawtext=fontfile='{font}':text='●':"
            f"fontcolor=0xFF3030:fontsize={live_dot_fontsize}:"
            f"alpha='0.6+0.4*sin(2*PI*t)':"
            f"x={live_dot_x}:y={live_dot_y}"
        )
        live_dot_offset = int(bar_h * 0.45)  # 给红点+LIVE文字让位

    # 6. LIVE 文字
    if live_dot:
        live_text_x = live_dot_x + int(bar_h * 0.30)
        live_text_y = live_dot_y + int(bar_h * 0.04)
        live_fontsize = int(bar_h * 0.22)
        filters.append(
            f"drawtext=fontfile='{font}':text='LIVE':"
            f"fontcolor=0xFFFFFF:fontsize={live_fontsize}:"
            f"alpha='if(lt(t,0.4),t/0.4,1)':"
            f"x={live_text_x}:y={live_text_y}"
        )
        title_x_actual = title_x + live_dot_offset
    else:
        title_x_actual = title_x

    # 7. 标题（淡入 + 横向 slide-in）
    if title_safe:
        # x 表达式：前 0.4s 从右滑入 50px
        x_expr = f"if(lt(t,0.4),{title_x_actual}+50*(1-t/0.4),{title_x_actual})"
        filters.append(
            f"drawtext=fontfile='{font}':text='{_esc(title_safe)}':"
            f"fontcolor=0x{title_color}:fontsize={title_fontsize}:"
            f"alpha='if(lt(t,0.4),t/0.4,1)':"
            f"x='{x_expr}':y={title_y}"
        )

    # 8. ticker 滚动（最底部一行 · 从右向左循环）
    if ticker_safe:
        # 滚动速度：80 px/秒
        ticker_x_expr = f"w-mod(80*t,w+text_w)"
        filters.append(
            f"drawtext=fontfile='{font}':text='{_esc(ticker_safe)}':"
            f"fontcolor=0xFFE0E0:fontsize={ticker_fontsize}:"
            f"x='{ticker_x_expr}':y={ticker_y}"
        )

    vf = ",".join(filters)

    return _ffm._run_ffmpeg([
        "-i", str(in_video),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        str(out_video),
    ])
