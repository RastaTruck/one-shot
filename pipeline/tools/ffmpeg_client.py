"""ffmpeg 合成工具包。

职责：
- `download_asset(url, cache_dir)` —— 把远程图片/视频 URL 下载到本地缓存（placehold.co / ark-tos）
- `probe_duration(audio_path)` —— 获取 mp3 实际时长
- `render_shot_clip(visual, audio, out, *, duration)` —— 单段视频片段（静态图 + 音频 或 视频 + 音频）
- `concat_clips(clips, out)` —— 拼接所有段（ffmpeg concat demuxer）
- `burn_subtitles(mp4, srt, out)` —— 烧录 SRT 字幕
- `compose_srt(segments)` —— 生成 SRT 文本（输入 [{"start": sec, "end": sec, "text": ...}]）

所有 ffmpeg 调用用 subprocess，不依赖 ffmpeg-python。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# =========================================================================
# 工具
# =========================================================================

def _hash_url(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def _normalize_url(url: str) -> str:
    """修正 placehold.co 的 URL 让其返 PNG 而非 SVG（ffmpeg 不认 SVG）。
    placehold.co 支持 `/W x H / BG / FG.png?text=...` 或 `/W x H / BG / FG / png?text=...`。
    这里统一追加 `/png` 到路径尾部（参数前）。
    """
    if "placehold.co" not in url:
        return url
    # 已经是 png/jpg 的不改
    if re.search(r"\.(png|jpg|jpeg|webp)(\?|$)", url, re.I):
        return url
    if re.search(r"/png(\?|$)", url, re.I):
        return url
    # 在 query string 之前插入 /png
    if "?" in url:
        base, q = url.split("?", 1)
        return f"{base.rstrip('/')}/png?{q}"
    return f"{url.rstrip('/')}/png"


def download_asset(url: str, cache_dir: Path, suffix_hint: str | None = None) -> Path:
    """下载 url 到 cache_dir/<hash><ext>. 已缓存则直接返回。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    url = _normalize_url(url)
    parsed = urllib.parse.urlparse(url)
    ext = suffix_hint
    if not ext:
        path = parsed.path
        if "." in path.rsplit("/", 1)[-1]:
            ext = "." + path.rsplit(".", 1)[-1].split("?")[0].lower()
        else:
            ext = ".bin"
    target = cache_dir / f"{_hash_url(url)}{ext}"
    if target.exists() and target.stat().st_size > 0:
        return target
    req = urllib.request.Request(url, headers={"User-Agent": "yierbubu-pipeline/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(target, "wb") as f:
        f.write(resp.read())
    return target


def pad_audio_silence(
    audio_path: str | Path,
    extra_sec: float,
    out_path: str | Path,
) -> dict:
    """在音频末尾追加 extra_sec 秒静音，输出新文件。
    用于在 shot 之间插入自然停顿（不改动视频渲染函数本身）。
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    return _run_ffmpeg([
        "-i", str(audio_path),
        "-af", f"apad=pad_dur={extra_sec:.3f}",
        "-c:a", "aac", "-b:a", "128k",
        str(out),
    ])


def _ffmpeg_binary() -> str:
    """优先用 imageio_ffmpeg 自带二进制；fallback 到系统 PATH 上的 ffmpeg。

    [AI-NOTE] 2026-05-07: Windows 用户多数没装 ffmpeg；imageio-ffmpeg 是 pip 装得到
    的自带 binary 包，让 Outhan 接力帧抽尾帧不依赖系统 PATH。
    """
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def extract_last_frame(video_path: str | Path, out_png: str | Path) -> dict:
    """从视频抽最后一帧到 PNG。Outhan 接力帧用：上段尾帧 → 下段首帧。

    [AI-NOTE] 2026-05-07: -sseof -0.5 避开末尾压缩花的几帧。专门用 imageio_ffmpeg
    binary 跑（不走 _run_ffmpeg，那个写死 "ffmpeg" PATH lookup）。
    """
    bin_ = _ffmpeg_binary()
    cmd = [bin_, "-hide_banner", "-loglevel", "error", "-y",
           "-sseof", "-0.5",
           "-i", str(video_path),
           "-vframes", "1",
           "-update", "1",
           str(out_png)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as e:
        return {"ok": False, "stderr": f"ffmpeg 未找到：{e}", "cmd": cmd}
    if p.returncode != 0:
        return {"ok": False, "stderr": p.stderr[:500], "cmd": cmd}
    return {"ok": True, "stderr": "", "cmd": cmd}


def probe_duration(media_path: str | Path) -> float:
    """ffprobe 读时长（秒）。"""
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(media_path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(p.stdout)["format"]["duration"])


def _run_ffmpeg(args: list[str], *, timeout: int = 300) -> dict:
    """跑一次 ffmpeg，返回 {ok, stderr, cmd}."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "ok": r.returncode == 0,
            "stderr": (r.stderr or "")[-800:],
            "cmd": " ".join(cmd),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "stderr": "ffmpeg timeout", "cmd": " ".join(cmd)}


# =========================================================================
# 单段合成
# =========================================================================

def render_shot_clip_from_image(
    image_path: str | Path,
    audio_path: str | Path,
    out_path: str | Path,
    *,
    duration: float,
    size: str = "1080x1920",
    fps: int = 30,
) -> dict:
    """图 + 音频 → mp4。图以 cover 模式填满 9:16 画面。
    duration 决定视频时长（通常 = 音频时长）。
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # -loop 1 让单图像循环，-t 设定时长，-c:v libx264 -pix_fmt yuv420p 播放兼容
    vf = (f"scale={size.replace('x', ':')}:force_original_aspect_ratio=increase,"
          f"crop={size.replace('x', ':')},setsar=1,fps={fps}")
    return _run_ffmpeg([
        "-loop", "1", "-i", str(image_path),
        "-i", str(audio_path),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(out),
    ])


def normalize_for_ffmpeg(media_path: str | Path, cache_dir: Path | None = None) -> Path:
    """把 ffmpeg 不友好的格式转成它能解的，**保留 alpha 通道**：
    - animated webp（多帧）→ qtrle .mov（QuickTime Animation, lossless, RGBA 保留）
    - 静态 webp（单帧）→ PNG (RGBA)
    - .gif / .png / .jpg / .mov / .mp4 等 → 原路径返回

    为什么用 qtrle 而非 mp4：
      H.264 只支持 yuv420p 没 alpha，转 mp4 会强制把透明像素填白色，破坏后续 overlay。
      qtrle (Apple Animation Codec) 在 .mov 容器里支持 argb pix_fmt，alpha 完整保留。
      ffmpeg overlay filter 输入流是 .mov 时自动识别 alpha，正确做 over-blending。

    缓存到 cache_dir。
    """
    p = Path(media_path)
    if not p.exists():
        return p
    ext = p.suffix.lower()
    if ext in (".gif", ".png", ".jpg", ".jpeg", ".bmp", ".mp4", ".mov", ".webm", ".m4v"):
        return p
    if ext != ".webp":
        return p
    try:
        from PIL import Image
    except ImportError:
        return p
    out_dir = cache_dir or p.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    h = _hash_url(str(p.resolve()))
    try:
        im = Image.open(p)
    except Exception:
        return p
    n_frames = getattr(im, "n_frames", 1)
    is_animated = getattr(im, "is_animated", False) and n_frames > 1
    if is_animated:
        # animated webp → .mov (qtrle, RGBA preserved) — 关键：alpha 不丢
        out_mov = out_dir / f"{h}.mov"
        if out_mov.exists() and out_mov.stat().st_size > 0:
            return out_mov
        try:
            import tempfile
            durs_ms: list[int] = []
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                for i in range(n_frames):
                    im.seek(i)
                    durs_ms.append(int(im.info.get("duration") or 80))
                    # 关键：RGBA 不要 convert("RGB") —— 保留 alpha
                    rgba = im.convert("RGBA")
                    rgba.save(tmp_dir / f"{i:04d}.png", "PNG")
                avg_dur = sum(durs_ms) / len(durs_ms) if durs_ms else 80
                framerate = max(8.0, min(30.0, 1000.0 / max(avg_dur, 33)))
                r = _run_ffmpeg([
                    "-framerate", f"{framerate:.2f}",
                    "-i", str(tmp_dir / "%04d.png"),
                    "-c:v", "qtrle",
                    "-pix_fmt", "rgba",
                    str(out_mov),
                ])
                if r["ok"] and out_mov.exists() and out_mov.stat().st_size > 0:
                    return out_mov
        except Exception:
            pass  # 失败 → 降级 PNG 首帧
    # 静态或转换失败 → PNG 首帧（PNG 天然支持 RGBA）
    out_png = out_dir / f"{h}.png"
    if out_png.exists() and out_png.stat().st_size > 0:
        return out_png
    try:
        im.seek(0)
        rgba = im.convert("RGBA")
        rgba.save(out_png, "PNG")
        return out_png
    except Exception:
        return p


def render_shot_clip_composite(
    bg_path: str | Path | None,
    ip_path: str | Path | None,
    audio_path: str | Path,
    out_path: str | Path,
    *,
    duration: float,
    size: str = "1080x1920",
    fps: int = 30,
    ip_scale: float = 0.65,    # IP 占画面短边的比例
) -> dict:
    """合成 L0 背景 + L1 IP 表情 + 音频 → mp4。
    - bg_path 缺失：用纯色背景 (#FFF8EC)
    - ip_path 缺失：fallback 到 image-only 渲染（仅背景）
    - ip 通常是 webp/gif 动图，会循环播放至音频时长
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    W, H = (int(x) for x in size.split("x"))
    ip_w = int(min(W, H) * ip_scale)

    # 预处理 IP：webp animated → png 首帧
    if ip_path:
        ip_path = normalize_for_ffmpeg(ip_path, cache_dir=out.parent.parent / "_ip_cache")

    inputs: list[str] = []
    if bg_path:
        inputs.extend(["-loop", "1", "-i", str(bg_path)])
    else:
        # 纯白底（user 指定）。后续真 BG 图接入时改回去 / 删此分支
        inputs.extend(["-f", "lavfi", "-i", f"color=c=#FFFFFF:s={W}x{H}:r={fps}"])
    bg_idx = 0
    next_idx = 1
    if ip_path:
        ip_ext = Path(str(ip_path)).suffix.lower()
        if ip_ext in (".gif", ".mp4", ".webm", ".mov"):
            # 动画源 → stream_loop 让它循环到 -t 时长
            inputs.extend(["-stream_loop", "-1", "-i", str(ip_path)])
        else:
            # 静态图 → -loop 1
            inputs.extend(["-loop", "1", "-i", str(ip_path)])
        ip_idx = next_idx
        next_idx += 1
    else:
        ip_idx = None
    inputs.extend(["-i", str(audio_path)])
    audio_idx = next_idx

    fc_parts = [
        f"[{bg_idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},setsar=1,fps={fps}[bg]"
    ]
    if ip_idx is not None:
        fc_parts.append(
            f"[{ip_idx}:v]scale={ip_w}:{ip_w}:force_original_aspect_ratio=decrease,"
            f"format=rgba[ip]"
        )
        fc_parts.append(f"[bg][ip]overlay=(W-w)/2:(H-h)/2+50:format=auto[out]")
        map_video = "[out]"
    else:
        map_video = "[bg]"
    fc = ";".join(fc_parts)

    args = [*inputs,
            "-filter_complex", fc,
            "-map", map_video,
            "-map", f"{audio_idx}:a:0",
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-pix_fmt", "yuv420p",
            "-shortest",
            str(out)]
    return _run_ffmpeg(args)


def render_beat_clip(
    bg_path: str | Path | None,
    left_path: str | Path | None,
    right_path: str | Path | None,
    audio_path: str | Path,
    out_path: str | Path,
    *,
    duration: float,
    audio_offset: float = 0.0,
    size: str = "1080x1920",
    fps: int = 30,
    slot_scale: float = 0.42,         # 单槽素材短边占画面短边比例（双素材并列时）
    center_scale: float = 0.62,       # 单素材居中（layout="center"）时的占比
    layout: str = "left_right",        # "left_right" | "center"
) -> dict:
    """渲染一个 beat：BG + 左槽 + 右槽 + audio 切片 → mp4。
    - left_path / right_path 任一为 None：自动 fallback 到 center 布局只显示有的那个
    - audio_offset：从源 audio 的哪个秒开始截取（用于把整段 shot audio 按 beat 切片）
    - 视觉素材：webp/gif/mp4/mov 都接，自动 normalize
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    W, H = (int(x) for x in size.split("x"))

    # 预处理：webp animated → qtrle .mov（保 alpha）
    cache_dir = out.parent.parent / "_ip_cache"
    if left_path:
        left_path = normalize_for_ffmpeg(left_path, cache_dir=cache_dir)
    if right_path:
        right_path = normalize_for_ffmpeg(right_path, cache_dir=cache_dir)

    # 任一缺失 → center 布局只显示存在的那个
    has_left = bool(left_path)
    has_right = bool(right_path)
    if not (has_left and has_right):
        layout = "center"
        # 把存在的那个塞 left_path（中央位置使用）
        if has_right and not has_left:
            left_path, right_path = right_path, None
            has_left, has_right = True, False

    # 单素材居中尺寸；双素材并列尺寸
    slot_w = int(min(W, H) * (center_scale if layout == "center" else slot_scale))

    inputs: list[str] = []
    if bg_path:
        inputs.extend(["-loop", "1", "-i", str(bg_path)])
    else:
        inputs.extend(["-f", "lavfi", "-i", f"color=c=#FFFFFF:s={W}x{H}:r={fps}"])
    bg_idx = 0
    next_idx = 1

    left_idx = right_idx = None
    if has_left:
        ext = Path(str(left_path)).suffix.lower()
        if ext in (".gif", ".mp4", ".webm", ".mov"):
            inputs.extend(["-stream_loop", "-1", "-i", str(left_path)])
        else:
            inputs.extend(["-loop", "1", "-i", str(left_path)])
        left_idx = next_idx; next_idx += 1
    if has_right:
        ext = Path(str(right_path)).suffix.lower()
        if ext in (".gif", ".mp4", ".webm", ".mov"):
            inputs.extend(["-stream_loop", "-1", "-i", str(right_path)])
        else:
            inputs.extend(["-loop", "1", "-i", str(right_path)])
        right_idx = next_idx; next_idx += 1

    # audio 用 -ss audio_offset 切起点
    audio_args = ["-ss", f"{audio_offset:.3f}", "-i", str(audio_path)]
    inputs.extend(audio_args)
    audio_idx = next_idx

    # filter_complex 构造
    fc_parts = [
        f"[{bg_idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},setsar=1,fps={fps}[bg]"
    ]
    if layout == "center":
        # 只有 left_idx（已被规整化）
        fc_parts.append(
            f"[{left_idx}:v]scale={slot_w}:{slot_w}:force_original_aspect_ratio=decrease,"
            f"format=rgba[ip]"
        )
        # 居中略偏下（与原 composite 一致）
        fc_parts.append(f"[bg][ip]overlay=(W-w)/2:(H-h)/2+50:format=auto[out]")
    else:
        # left_right 双素材并列：左槽中心 x = W*0.28，右槽中心 x = W*0.72，y 居中
        fc_parts.append(
            f"[{left_idx}:v]scale={slot_w}:{slot_w}:force_original_aspect_ratio=decrease,"
            f"format=rgba[lf]"
        )
        fc_parts.append(
            f"[{right_idx}:v]scale={slot_w}:{slot_w}:force_original_aspect_ratio=decrease,"
            f"format=rgba[rt]"
        )
        # 左：x = W*0.28 - w/2 = W*0.28 - slot_w/2
        # 右：x = W*0.72 - w/2
        # y 垂直居中：(H - h)/2
        x_left = int(W * 0.28 - slot_w / 2)
        x_right = int(W * 0.72 - slot_w / 2)
        y_center = int((H - slot_w) / 2)
        fc_parts.append(f"[bg][lf]overlay={x_left}:{y_center}:format=auto[bg_l]")
        fc_parts.append(f"[bg_l][rt]overlay={x_right}:{y_center}:format=auto[out]")
    fc = ";".join(fc_parts)

    args = [*inputs,
            "-filter_complex", fc,
            "-map", "[out]",
            "-map", f"{audio_idx}:a:0",
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-pix_fmt", "yuv420p",
            "-shortest",
            str(out)]
    return _run_ffmpeg(args)


def render_shot_clip_from_video(
    video_path: str | Path,
    audio_path: str | Path,
    out_path: str | Path,
    *,
    duration: float,
    size: str = "1080x1920",
    fps: int = 30,
) -> dict:
    """已有视频 + 音频 → 替换音轨、统一尺寸、截到 duration。"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = (f"scale={size.replace('x', ':')}:force_original_aspect_ratio=increase,"
          f"crop={size.replace('x', ':')},setsar=1,fps={fps}")
    # 视频太短循环、太长截断
    return _run_ffmpeg([
        "-stream_loop", "-1", "-i", str(video_path),
        "-i", str(audio_path),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        str(out),
    ])


# =========================================================================
# 合并 + 字幕
# =========================================================================

def concat_clips(clip_paths: list[str | Path], out_path: str | Path) -> dict:
    """用 concat demuxer 把多个片段连成一个 mp4（要求所有片段编码一致）。"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 写 concat list 文件
    list_file = out.parent / f"{out.stem}.concat.txt"
    list_file.write_text(
        "\n".join(f"file '{Path(p).resolve()}'" for p in clip_paths),
        encoding="utf-8",
    )
    return _run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy",
        str(out),
    ])


def mix_bgm(
    video_path: str | Path,
    bgm_path: str | Path,
    out_path: str | Path,
    *,
    bgm_volume: float = 0.18,    # BGM 音量（0-1），0.18 ≈ -15dB
    fade_in: float = 0.5,         # BGM 淡入秒数
    fade_out: float = 0.8,        # BGM 淡出秒数
) -> dict:
    """把 BGM 与视频原音轨混合。BGM 自动循环 + 截到视频时长 + 淡入淡出。
    原配音保持原音量，BGM 减小到 bgm_volume 让人声清晰。
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # filter_complex:
    # [1:a] = BGM stream，stream_loop=-1 让短 BGM 循环
    # afade 淡入淡出 + volume 减小 + amix 与原音 [0:a] 混合
    fc = (
        f"[1:a]aloop=loop=-1:size=2e9,volume={bgm_volume},"
        f"afade=t=in:st=0:d={fade_in},"
        # 淡出基于视频总时长，让 BGM 最后 N 秒淡出（用 -shortest 截到视频时长）
        f"asetpts=PTS-STARTPTS[bgm];"
        f"[0:a][bgm]amix=inputs=2:duration=shortest:dropout_transition=0:weights=1.0 1.0[aout]"
    )
    return _run_ffmpeg([
        "-i", str(video_path),
        "-stream_loop", "-1", "-i", str(bgm_path),
        "-filter_complex", fc,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out),
    ])


