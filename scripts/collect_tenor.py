"""Tenor 一键抓取 + 入素材库。

用法：
    # 收集 IP 表情库（一二布布 / 实际是 BuBu DuDu）
    python scripts/collect_tenor.py --kind expressions --library yierbubu \\
           --character "一二布布" --queries "一二,布布嘟嘟,bubududu"

    # 收集场景元素库（多个搜索词，每词一批）
    python scripts/collect_tenor.py --kind elements --style MBE_TENOR \\
           --queries "金币,钱袋,股票,K线,上涨,下跌,惊讶,思考"

每轮搜索抓 ≤ N 条 webp/gif，写到 data/assets/<kind>/<library_id>/，
更新 config/asset_catalog.json。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.asset_library import AssetLibrary
from pipeline.tools import tenor_scraper as ts


def slugify_id(prefix: str, query: str, slug: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]+", "_", f"{prefix}_{query}_{slug}").strip("_")
    return s.upper()[:64]


def main():
    p = argparse.ArgumentParser(description="Tenor 抓取入库")
    p.add_argument("--kind", choices=["expressions", "elements"], required=True)
    p.add_argument("--library", required=True,
                    help="对 expressions=character_id (yierbubu 等)；对 elements=style 字段值")
    p.add_argument("--character", default=None,
                    help="对 expressions：写进 meta.character 的中文名（默认同 library）")
    p.add_argument("--queries", required=True, help="逗号分隔的搜索词")
    p.add_argument("--limit", type=int, default=40, help="每个搜索词最多抓多少")
    p.add_argument("--lang", default="zh-CN")
    args = p.parse_args()

    queries = [q.strip() for q in args.queries.split(",") if q.strip()]
    print(f"[+] 准备抓取 {len(queries)} 个搜索词，每词 ≤ {args.limit} 条")

    out_dir = ROOT / "data" / "assets" / args.kind / args.library
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[+] 输出目录 {out_dir.relative_to(ROOT)}")

    lib = AssetLibrary()
    total_added = 0
    total_skipped = 0
    total_failed = 0

    for q in queries:
        print(f"\n=== 搜索: {q!r} ===")
        try:
            # Tenor 一次返 webp + gif 混合，按 limit 内多抓再过滤——尽量保证 webp 数到 limit
            raw = ts.extract_from_search(q, lang=args.lang, limit=args.limit * 3)
        except Exception as e:
            print(f"  [!] 搜索失败: {e}")
            continue
        # 强制只要 webp（webp 支持动画 + 真透明，gif 透明只 1bit）
        items = [it for it in raw if (it.get("format") or "").lower() == "webp"][:args.limit]
        skipped = len(raw) - len(items)
        print(f"  找到 {len(raw)} 条 → 保留 webp {len(items)} 条（跳过 {skipped} 个非 webp）")
        for i, it in enumerate(items, 1):
            try:
                local = ts.download(it, out_dir)
            except Exception as e:
                total_failed += 1
                print(f"  [{i:02d}] 下载失败: {e}")
                continue
            relpath = str(local.resolve().relative_to(ROOT))
            slug_str = "-".join(it["slug_tags"][:3]) or "asset"
            asset_id = slugify_id(args.kind[:3].upper(), q, slug_str + "_" + str(i).zfill(2))
            # 已存在就跳过
            if lib.get(asset_id) is not None:
                total_skipped += 1
                continue
            scene_tags = ts.infer_scene_tags(it["alt"], it["slug_tags"], q)

            tags = list(set(it["slug_tags"] + scene_tags + [q]))[:8]
            description = it["alt"] or f"{q} · {slug_str}"

            if args.kind == "expressions":
                meta = {
                    "source": "tenor",
                    "scene_tags": scene_tags or [q],
                    "intensity": 2,
                    "has_alpha": True,
                    "character": args.character or args.library,
                    "alt": it["alt"],
                }
                lib.add(
                    "expressions",
                    asset_id=asset_id,
                    file_path=relpath,
                    tags=tags,
                    description=description,
                    style=args.library,
                    meta=meta,
                )
            else:  # elements
                meta = {
                    "source": "tenor",
                    "keyword": q,
                    "alt": it["alt"],
                }
                lib.add(
                    "elements",
                    asset_id=asset_id,
                    file_path=relpath,
                    tags=tags,
                    description=description,
                    style=args.library,
                    meta=meta,
                )
            total_added += 1
            print(f"  [{i:02d}] + {asset_id} ({local.name})")

    lib.save()
    print(f"\n[+] 完成 · 新增 {total_added} · 已存在跳过 {total_skipped} · 失败 {total_failed}")
    print(f"[+] 写回 config/asset_catalog.json")


if __name__ == "__main__":
    main()
