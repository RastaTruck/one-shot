"""离线烟雾测试 plate-v1 标准化标题区。

不调任何 AI：自己合成一张 1080×1920 的"假 frame"（在标准内可写区
位置画一个浅色矩形作为牌子的内腔），然后调用 render_titled_frame
把示例标题 P 上去 → 输出到 data/cache/title_preview/_smoke_plate_v1.png

判断标准：肉眼检查文字是否居中落在矩形内、无溢出。

跑法：
    python -m scripts.smoke_plate_v1
    open data/cache/title_preview/_smoke_plate_v1.png
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw

from pipeline.tools.title_overlay import (
    PLATE_V1_CANVAS,
    PLATE_V1_INNER_BBOX,
    render_titled_frame,
    resolve_title_bbox,
)


def main():
    base = Path(__file__).resolve().parents[1]
    cache_dir = base / "data" / "cache" / "title_preview"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 1. 合成假 frame：纯色画布 + 内可写区位置画个浅米色矩形 + 外接框红线参考
    cw, ch = PLATE_V1_CANVAS
    img = Image.new("RGB", (cw, ch), "#FFE9D6")
    draw = ImageDraw.Draw(img)

    # plate-v1 外接框（参考线，仅烟测可见）
    draw.rectangle([(100, 80), (980, 440)], outline="#FF3B30", width=3)
    # plate-v1 内可写区（牌子内腔）
    x, y, w, h = PLATE_V1_INNER_BBOX
    draw.rectangle([(x, y), (x + w, y + h)], fill="#FFFFFF", outline="#C8B6A0", width=4)

    # 画布其余安全区参考
    draw.rectangle([(240, 800), (840, 1500)], outline="#34C759", width=2)  # IP zone
    draw.rectangle([(0, 1600), (cw, ch)], outline="#007AFF", width=2)       # 字幕带

    fake_frame = cache_dir / "_smoke_fake_frame.png"
    img.save(fake_frame)

    # 2. 用 resolver 验证 bbox 解析（会等于内可写区）
    meta = {"spec_version": "plate-v1"}
    bbox = resolve_title_bbox(img.size, meta)
    assert bbox == PLATE_V1_INNER_BBOX, f"resolver mismatch: {bbox}"
    print(f"[ok] resolver returns {bbox}")

    # 3. 走真实渲染：4 个测试标题（短/中/长/英文）
    samples = [
        ("4字-短",  "夕潮日报"),
        ("6字-中",  "AI百科速记"),
        ("9字-长",  "今日财经早晚报告"),
        ("english", "Cyber News"),
    ]
    for tag, text in samples:
        out = cache_dir / f"_smoke_plate_v1_{tag}.png"
        render_titled_frame(
            frame_path=fake_frame,
            text=text,
            title_bbox=bbox,
            output_path=out,
            text_color="#A55B2A",
            stroke_color="#FFFFFF",
            stroke_width=4,
        )
        print(f"[ok] rendered {text!r:>20} → {out.relative_to(base)}")

    print()
    print("打开下面的图肉眼检查（文字应居中落在白色矩形内、无溢出）：")
    for tag, _ in samples:
        print(f"  open data/cache/title_preview/_smoke_plate_v1_{tag}.png")


if __name__ == "__main__":
    main()
