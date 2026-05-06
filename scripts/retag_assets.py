"""把已入库 expressions / elements 的 alt/slug/desc 重新跑 infer_scene_tags 补 tag。

用 v2 扩展后的 SCENE_TAG_KEYWORDS。

策略：
- expressions：保持已有 scene_tags + 重推合并（去重）
- elements：alt 推 tag 后并入 tags 字段
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.asset_library import AssetLibrary
from pipeline.tools.tenor_scraper import infer_scene_tags

CATALOG = ROOT / "config" / "asset_catalog.json"


def main():
    lib = AssetLibrary(catalog_path=CATALOG)
    print(f"loaded · expressions={len(lib.all('expressions'))} · elements={len(lib.all('elements'))}")

    n_expr_changed = 0
    for a in lib.all("expressions"):
        alt = a.meta.get("alt") or a.description or ""
        slug_tags = a.meta.get("slug_tags") or a.tags or []
        # 没记 alt 的，用 description+tags 凑
        existing = set(a.meta.get("scene_tags") or [])
        new_tags = set(infer_scene_tags(alt, slug_tags, ""))
        merged = list(existing | new_tags)[:4]
        if set(merged) != existing:
            a.meta["scene_tags"] = merged
            n_expr_changed += 1

    n_el_changed = 0
    for a in lib.all("elements"):
        alt = a.meta.get("alt") or a.description or ""
        slug_tags = a.meta.get("slug_tags") or a.tags or []
        existing_tags = set(a.tags or [])
        scene = set(infer_scene_tags(alt, slug_tags, ""))
        new_tags_set = existing_tags | scene
        if new_tags_set != existing_tags:
            a.tags = list(new_tags_set)
            n_el_changed += 1

    lib.save()
    print(f"✓ expressions retag: {n_expr_changed} 改动")
    print(f"✓ elements retag:   {n_el_changed} 改动")

    # 输出新覆盖
    from collections import Counter
    c = Counter()
    for a in lib.all("expressions"):
        for t in a.meta.get("scene_tags", []):
            c[t] += 1
    print("\nexpression scene_tag 覆盖统计:")
    for tag, n in c.most_common():
        print(f"  {tag}: {n}")


if __name__ == "__main__":
    main()
