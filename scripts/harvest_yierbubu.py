"""一二布布 IP 素材抓取入口。

策略：
1. 有 TENOR_API_KEY → 走 Tenor API v2，强制 webp/webp_transparent
2. 无 key → 走现有 HTML 抓取（pipeline.tools.tenor_scraper）
3. 抓到的非 webp 文件 → ffmpeg 统一转 webp
4. 自动注册到 asset_catalog.json，meta.ip="yierbubu"，scene_tags 留空待人工/AI 复审

预设搜索词清单覆盖一二布布全情绪谱。可用 --queries 覆盖。

用法：
  python scripts/harvest_yierbubu.py                          # 跑全部预设
  python scripts/harvest_yierbubu.py --queries "一二布布 兴奋"  # 只抓一个
  python scripts/harvest_yierbubu.py --limit 10               # 每词 10 张
  python scripts/harvest_yierbubu.py --no-register            # 只下载不入库
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.asset_library import AssetLibrary  # noqa: E402
from pipeline.tools import tenor_api_client, tenor_scraper  # noqa: E402
from pipeline.tools.webp_converter import to_webp, probe_format  # noqa: E402


DEFAULT_QUERIES = [
    "一二布布",
    "yierbubu",
    "bubududu",
    "bubu dudu",
    "bubu cute",
    "bubu sad",
    "bubu happy",
    "bubu shocked",
    "bubu angry",
    "bubu confused",
    "bubu thinking",
    "bubu calm",
]

REPO_ROOT = Path(__file__).resolve().parents[1]
HARVEST_DIR = REPO_ROOT / "data" / "assets" / "ip" / "yierbubu" / "raw"


def _slug_id(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", s).strip("_")[:60]


def _safe_filename(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._\-]+", "_", s)[:120]


def harvest_query(query: str, limit: int) -> list[dict]:
    """单 query 抓取 → 返回 [{tenor_id, description, local_webp, has_alpha, format, source}]"""
    raw_dir = HARVEST_DIR / _slug_id(query)
    raw_dir.mkdir(parents=True, exist_ok=True)

    use_api = tenor_api_client.api_key() is not None
    items: list[dict] = []

    if use_api:
        print(f"  [{query}] via API v2 (limit={limit})")
        api_items = tenor_api_client.harvest(query, raw_dir, limit=limit, prefer_alpha=True)
        for it in api_items:
            if it.get("error") or not it.get("local_path"):
                continue
            items.append({
                "tenor_id": it.get("id"),
                "description": it.get("description") or query,
                "tags": it.get("tags") or [],
                "local_path": it["local_path"],
                "format": it.get("format"),
                "source": "tenor_api",
            })
    else:
        print(f"  [{query}] via HTML fallback (TENOR_API_KEY 未设置)")
        html_items = tenor_scraper.harvest_search(query, raw_dir, limit=limit)
        for it in html_items:
            if it.get("error") or not it.get("local_path"):
                continue
            tenor_id = ""
            try:
                parts = it["url"].rsplit("/", 2)
                tenor_id = parts[-2] if len(parts) >= 2 else ""
            except Exception:
                pass
            items.append({
                "tenor_id": tenor_id or _slug_id(it.get("alt") or "x"),
                "description": it.get("alt") or query,
                "tags": it.get("slug_tags") or [],
                "local_path": it["local_path"],
                "format": it.get("format"),
                "source": "tenor_html",
            })

    # 统一转 webp
    out: list[dict] = []
    for it in items:
        src = Path(it["local_path"])
        if src.suffix.lower() == ".webp":
            webp_path = src
        else:
            webp_path = src.with_suffix(".webp")
            res = to_webp(src, webp_path, overwrite=False)
            if not res.get("ok"):
                print(f"    convert failed: {src.name} -> {res.get('error')}")
                continue
        info = probe_format(webp_path)
        it["local_webp"] = str(webp_path)
        it["has_alpha"] = info.get("has_alpha", False)
        out.append(it)
    return out


def register(items: list[dict], query: str, lib: AssetLibrary, kind: str) -> int:
    """把 harvest 结果注册到 catalog。kind = 'expressions' 或 'elements'."""
    n = 0
    for it in items:
        webp = Path(it["local_webp"])
        try:
            rel = webp.relative_to(REPO_ROOT)
        except ValueError:
            rel = webp
        tenor_id = it.get("tenor_id") or _slug_id(webp.stem)
        prefix = "EXPR_YB_" if kind == "expressions" else "ELE_YB_"
        asset_id = f"{prefix}{_slug_id(tenor_id)}"[:64]
        # 已存在则跳过（按 id 去重）
        if lib.get(asset_id):
            continue
        meta = {
            "ip": "yierbubu",
            "format": "webp",
            "has_alpha": bool(it.get("has_alpha")),
            "source": it.get("source") or "tenor",
            "tenor_id": tenor_id,
            "harvest_query": query,
            "needs_review": True,           # 等人工复审
            "scene_tags": [],               # 空——待人工/AI 打标
            "scene_tags_ai": [],            # 空——待 AI 打标
            "character": "一二布布" if kind == "expressions" else "",
            "intensity": 2,
        }
        lib.add(
            kind,
            asset_id=asset_id,
            file_path=str(rel),
            tags=[],
            description=it.get("description") or "",
            meta=meta,
        )
        n += 1
    return n


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", nargs="+", default=None,
                        help="搜索词列表（默认 12 个预设）")
    parser.add_argument("--limit", type=int, default=20,
                        help="每词最多抓多少（默认 20）")
    parser.add_argument("--kind", choices=["expressions", "elements"],
                        default="expressions", help="入库到哪一类")
    parser.add_argument("--no-register", action="store_true",
                        help="只下载不写入 catalog")
    args = parser.parse_args()

    queries = args.queries or DEFAULT_QUERIES
    print(f"queries: {len(queries)} · limit={args.limit} · kind={args.kind}")
    print(f"output dir: {HARVEST_DIR}")
    print(f"API key: {'YES' if tenor_api_client.api_key() else 'no (HTML fallback)'}")
    print("---")

    lib = None
    if not args.no_register:
        lib = AssetLibrary()

    grand_total = 0
    grand_registered = 0
    for q in queries:
        items = harvest_query(q, args.limit)
        print(f"  [{q}] harvested {len(items)} webp")
        grand_total += len(items)
        if lib:
            n = register(items, q, lib, args.kind)
            print(f"  [{q}] registered {n} new entries")
            grand_registered += n

    print("---")
    print(f"Total harvested webp files: {grand_total}")
    if lib:
        print(f"Total registered to catalog: {grand_registered}")
        print(f"Catalog stats now: {lib.stats()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
