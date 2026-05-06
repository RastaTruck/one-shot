"""把素材库里非 webp 的文件挪到 _trash/ 备份目录，同步从 asset_catalog.json 移除条目。

为什么需要：Tenor 抓回的素材有 webp / gif / mp4 混合。webp 是动画 + 透明 + 抠白底脚本能处理的目标格式；
其他格式的素材（gif 主要）合成视频时透明度只有 1bit、抠白底也没意义、不利于流水线统一。

操作：
- 移动 data/assets/{expressions,elements,backgrounds}/**/*.{gif,mp4,jpg,png} → data/assets/_trash/<原路径>/
- 同步 config/asset_catalog.json 里 file 指向的条目（搬到 catalog._trash 里，可恢复）

用法：
    python3 scripts/trash_non_webp.py --dry-run  # 预览不动手
    python3 scripts/trash_non_webp.py            # 真挪
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = ROOT / "data" / "assets"
TRASH_DIR = ASSETS_DIR / "_trash"
CATALOG_PATH = ROOT / "config" / "asset_catalog.json"

# 保留的格式白名单
KEEP_EXTS = {".webp"}
# 要挪的格式
MOVE_EXTS = {".gif", ".jpg", ".jpeg", ".png", ".mp4"}


def collect_files_to_move() -> list[Path]:
    """扫所有 expressions / elements / backgrounds 子树，找出非 webp 文件。"""
    files: list[Path] = []
    for sub in ("expressions", "elements", "backgrounds"):
        root = ASSETS_DIR / sub
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            # 跳过备份目录里的内容
            if "_orig" in p.parts or "_trash" in p.parts:
                continue
            if p.suffix.lower() in MOVE_EXTS:
                files.append(p)
    return sorted(files)


def move_file(p: Path, dry_run: bool) -> Path:
    """把文件从原位置挪到 _trash/<相对路径>。返回新位置。"""
    rel = p.relative_to(ASSETS_DIR)
    target = TRASH_DIR / rel
    if dry_run:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        # 防覆盖 — 给个数字后缀
        i = 1
        while True:
            alt = target.with_suffix(f".{i}{target.suffix}")
            if not alt.exists():
                target = alt
                break
            i += 1
    shutil.move(str(p), str(target))
    return target


def update_catalog(moved_files: list[Path], dry_run: bool) -> dict:
    """从 catalog 移除 file 路径在 moved_files 里的条目，搬进 _trash 数组。
    返回 {kind: removed_count}。"""
    if dry_run:
        # 仅统计，不写文件
        cat = json.loads(CATALOG_PATH.read_text("utf-8"))
    else:
        cat = json.loads(CATALOG_PATH.read_text("utf-8"))

    moved_rel_set = {str(p.relative_to(ROOT)) for p in moved_files}
    stats: dict[str, int] = {}
    cat.setdefault("_trash", [])  # 历史回收站

    for kind in ("expressions", "elements", "backgrounds", "characters"):
        if kind not in cat or not isinstance(cat[kind], list):
            continue
        kept = []
        removed = []
        for entry in cat[kind]:
            file_rel = entry.get("file", "")
            if file_rel in moved_rel_set:
                entry["_trashed_from"] = kind
                removed.append(entry)
            else:
                kept.append(entry)
        if removed:
            stats[kind] = len(removed)
            if not dry_run:
                cat[kind] = kept
                cat["_trash"].extend(removed)

    if not dry_run and stats:
        CATALOG_PATH.write_text(json.dumps(cat, ensure_ascii=False, indent=2), "utf-8")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="挪非 webp 素材到 _trash 并同步 catalog")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    files = collect_files_to_move()
    if not files:
        print("[trash] 没有找到非 webp 文件，啥都不用做")
        return 0

    print(f"[trash] 准备处理 {len(files)} 个非 webp 文件 (dry_run={args.dry_run})\n")
    by_dir: dict[str, int] = {}
    for f in files:
        rel_dir = str(f.parent.relative_to(ASSETS_DIR))
        by_dir[rel_dir] = by_dir.get(rel_dir, 0) + 1
    for d, n in sorted(by_dir.items()):
        print(f"  {d:<30} {n} 个")
    print()

    moved: list[Path] = []
    for f in files:
        target = move_file(f, args.dry_run)
        moved.append(f)
        if not args.dry_run:
            print(f"  → {f.relative_to(ROOT)} → _trash/{f.relative_to(ASSETS_DIR)}")

    stats = update_catalog(moved, args.dry_run)
    print()
    print(f"[trash] catalog 同步：")
    for k, n in stats.items():
        print(f"  {k}: 移除 {n} 条 → _trash 数组")

    if args.dry_run:
        print("\n[trash] dry-run 没动文件。加 --no-dry-run 选项不存在 — 直接去掉 --dry-run 跑真删。")
    else:
        print(f"\n[trash] 完成。备份在 {TRASH_DIR.relative_to(ROOT)}/")
        print("[trash] catalog 里的旧条目搬到 _trash 数组，需要时手动恢复。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