def burn_subtitles(mp4_path: str | Path, srt_path: str | Path,
                   out_path: str | Path, *, style: str | None = None) -> dict:
    """把 SRT 烧进视频。style 用 ASS 力度（FontName=... PrimaryColour=... Outline=...）。"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # ffmpeg subtitles filter 要求路径转义（冒号 / 反斜杠）
    srt_escaped = str(srt_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    style_args = f":force_style='{style}'" if style else ""
    vf = f"subtitles='{srt_escaped}'{style_args}"
    return _run_ffmpeg([
        "-i", str(mp4_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "copy",
        str(out),
    ])


# =========================================================================
# SRT 生成
# =========================================================================

def _sec_to_srt(s: float) -> str:
    if s < 0:
        s = 0
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    ms = int((s - int(s)) * 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def split_text_to_cues(text: str, start: float, end: float,
                       *, max_chars: int = 18, min_chars: int = 8) -> list[dict]:
    """把单段文本按 标点 + 字数切成多条字幕，时长按字数比例分配。

    断句原则（避免"AAA"被切成"AA"+"A"这种破碎）：
    1. 按标点切
    2. 短段（< min_chars）合并到相邻段，宁可超 max_chars 也保完整意思
    3. 长段硬切时，避免在词中间切（找最近的标点；找不到看 1-3 字常见组合边界）
    4. 硬切后短尾巴 < min_chars 吞回前一条
    返回 [{"start", "end", "text"}, ...]，时间总和 = end - start
    """
    text = (text or "").strip()
    if not text:
        return []
    duration = max(0.0, end - start)
    if duration <= 0:
        return [{"start": start, "end": end, "text": text}]

    PUNCT = "，。！？!?；;,、"
    # 1. 按标点切
    pieces: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in PUNCT:
            if buf.strip():
                pieces.append(buf.strip())
            buf = ""
    if buf.strip():
        pieces.append(buf.strip())
    if not pieces:
        pieces = [text]

    # 2. 合并：短段（< min_chars）和上下文连成完整意思
    merged: list[str] = []
    for p in pieces:
        clean = p.rstrip(PUNCT)
        if not clean:
            continue
        # 当前段过短 OR 上一段过短 → 合并（即使超 max_chars，意思完整优先）
        if merged and (len(clean) < min_chars or len(merged[-1].rstrip(PUNCT)) < min_chars):
            merged[-1] = merged[-1] + p
        # 总长仍在 max_chars 弹性范围内（max_chars+4 容许）→ 也合并
        elif merged and len(merged[-1]) + len(p) <= max_chars + 4:
            merged[-1] = merged[-1] + p
        else:
            merged.append(p)

    # 3. 硬切过长（>max_chars * 1.5 才切，给一定弹性）
    final: list[str] = []
    HARD_CAP = int(max_chars * 1.5)  # 27 字才硬切
    for p in merged:
        s = p
        while len(s) > HARD_CAP:
            # 在 max_chars 附近找最佳断点：先标点 → 再 ASCII 数字/字母词边界
            cut = max_chars
            best = None
            for i in range(min(len(s), max_chars + 4), max(min_chars, max_chars - 4), -1):
                if i <= 0 or i > len(s):
                    continue
                # 优先标点后断
                if s[i - 1] in PUNCT:
                    best = i
                    break
                # 其次：ASCII 词边界（避免把 "AAA" 切开 / 把 "10000" 切开）
                if best is None and i < len(s):
                    prev_ascii = s[i - 1].isascii() and (s[i - 1].isalnum())
                    next_ascii = s[i].isascii() and (s[i].isalnum())
                    if not (prev_ascii and next_ascii):
                        best = i
            if best is not None:
                cut = best
            piece = s[:cut].strip()
            if piece:
                final.append(piece)
            s = s[cut:].strip()
        if s:
            final.append(s)

    # 4. 短尾巴 < min_chars 吞回前一条
    if len(final) >= 2 and len(final[-1].rstrip(PUNCT)) < min_chars:
        final[-2] = final[-2] + final[-1]
        final.pop()

    if not final:
        return [{"start": start, "end": end, "text": text}]

    # 4. 按字数比例分配时长
    total_chars = sum(len(x.rstrip(PUNCT)) or 1 for x in final)
    out: list[dict] = []
    cursor = start
    for i, piece in enumerate(final):
        if i == len(final) - 1:
            seg_end = end
        else:
            chars = len(piece.rstrip(PUNCT)) or 1
            seg_end = cursor + duration * chars / total_chars
        out.append({"start": cursor, "end": seg_end, "text": piece.rstrip(PUNCT) or piece})
        cursor = seg_end
    return out


# FontSize=108 · PlayResY=1920 · 1080px宽 → 单行视觉容量上限
SUBTITLE_MAX_VISUAL_WIDTH: float = 12.0


def _subtitle_visual_width(text: str) -> float:
    """估算字幕视觉宽度（以CJK字符为单位）。ASCII字符约半宽，CJK字符全宽。"""
    return sum(0.55 if c.isascii() else 1.0 for c in text)


def _subtitle_split_lines(text: str, max_vw: float = SUBTITLE_MAX_VISUAL_WIDTH,
                          min_chars: int = 3) -> list[str]:
    """把一段文本按视觉宽度拆成多个单行 chunk（不截断，不省略）。
    规则：
    - 每 chunk 视觉宽度 ≤ max_vw
    - 不在 ASCII 字母/数字序列中间断开（"Robinhood"/"100" 整体保留）
    - 每个 chunk 实际字符数 ≥ min_chars（避免单字/双字孤立成行）
      → 不足 min_chars 的 chunk 合并回前一个（允许前一个轻微超宽）
    """
    text = text.strip().replace("\n", " ").replace("\r", "")
    if _subtitle_visual_width(text) <= max_vw:
        return [text]

    lines: list[str] = []
    cur = ""
    w = 0.0
    i = 0
    chars = list(text)

    while i < len(chars):
        ch = chars[i]
        # 若当前字符是 ASCII 字母/数字，先把整个 ASCII token 取出
        if ch.isascii() and ch.isalnum():
            token = ""
            j = i
            while j < len(chars) and chars[j].isascii() and chars[j].isalnum():
                token += chars[j]
                j += 1
            tw = _subtitle_visual_width(token)
            if w + tw > max_vw and cur:
                lines.append(cur.rstrip())
                cur = ""
                w = 0.0
            cur += token
            w += tw
            i = j
        else:
            cw = 0.55 if ch.isascii() else 1.0
            if w + cw > max_vw and cur:
                lines.append(cur.rstrip())
                cur = ch
                w = cw
            else:
                cur += ch
                w += cw
            i += 1

    if cur:
        lines.append(cur.rstrip())

    if not lines:
        return [text]

    # 合并 < min_chars 的 chunk 到前一个（从后往前扫，处理末尾短块）
    merged: list[str] = []
    for chunk in lines:
        if merged and len(chunk) < min_chars:
            # 并入上一个，允许轻微超宽
            merged[-1] = merged[-1] + chunk
        else:
            merged.append(chunk)

    # 若第一个 chunk 自身也 < min_chars 且后面还有，并入第二个
    if len(merged) >= 2 and len(merged[0]) < min_chars:
        merged[1] = merged[0] + merged[1]
        merged = merged[1:]

    return merged


def _wrap_subtitle_text(text: str, max_visual_width: float = SUBTITLE_MAX_VISUAL_WIDTH) -> str:
    """字幕强制单行（供 compose_srt 的 fallback 路径调用）。
    长于单行的文本只返回第一行，剩余部分由调用方拆成多个 SRT cue。
    """
    lines = _subtitle_split_lines(text, max_visual_width)
    return lines[0] if lines else text


def compose_srt(segments: list[dict]) -> str:
    """segments: [{"start": 秒, "end": 秒, "text": "..."}, ...]  → SRT 文本.
    文本超出单行视觉宽度时，自动拆成多条顺序 cue（等分时长），保证每条只有一行。
    """
    # 先把 segments 展开：长文本 → 多条单行 cue
    expanded: list[dict] = []
    for seg in segments:
        text = seg["text"].strip()
        chunks = _subtitle_split_lines(text)
        if len(chunks) <= 1:
            expanded.append(seg)
        else:
            # 等分时长
            dur = seg["end"] - seg["start"]
            chunk_dur = dur / len(chunks)
            t = seg["start"]
            for chunk in chunks:
                expanded.append({"start": round(t, 3), "end": round(t + chunk_dur, 3), "text": chunk})
                t += chunk_dur

    lines = []
    for i, seg in enumerate(expanded, start=1):
        lines.append(str(i))
        lines.append(f"{_sec_to_srt(seg['start'])} --> {_sec_to_srt(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")
    return "\n".join(lines)
