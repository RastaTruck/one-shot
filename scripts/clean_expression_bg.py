"""清掉 expression webp 边缘连通的白底。支持动画 webp（多帧）。

为什么需要：Tenor 抓的 sticker 是 28 帧动画 webp，每帧 panda 主体 + 周围白色硬边 + 4 角透明。
中间白边被标记 alpha=255 不透明，叠到深色背景上会看到一圈白方块。

算法：4 角种子向内 floodfill 白色（容差 R/G/B 都 ≥ 240），命中的像素改 alpha=0。
保护：panda 内部的白（眼眶 / 肚子）和 panda 边缘连通——floodfill 遇到非白边界就停，不会渗进 panda 内部。

**关键**：动画 webp 必须**每一帧独立处理**并以动画形式保存——单帧保存会丢失所有动画。

用法：
    python3 scripts/clean_expression_bg.py [--dry-run] [--threshold 240] [glob_pattern]

默认处理 data/assets/expressions/**/*.webp。--dry-run 只报告，不写文件。
原图备份到 同目录 / _orig / 文件名 — 可重跑回滚。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from collections import deque
from pathlib import Path

from PIL import Image


def floodfill_white_to_transparent(img: Image.Image, threshold: int = 240) -> tuple[Image.Image, int]:
    """单帧 RGBA 图：从 4 角向内 floodfill RGB 全部 ≥ threshold 的像素 → alpha=0。
    返回 (new_image, cleared_pixel_count)。"""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    W, H = img.size
    px = img.load()
    cleared = 0
    visited = [[False] * H for _ in range(W)]
    queue: deque = deque()
    for sx, sy in [(0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1)]:
        queue.append((sx, sy))
    while queue:
        x, y = queue.popleft()
        if x < 0 or x >= W or y < 0 or y >= H:
            continue
        if visited[x][y]:
            continue
        visited[x][y] = True
        r, g, b, a = px[x, y]
        if a == 0:
            queue.extend([(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)])
            continue
        if r >= threshold and g >= threshold and b >= threshold:
            px[x, y] = (r, g, b, 0)
            cleared += 1
            queue.extend([(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)])
    return img, cleared


def process_file(path: Path, threshold: int, dry_run: bool, backup_dir_name: str = "_orig") -> dict:
    """处理单文件：识别帧数 → 每帧 floodfill → 以动画形式保存。"""
    img = Image.open(path)
    n_frames = getattr(img, "n_frames", 1)
    is_animated = getattr(img, "is_animated", False)

    cleaned_frames: list[Image.Image] = []
    durations: list[int] = []
    total_cleared = 0
    W, H = img.size

    for i in range(n_frames):
        img.seek(i)
        frame = img.convert("RGBA").copy()
        # 保留每帧 duration（默认 100ms）
        durations.append(frame.info.get("duration", img.info.get("duration", 100)))
        cleaned, cleared = floodfill_white_to_transparent(frame, threshold)
        total_cleared += cleared
        cleaned_frames.append(cleaned)

    if not dry_run and total_cleared > 0:
        # 备份原图（首次）
        backup_dir = path.parent / backup_dir_name
        backup_dir.mkdir(exist_ok=True)
        backup_file = backup_dir / path.name
        if not backup_file.exists():
            shutil.copy2(path, backup_file)

        # 保存：单帧 vs 动画分开处理
        if is_animated and n_frames > 1:
            cleaned_frames[0].save(
                path, "WEBP",
                save_all=True,
                append_images=cleaned_frames[1:],
                duration=durations,
                loop=img.info.get("loop", 0),
                lossless=True,
                quality=100,
            )
        else:
            cleaned_frames[0].save(path, "WEBP", lossless=True, quality=100)

    return {
        "path": str(path),
        "cleared": total_cleared,
        "n_frames": n_frames,
        "is_animated": is_animated,
        "total_pixels": W * H * n_frames,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="清 expression webp 的白底（支持动画）")
    parser.add_argument("pattern", nargs="?", default="data/assets/expressions/**/*.webp",
                        help="glob 模式（默认全部 expressions）")
    parser.add_argument("--threshold", type=int, default=240,
                        help="白色阈值（R/G/B 都 ≥ 此值视为白），默认 240")
    parser.add_argument("--dry-run", action="store_true", help="只报告不写文件")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    files = sorted(root.glob(args.pattern))
    files = [f for f in files if "_orig" not in f.parts]
    if not files:
        print(f"[clean_bg] 没找到匹配文件: {args.pattern}")
        return 1

    print(f"[clean_bg] 处理 {len(files)} 个 webp · threshold={args.threshold} · dry_run={args.dry_run}")
    total_cleared = 0
    significantly_cleaned = 0
    animated_count = 0
    for f in files:
        try:
            res = process_file(f.relative_to(root), args.threshold, args.dry_run)
        except Exception as e:
            print(f"  [ERR] {f.name}: {e}")
            continue
        total_cleared += res["cleared"]
        if res["is_animated"]:
            animated_count += 1
        ratio = res["cleared"] / res["total_pixels"] if res["total_pixels"] else 0
        if ratio > 0.01:
            significantly_cleaned += 1
            anim_tag = f"[{res['n_frames']}帧]" if res["is_animated"] else "[静态]"
            print(f"  ✓ {f.name[:45]:<45} {anim_tag:>7} 清 {res['cleared']:>7} 像素 ({ratio*100:.1f}%)")
    print(f"\n[clean_bg] 完成：{significantly_cleaned}/{len(files)} 个有显著白底已清，"
          f"动画 {animated_count} 个，静态 {len(files) - animated_count} 个，共清 {total_cleared} 像素")
    if not args.dry_run:
        print(f"[clean_bg] 原图备份在每个目录的 _orig/ 子目录")
    return 0


if __name__ == "__main__":
    sys.exit(main())
