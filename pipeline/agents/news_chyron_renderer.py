"""ZNN/CNN 风新闻栏目条渲染器 · PIL 画整张装饰层 PNG。

ffmpeg drawbox 画不出斜角块、圆角、阴影、渐变——所以 ZNN 风必须走 PIL：
  1. PIL 在视频尺寸的透明 RGBA 画布上画完整 chyron 装饰层
  2. 主条 / 副条 / 左上红斜块 / 右上台标斜块 / 时间码 / 装饰线 一次画完
  3. 输出 PNG 给 ffmpeg overlay 用

调用方负责 ffmpeg overlay 部分（含动效控制）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


# ── 字体探测（macOS 系统路径优先；找不到 fallback PingFang） ──

_FONT_PATHS_BOLD_LATIN = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
]
_FONT_PATHS_PINGFANG_BOLD = [
    "/System/Library/Fonts/PingFang.ttc",
]


def _load_font(paths: list[str], size: int, index: int = 0) -> ImageFont.FreeTypeFont:
    for p in paths:
        if Path(p).exists():
            try:
                if p.endswith(".ttc"):
                    return ImageFont.truetype(p, size, index=index)
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    # 真兜底
    try:
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
    except Exception:
        return ImageFont.load_default()


def _font_latin_bold(size: int) -> ImageFont.FreeTypeFont:
    return _load_font(_FONT_PATHS_BOLD_LATIN, size)


def _font_cjk_bold(size: int) -> ImageFont.FreeTypeFont:
    # PingFang.ttc index 1=Medium, 3=Semibold, 4=Bold, 5=Heavy；用 Heavy 最厚重
    return _load_font(_FONT_PATHS_PINGFANG_BOLD, size, index=5)


def _hex_to_rgba(hex_str: str, alpha: int = 255) -> tuple[int, int, int, int]:
    s = hex_str.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), alpha)


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_loader,
    *,
    target_size: int,
    max_w: int,
    min_size: int = 16,
) -> tuple[ImageFont.FreeTypeFont, int, int, int]:
    """从 target_size 开始尝试，测量超宽就按 0.9 缩字号循环直到 fit max_w 或 min_size。
    返回 (font, font_size, text_w, text_h)。
    """
    fs = target_size
    font = font_loader(fs)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    while tw > max_w and fs > min_size:
        fs = max(min_size, int(fs * 0.9))
        font = font_loader(fs)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    return font, fs, tw, th


def render_znn_chyron(
    *,
    profile: dict,
    label: str,         # 左上斜块（LIVE / DEVELOPING / 突发）
    title_main: str,    # 主条大字（英文优先，回退中文）
    title_sub: str,     # 副条文字（中文）
    station: str,       # 右上斜块（台标 ZNN）
    time_str: str,      # 右下时间（6:01 PM）
    video_w: int,
    video_h: int,
    out_path: Path,
) -> dict[str, Any]:
    """画完整 ZNN 风 chyron，输出 PNG (与视频同尺寸的透明叠加层)。"""

    bar_h_ratio = float(profile.get("bar_height_ratio", 0.22))
    chyron_h = int(video_h * bar_h_ratio)
    main_h = int(chyron_h * 0.55)
    sub_h = chyron_h - main_h
    main_y = video_h - main_h
    sub_y = main_y - sub_h

    img = Image.new("RGBA", (video_w, video_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")

    main_bg = _hex_to_rgba(profile.get("main_bg_hex", "FAFAFA"))
    main_text = _hex_to_rgba(profile.get("main_text_hex", "111111"))
    sub_bg = _hex_to_rgba(profile.get("sub_bg_hex", "0A2E5C"))
    sub_text = _hex_to_rgba(profile.get("sub_text_hex", "FFFFFF"))
    accent_bg = _hex_to_rgba(profile.get("accent_bg_hex", "D11A2A"))
    accent_text = _hex_to_rgba(profile.get("accent_text_hex", "FFFFFF"))
    accent_line = _hex_to_rgba(profile.get("accent_line_hex", "D4A04A"))
    time_color = _hex_to_rgba(profile.get("time_color_hex", "FFC845"))
    shadow = (0, 0, 0, 90)

    # ─── 1. 主白条（带顶部阴影） ───
    # 阴影：在白条上方 8px 渐变（用半透明矩形多次叠加模拟）
    shadow_h = 12
    for i in range(shadow_h):
        a = int(60 * (1 - i / shadow_h))
        draw.rectangle(
            [(0, main_y - shadow_h + i), (video_w, main_y - shadow_h + i + 1)],
            fill=(0, 0, 0, a),
        )
    draw.rectangle([(0, main_y), (video_w, video_h)], fill=main_bg)

    # ─── 2. 副条深蓝 ───
    draw.rectangle([(0, sub_y), (video_w, main_y)], fill=sub_bg)
    # 副条顶部金色细线（设计强调）
    line_h = max(2, int(sub_h * 0.04))
    draw.rectangle([(0, sub_y), (video_w, sub_y + line_h)], fill=accent_line)

    # ─── 3. 左上红色斜块（LIVE / DEVELOPING） ───
    label_block_right_x = 0     # 副标题左边界用
    if label:
        # 让 block 宽度跟 label 字数自适应
        label_safe = (label or "").upper()[:14]
        block_w_min = int(video_w * 0.22)
        block_w_max = int(video_w * 0.34)
        # 先估算最大字号下文字宽度，决定 block_w
        block_h = sub_h
        slant = int(block_h * 0.42)
        # 先尝试目标字号 sub_h * 0.46
        target_fs = int(block_h * 0.46)
        # 字号自适应到 fit (block_w_max - slant - 内边距)
        max_text_w = block_w_max - slant - int(video_w * 0.04)
        label_font, label_fs, label_tw, _ = _fit_text(
            draw, label_safe, _font_latin_bold,
            target_size=target_fs, max_w=max_text_w, min_size=20,
        )
        # 真实 block_w = 文字宽度 + 左右 padding + slant
        block_w = max(block_w_min, label_tw + int(video_w * 0.06) + slant)
        block_w = min(block_w, block_w_max)

        block_x = 0
        block_y = sub_y
        polygon = [
            (block_x, block_y),
            (block_x + block_w, block_y),
            (block_x + block_w - slant, block_y + block_h),
            (block_x, block_y + block_h),
        ]
        draw.polygon(polygon, fill=accent_bg)
        # 左侧细白竖条
        bar_w = max(6, int(video_w * 0.006))
        draw.rectangle(
            [(block_x, block_y), (block_x + bar_w, block_y + block_h)],
            fill=(255, 255, 255, 255),
        )
        # label 文字（垂直居中，水平在 block 平行四边形内）
        text_avail_w = block_w - slant - bar_w - int(video_w * 0.025)
        tx = block_x + bar_w + int(video_w * 0.018)
        ty = block_y + (block_h - label_fs) // 2 - int(label_fs * 0.12)
        draw.text((tx, ty), label_safe, font=label_font, fill=accent_text)
        # 记录红斜块"右下"边界（副标题用）
        label_block_right_x = block_x + block_w

    # ─── 4. 右上红色斜块（台标 ZNN） ───
    station_block_left_x = video_w
    if station:
        stn_safe = (station or "").upper()[:6]
        block_w_min = int(video_w * 0.14)
        block_w_max = int(video_w * 0.24)
        block_h = sub_h
        slant = int(block_h * 0.42)
        target_fs = int(block_h * 0.56)
        max_text_w = block_w_max - slant - int(video_w * 0.03)
        stn_font, stn_fs, stn_tw, _ = _fit_text(
            draw, stn_safe, _font_latin_bold,
            target_size=target_fs, max_w=max_text_w, min_size=20,
        )
        block_w = max(block_w_min, stn_tw + int(video_w * 0.06) + slant)
        block_w = min(block_w, block_w_max)
        block_x = video_w - block_w
        block_y = sub_y
        polygon = [
            (block_x + slant, block_y),
            (block_x + block_w, block_y),
            (block_x + block_w, block_y + block_h),
            (block_x, block_y + block_h),
        ]
        draw.polygon(polygon, fill=accent_bg)
        tx = block_x + slant + ((block_w - slant) - stn_tw) // 2
        ty = block_y + (block_h - stn_fs) // 2 - int(stn_fs * 0.12)
        draw.text((tx, ty), stn_safe, font=stn_font, fill=accent_text)
        # 记录台标斜块"左上"边界
        station_block_left_x = block_x + slant

    # ─── 5. 副条中文副标题（在两个斜块之间） ───
    if title_sub:
        sub_safe = title_sub[:24]
        left_pad = label_block_right_x + int(video_w * 0.02)
        right_pad = station_block_left_x - int(video_w * 0.02)
        avail_w = max(50, right_pad - left_pad)
        target_fs = int(sub_h * 0.46)
        sub_font, sub_fs, sub_tw, _ = _fit_text(
            draw, sub_safe, _font_cjk_bold,
            target_size=target_fs, max_w=avail_w, min_size=20,
        )
        tx = left_pad + (avail_w - sub_tw) // 2
        ty = sub_y + (sub_h - sub_fs) // 2 - int(sub_fs * 0.10)
        draw.text((tx, ty), sub_safe, font=sub_font, fill=sub_text)

    # ─── 6. 右下时间码（先画 · 给主标题让位） ───
    time_left_x = video_w
    if time_str:
        target_fs = int(main_h * 0.42)
        time_font, time_fs, time_tw, _ = _fit_text(
            draw, time_str, _font_latin_bold,
            target_size=target_fs, max_w=int(video_w * 0.22), min_size=20,
        )
        tx = video_w - time_tw - int(video_w * 0.025)
        ty = main_y + (main_h - time_fs) // 2 - int(time_fs * 0.12)
        draw.text((tx, ty), time_str, font=time_font, fill=time_color)
        time_left_x = tx - int(video_w * 0.015)

    # ─── 7. 主条主标题（白底大字，给时间码让位） ───
    if title_main:
        is_latin = _is_mostly_ascii(title_main)
        main_safe = title_main[:40].upper() if is_latin else title_main[:18]
        font_loader = _font_latin_bold if is_latin else _font_cjk_bold
        target_fs = int(main_h * (0.62 if is_latin else 0.55))
        left_pad = int(video_w * 0.025)
        avail_w = max(100, time_left_x - left_pad)
        main_font, main_fs, main_tw, _ = _fit_text(
            draw, main_safe, font_loader,
            target_size=target_fs, max_w=avail_w, min_size=24,
        )
        tx = left_pad + (avail_w - main_tw) // 2
        ty = main_y + (main_h - main_fs) // 2 - int(main_fs * 0.12)
        draw.text((tx, ty), main_safe, font=main_font, fill=main_text)

    img.save(out_path, "PNG")
    return {"ok": True, "out_path": str(out_path), "chyron_h": chyron_h}


def _is_mostly_ascii(s: str) -> bool:
    if not s:
        return True
    n_ascii = sum(1 for c in s if ord(c) < 128)
    return n_ascii / len(s) > 0.6
