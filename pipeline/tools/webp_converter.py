"""把 gif/mp4/webm 等格式统一转为 webp，并探测是否带 alpha 通道。

依赖系统 ffmpeg / ffprobe（macOS 用 brew install ffmpeg）。

核心场景：从 Tenor 抓回的素材格式不一，要落到 IP 库前统一成 webp，
让视频合成时透明背景能正确融入。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


# 带 alpha 通道的 ffmpeg pixel format 关键词
_ALPHA_PIX_FMTS = {
    "yuva420p", "yuva422p", "yuva444p",
    "rgba", "bgra", "argb", "abgr",
    "rgba64", "bgra64",
    "pal8",  # gif 调色板 + 透明索引
}


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe_format(path: str | Path) -> dict[str, Any]:
    """探测文件实际格式 + 是否带 alpha。

    返回 {ok, container, codec, pix_fmt, has_alpha, width, height, duration, error}
    """
    path = Path(path)
    if not path.exists():
        return {"ok": False, "error": f"file not found: {path}"}
    if not ffmpeg_available():
        return {"ok": False, "error": "ffmpeg/ffprobe not installed"}
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_streams", "-show_format",
                "-print_format", "json",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"ok": False, "error": f"ffprobe failed: {e}"}
    if proc.returncode != 0:
        return {"ok": False, "error": f"ffprobe rc={proc.returncode}: {proc.stderr[:200]}"}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"ffprobe json decode: {e}"}
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        return {"ok": False, "error": "no video stream"}
    s = streams[0]
    pix_fmt = (s.get("pix_fmt") or "").lower()
    has_alpha = pix_fmt in _ALPHA_PIX_FMTS
    # ffprobe 对 animated webp 不暴露 pix_fmt——用 PIL 兜底
    if not has_alpha and path.suffix.lower() == ".webp":
        try:
            from PIL import Image
            with Image.open(path) as im:
                has_alpha = im.mode in ("RGBA", "LA") or "A" in im.mode \
                    or im.info.get("transparency") is not None
        except (ImportError, OSError):
            pass
    fmt = data.get("format", {})
    container = (fmt.get("format_name") or "").split(",")[0]
    duration = None
    try:
        duration = float(fmt.get("duration") or s.get("duration") or 0) or None
    except (TypeError, ValueError):
        pass
    return {
        "ok": True,
        "container": container,
        "codec": s.get("codec_name"),
        "pix_fmt": pix_fmt,
        "has_alpha": has_alpha,
        "width": s.get("width"),
        "height": s.get("height"),
        "duration": duration,
        "error": None,
    }


def to_webp(
    src: str | Path,
    dst: str | Path,
    *,
    quality: int = 80,
    overwrite: bool = False,
    preserve_alpha: bool = True,
) -> dict[str, Any]:
    """转码源文件到 dst（.webp）。

    - 已经是 webp 且 dst==src：探测后直接返回
    - gif/mp4/webm → animated webp，preserve_alpha 时尽量保留透明通道
    - quality: 0-100
    """
    src, dst = Path(src), Path(dst)
    if not src.exists():
        return {"ok": False, "error": f"src not found: {src}"}
    if not ffmpeg_available():
        return {"ok": False, "error": "ffmpeg not installed"}

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not overwrite and dst.stat().st_size > 0:
        info = probe_format(dst)
        return {"ok": True, "dst": str(dst), "skipped": True,
                "has_alpha": info.get("has_alpha", False),
                "format": "webp", "error": None}

    src_info = probe_format(src)
    if not src_info["ok"]:
        return {"ok": False, "error": f"probe src failed: {src_info['error']}"}

    src_has_alpha = src_info["has_alpha"]
    is_already_webp = src.suffix.lower() == ".webp" and src_info.get("codec") in ("webp",)

    if is_already_webp and src.resolve() != dst.resolve():
        # 直接复制
        shutil.copy2(src, dst)
        return {"ok": True, "dst": str(dst), "skipped": False,
                "has_alpha": src_has_alpha, "format": "webp", "error": None}
    if is_already_webp:
        return {"ok": True, "dst": str(dst), "skipped": True,
                "has_alpha": src_has_alpha, "format": "webp", "error": None}

    # ffmpeg 命令：动图 webp，loop=0 永远循环，quality 经验值 80
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if preserve_alpha and src_has_alpha:
        cmd += ["-vf", "format=yuva420p"]
    cmd += [
        "-c:v", "libwebp_anim",  # animated webp encoder（ffmpeg 内置）
        "-loop", "0",
        "-quality", str(quality),
        "-preset", "default",
        "-an",  # 去音轨
        str(dst),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"ok": False, "error": f"ffmpeg failed: {e}"}
    if proc.returncode != 0:
        # libwebp_anim 在某些 ffmpeg build 不存在，fallback libwebp
        cmd_fb = ["ffmpeg", "-y", "-i", str(src), "-c:v", "libwebp",
                  "-loop", "0", "-quality", str(quality), "-an", str(dst)]
        if preserve_alpha and src_has_alpha:
            cmd_fb.insert(-1, "-vf")
            cmd_fb.insert(-1, "format=yuva420p")
        try:
            proc = subprocess.run(cmd_fb, capture_output=True, text=True, timeout=120)
        except (subprocess.TimeoutExpired, OSError) as e:
            return {"ok": False, "error": f"ffmpeg fallback failed: {e}"}
        if proc.returncode != 0:
            return {"ok": False,
                    "error": f"ffmpeg rc={proc.returncode}: {proc.stderr[:300]}"}

    if not dst.exists() or dst.stat().st_size == 0:
        return {"ok": False, "error": "ffmpeg produced empty output"}

    out_info = probe_format(dst)
    return {
        "ok": True, "dst": str(dst), "skipped": False,
        "has_alpha": out_info.get("has_alpha", False),
        "format": "webp",
        "error": None,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("usage: python -m pipeline.tools.webp_converter <src> <dst> [quality]")
        sys.exit(2)
    q = int(sys.argv[3]) if len(sys.argv) > 3 else 80
    info = probe_format(sys.argv[1])
    print(f"src probe: {info}")
    res = to_webp(sys.argv[1], sys.argv[2], quality=q)
    print(f"convert: {res}")
