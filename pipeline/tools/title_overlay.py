"""标题牌叠加工具 — badge-overlay v3  双色温自适应配色

设计语言：与背景融合 · 极简线条 · 哑光玻璃质感 · 细腻投影 · 冷暖两套配色
──────────────────────────────────────────────────────────────────────
层次（底→顶）:
  1. 背景原图
  2. 超软阴影   — 胶囊偏移，GaussianBlur r=24，颜色取自背景装饰色，极低透明度
  3. 色温光晕   — 胶囊外扩 12px，匹配背景色温，GaussianBlur r=10，极低透明度
  4. 外框线     — 1.5px 细线，哑光色温边框色
  5. 内腔填充   — 极浅径向渐变（近白→轻微色温晕染）
  6. 细装饰线   — 文字两侧各一根短横线 + 小圆点，与边框同色
  7. 文字       — PingFang Medium，深色（与色温匹配），CJK 间距，极轻阴影

色温配色:
  warm → 玫瑰金系（匹配暖米色背景）
  cool → 雾霾蓝系（匹配冷蓝白背景）

坐标基准: 1080×1920 画布（自动按实际背景尺寸等比缩放）
──────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ================================================================
# 胶囊规格（基准 1080×1920）
# ================================================================
_CW, _CH = 1080, 1920

_BL, _BR = 90, 990        # left / right  → 宽 900px
_BT, _BB = 210, 430       # top / bottom  → 高 220px
_BR_RADIUS = (_BB - _BT) // 2   # = 110，完整 pill

# ================================================================
# 双色温配色方案
# ================================================================
_SCHEMES: dict[str, dict] = {
    "warm": {
        # 暖米色背景配套 — 玫瑰金 · 杏仁奶 · 深棕
        "shadow":       (165, 100,  72, 38),   # 玫瑰棕阴影，极淡
        "glow":         (232, 204, 186, 22),   # 杏仁光晕，几乎看不见
        "border":       "#C4957A",             # 哑光玫瑰金边框
        "sep":          "#FEF9F6",             # 极淡暖白隔离
        "fill_center":  np.array([254, 252, 249], dtype=np.float32),  # 近白奶油
        "fill_edge":    np.array([248, 234, 223], dtype=np.float32),  # 浅杏桃
        "deco":         "#C4957A",             # 装饰线：玫瑰金
        "text":         "#3D1F10",             # 深暖棕文字
        "text_shadow":  (175, 110,  75, 60),   # 玫瑰棕文字阴影
    },
    "cool": {
        # 冷蓝白背景配套 — 雾霾蓝 · 冰白 · 深海军蓝
        "shadow":       (118, 152, 192, 38),   # 雾蓝阴影，极淡
        "glow":         (188, 215, 248, 22),   # 冰蓝光晕，几乎看不见
        "border":       "#7AAAC8",             # 哑光雾霾蓝边框
        "sep":          "#F5FAFF",             # 极淡冷白隔离
        "fill_center":  np.array([250, 253, 255], dtype=np.float32),  # 近白冰蓝
        "fill_edge":    np.array([224, 238, 253], dtype=np.float32),  # 浅天蓝
        "deco":         "#7AAAC8",             # 装饰线：雾霾蓝
        "text":         "#1A2D42",             # 深海军蓝文字
        "text_shadow":  ( 85, 125, 175, 60),   # 蓝调文字阴影
    },
}

# 布局参数（基准 px）
_BORDER_W    = 3     # 外框厚度
_SEP_W       = 2     # 白色隔离厚度
_TEXT_TRACK  = 10    # CJK 字间距
_DECO_LINE_W = 28    # 装饰横线长度
_DECO_DOT_R  = 4     # 装饰端点圆半径
_DECO_GAP    = 22    # 装饰线距胶囊内边缘
_TEXT_GAP    = 14    # 装饰线与文字之间的间距（含 dot）


# ================================================================
# 字体加载
# ================================================================
_FONT_CANDIDATES = [
    ("/System/Library/Fonts/PingFang.ttc",          2),
    ("/System/Library/Fonts/PingFang.ttc",          1),
    ("/System/Library/Fonts/STHeiti Medium.ttc",    0),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc",  1),
    ("/Library/Fonts/Songti.ttc",                   0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",      0),
    ("C:/Windows/Fonts/msyhbd.ttc",  0),
    ("C:/Windows/Fonts/msyh.ttc",    0),
]


def _resolve_font() -> tuple[str, int]:
    env = os.environ.get("TITLE_FONT_PATH")
    if env and Path(env).exists():
        return env, int(os.environ.get("TITLE_FONT_INDEX", "0"))
    for path, idx in _FONT_CANDIDATES:
        if Path(path).exists():
            return path, idx
    raise RuntimeError(
        "找不到中文字体。请设置 TITLE_FONT_PATH 环境变量指向 .ttc/.ttf/.otf 文件。"
    )


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    path, idx = _resolve_font()
    try:
        return ImageFont.truetype(path, size=size, index=idx)
    except (OSError, IndexError):
        return ImageFont.truetype(path, size=size)


# ================================================================
# 径向渐变（numpy 向量化）
# ================================================================
def _make_gradient(w: int, h: int,
                   fill_center: np.ndarray,
                   fill_edge: np.ndarray) -> Image.Image:
    """极浅径向渐变，中心→边缘几乎察觉不到的色温晕。"""
    yy, xx = np.mgrid[0:h, 0:w]
    dx = (xx - w / 2) / (w / 2 + 1)
    dy = (yy - h / 2) / (h / 2 + 1)
    t = np.clip(np.sqrt(dx ** 2 + dy ** 2), 0.0, 1.0).reshape(h, w, 1)
    rgb = (fill_center * (1 - t) + fill_edge * t).astype(np.uint8)
    alpha = np.full((h, w, 1), 255, dtype=np.uint8)
    return Image.fromarray(np.concatenate([rgb, alpha], axis=2), "RGBA")


# ================================================================
# 圆角矩形兼容绘制
# ================================================================
def _rrect(draw: ImageDraw.ImageDraw,
           xy: tuple[int, int, int, int],
           radius: int,
           fill=None, outline=None, outline_width: int = 1) -> None:
    try:
        draw.rounded_rectangle(xy, radius=radius, fill=fill,
                                outline=outline, width=outline_width)
    except AttributeError:
        x0, y0, x1, y1 = xy
        r = min(radius, (x1 - x0) // 2, (y1 - y0) // 2)
        if fill:
            draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
            draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)
            for ex, ey in [(x0, y0), (x1-2*r, y0), (x0, y1-2*r), (x1-2*r, y1-2*r)]:
                draw.ellipse([ex, ey, ex+2*r, ey+2*r], fill=fill)


# ================================================================
# 细装饰线 + 端点圆点
# ================================================================
def _draw_deco(draw: ImageDraw.ImageDraw,
               cx_left: float, cx_right: float, cy: float,
               line_w: int, dot_r: int,
               color: str) -> None:
    """在文字两侧各绘制：小圆点 · 横线，极细优雅。"""
    # 左侧：从 cx_left 向右延伸 line_w，圆点在最外端
    lx0 = cx_left - line_w - dot_r
    lx1 = cx_left
    # 横线
    draw.rectangle([lx0 + dot_r, int(cy) - 1, lx1 - 3, int(cy) + 1], fill=color)
    # 圆点
    draw.ellipse([lx0 - dot_r, cy - dot_r, lx0 + dot_r, cy + dot_r], fill=color)

    # 右侧：从 cx_right 向左延伸 line_w，圆点在最外端
    rx0 = cx_right
    rx1 = cx_right + line_w + dot_r
    draw.rectangle([rx0 + 3, int(cy) - 1, rx1 - dot_r, int(cy) + 1], fill=color)
    draw.ellipse([rx1 - dot_r, cy - dot_r, rx1 + dot_r, cy + dot_r], fill=color)


# ================================================================
# CJK 间距文字渲染
# ================================================================
def _draw_tracked_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    cx: float, cy: float,
    font: ImageFont.FreeTypeFont,
    fill: str,
    tracking: int = 0,
    shadow_color: tuple | None = None,
    shadow_offset: tuple[int, int] = (1, 2),
) -> tuple[float, float]:
    """逐字渲染，返回 (text_left_x, text_right_x) 实际墨水边界。"""
    chars = list(text)
    char_widths = []
    for ch in chars:
        ink = draw.textbbox((0, 0), ch, font=font, anchor="lt")
        char_widths.append(ink[2] - ink[0])
    total_w = sum(char_widths) + tracking * max(0, len(chars) - 1)
    x = cx - total_w / 2
    x_start = x
    for ch, cw in zip(chars, char_widths):
        ink = draw.textbbox((0, 0), ch, font=font, anchor="lt")
        ch_h = ink[3] - ink[1]
        ch_y = cy - ch_h / 2 - ink[1]
        if shadow_color:
            sx, sy = shadow_offset
            draw.text((x - ink[0] + sx, ch_y + sy), ch,
                      font=font, fill=shadow_color, anchor="lt")
        draw.text((x - ink[0], ch_y), ch, font=font, fill=fill, anchor="lt")
        x += cw + tracking
    return x_start, x - tracking


def _fit_tracked_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    box_w: int,
    box_h: int,
    tracking: int,
    *,
    max_size: int = 200,
    min_size: int = 24,
) -> ImageFont.FreeTypeFont:
    chars = list(text)

    def measure(size: int) -> tuple[int, int]:
        font = _load_font(size)
        widths, heights = [], []
        for ch in chars:
            ink = draw.textbbox((0, 0), ch, font=font, anchor="lt")
            widths.append(ink[2] - ink[0])
            heights.append(ink[3] - ink[1])
        w = sum(widths) + tracking * max(0, len(chars) - 1)
        h = max(heights) if heights else 0
        return w, h

    lo, hi = min_size, max_size
    best_size = lo
    while lo <= hi:
        mid = (lo + hi) // 2
        w, h = measure(mid)
        if w <= box_w * 0.78 and h <= box_h * 0.64:
            best_size = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return _load_font(best_size)


# ================================================================
# 主渲染函数
# ================================================================
def render_titled_frame(
    frame_path: str | Path,
    text: str,
    title_bbox,
    output_path: str | Path,
    *,
    scheme: str = "warm",          # "warm" | "cool"
    text_color: str | None = None,
    stroke_color: str | None = None,
    stroke_width: int = 0,
    font_index: int = 0,
) -> Path:
    """双色温自适应标题牌叠加渲染 · badge-overlay v3"""
    frame_path  = Path(frame_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    text = (text or "").strip() or " "
    sch = _SCHEMES.get(scheme, _SCHEMES["warm"])

    # 颜色（参数可覆盖配色方案）
    text_clr = text_color or sch["text"]

    # 1. 载入背景
    bg = Image.open(frame_path).convert("RGBA")
    iw, ih = bg.size

    # 2. 缩放因子
    sx = iw / _CW
    sy = ih / _CH

    def sc(v: float, axis: str = "x") -> int:
        return int(round(v * (sx if axis == "x" else sy)))

    bl    = sc(_BL)
    br    = sc(_BR)
    bt    = sc(_BT, "y")
    bb    = sc(_BB, "y")
    brad  = sc(_BR_RADIUS)
    bw    = sc(_BORDER_W)
    sw    = sc(_SEP_W)
    track = sc(_TEXT_TRACK)
    dl_w  = sc(_DECO_LINE_W)
    dr    = max(3, sc(_DECO_DOT_R))
    dgap  = sc(_DECO_GAP)
    tgap  = sc(_TEXT_GAP)

    bw_full = bw + sw

    # 3. 软阴影（极淡，带色温）
    shadow_layer = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow_layer)
    sh_off_x = sc(3)
    sh_off_y = sc(6, "y")
    _rrect(sd,
           (bl + sh_off_x, bt + sh_off_y, br + sh_off_x, bb + sh_off_y),
           radius=brad, fill=sch["shadow"])
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=sc(22)))

    # 4. 色温光晕（几乎透明，只是让边缘柔和融入背景）
    glow_layer = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    gexp = sc(10)
    _rrect(gd,
           (bl - gexp, bt - gexp, br + gexp, bb + gexp),
           radius=brad + gexp, fill=sch["glow"])
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=sc(10)))

    # 5. 胶囊主体
    badge_layer = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
    bd = ImageDraw.Draw(badge_layer)

    # 5a. 极细外框（border + sep 叠出细线感）
    _rrect(bd,
           (bl - bw, bt - bw, br + bw, bb + bw),
           radius=brad + bw, fill=sch["border"])
    _rrect(bd,
           (bl - sw, bt - sw, br + sw, bb + sw),
           radius=brad + sw, fill=sch["sep"])

    # 5b. 内腔渐变（极浅，近白）
    pill_w, pill_h = br - bl, bb - bt
    grad = _make_gradient(pill_w, pill_h, sch["fill_center"], sch["fill_edge"])
    mask = Image.new("L", (pill_w, pill_h), 0)
    md = ImageDraw.Draw(mask)
    _rrect(md, (0, 0, pill_w - 1, pill_h - 1), radius=brad, fill=255)
    grad.putalpha(mask)
    badge_layer.paste(grad, (bl, bt), grad)

    # 6. 文字层
    text_layer = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
    td = ImageDraw.Draw(text_layer)

    pill_cx = (bl + br) // 2
    pill_cy = (bt + bb) // 2

    # 文字可写宽度（内腔减去两侧装饰区）
    deco_reserve = (dgap + dl_w + dr + tgap) * 2
    text_box_w = (br - bl) - deco_reserve
    text_box_h = (bb - bt)

    font = _fit_tracked_font(td, text, text_box_w, text_box_h, track)

    text_left, text_right = _draw_tracked_text(
        td, text,
        cx=pill_cx, cy=pill_cy,
        font=font, fill=text_clr,
        tracking=track,
        shadow_color=sch["text_shadow"],
        shadow_offset=(sc(1), sc(1, "y")),
    )

    # 7. 装饰线（文字两侧，与边框同色，极细）
    deco_left_x  = text_left - tgap           # 装饰线右端（贴近文字左侧）
    deco_right_x = text_right + tgap          # 装饰线左端（贴近文字右侧）
    _draw_deco(bd, deco_left_x, deco_right_x, pill_cy,
               dl_w, dr, sch["deco"])

    # 8. 合成
    result = bg.copy()
    result = Image.alpha_composite(result, shadow_layer)
    result = Image.alpha_composite(result, glow_layer)
    result = Image.alpha_composite(result, badge_layer)
    result = Image.alpha_composite(result, text_layer)

    result.save(output_path, format="PNG")
    return output_path


# ================================================================
# 向后兼容接口
# ================================================================
def resolve_title_bbox(image_size: tuple[int, int], meta: dict) -> tuple[int, int, int, int]:
    iw, ih = image_size
    return (0, 0, iw, ih // 4)


PLATE_V1_CANVAS    = (_CW, _CH)
PLATE_V1_INNER_BBOX = (_BL, _BT, _BR - _BL, _BB - _BT)
