"""一次性逻辑迁移：扫 asset_catalog.json 给每条素材打 meta.ip / meta.format / meta.has_alpha。

不动文件路径（物理上 expressions/yierbubu/ 已经隔离了）。

使用：
  python scripts/migrate_yierbubu.py            # dry-run，只打印将要写什么
  python scripts/migrate_yierbubu.py --apply    # 真改 catalog
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本能在仓库根直接跑
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.asset_library import AssetLibrary  # noqa: E402
from pipeline.tools.webp_converter import probe_format  # noqa: E402


YIERBUBU_KEYWORDS = ["yierbubu", "布布", "一二", "bubududu", "bubu"]
GENERAL_PATH_HINTS = ["TENOR_GENERAL", "TENOR_FINANCE"]


def detect_ip(asset) -> str | None:
    """根据 file path / meta.character / description / tags 判定 IP。"""
    blob = " ".join([
        asset.file or "",
        asset.description or "",
        " ".join(asset.tags or []),
        asset.meta.get("character") or "",
        asset.id or "",
    ]).lower()
    if "/yierbubu/" in (asset.file or "").lower():
        return "yierbubu"
    if any(kw.lower() in blob for kw in YIERBUBU_KEYWORDS):
        return "yierbubu"
    if any(hint in (asset.file or "") for hint in GENERAL_PATH_HINTS):
        return "general"
    return None


def detect_format(asset) -> str | None:
    p = (asset.file or "").lower()
    if not p:
        return None
    for ext in ("webp", "gif", "webm", "mp4", "png", "jpg", "jpeg"):
        if p.endswith("." + ext):
            return "jpg" if ext == "jpeg" else ext
    return None


def maybe_probe_alpha(asset, repo_root: Path) -> bool | None:
    """对本地文件探测 alpha；远程占位 url 跳过。"""
    fp = asset.file or ""
    if fp.startswith("http") or not fp:
        return None
    full = repo_root / fp
    if not full.exists():
        return None
    info = probe_format(full)
    if not info.get("ok"):
        return None
    return bool(info.get("has_alpha"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="真写 catalog；默认只 dry-run")
    parser.add_argument("--probe-alpha", action="store_true",
                        help="探测每个本地文件 alpha（慢，几百次 ffprobe）")
    args = parser.parse_args()

    lib = AssetLibrary()
    repo_root = Path(__file__).resolve().parents[1]

    plan = []  # [(asset_id, kind, file, current_ip, new_ip, current_fmt, new_fmt, alpha)]
    for kind in ("backgrounds", "characters", "elements", "expressions"):
        for a in lib.all(kind):
            cur_ip = a.meta.get("ip")
            new_ip = detect_ip(a)
            cur_fmt = a.meta.get("format")
            new_fmt = detect_format(a)
            new_alpha = None
            if args.probe_alpha:
                new_alpha = maybe_probe_alpha(a, repo_root)
            need_change = (
                (new_ip and new_ip != cur_ip)
                or (new_fmt and new_fmt != cur_fmt)
                or (args.probe_alpha and new_alpha is not None
                    and new_alpha != a.meta.get("has_alpha"))
            )
            if need_change:
                plan.append({
                    "asset_id": a.id,
                    "kind": kind,
                    "file": a.file,
                    "cur_ip": cur_ip, "new_ip": new_ip,
                    "cur_fmt": cur_fmt, "new_fmt": new_fmt,
                    "new_alpha": new_alpha,
                })

    print(f"Total assets: {sum(lib.stats().values())}")
    print(f"Will change: {len(plan)}")
    by_ip: dict[str, int] = {}
    for p in plan:
        by_ip[p["new_ip"] or "(none)"] = by_ip.get(p["new_ip"] or "(none)", 0) + 1
    print(f"By new_ip: {by_ip}")

    by_fmt: dict[str, int] = {}
    for p in plan:
        by_fmt[p["new_fmt"] or "(none)"] = by_fmt.get(p["new_fmt"] or "(none)", 0) + 1
    print(f"By new_format: {by_fmt}")

    print("\nFirst 10 changes preview:")
    for p in plan[:10]:
        print(f"  {p['asset_id']:30s} kind={p['kind']:11s} ip {p['cur_ip']!r:>10s}->{p['new_ip']!r:<12s} fmt {p['cur_fmt']!r:>6s}->{p['new_fmt']!r}")

    if not args.apply:
        print("\n[dry-run] no catalog written. Re-run with --apply to commit.")
        return 0

    # 真写
    updates = []
    for p in plan:
        patch: dict = {}
        if p["new_ip"]:
            patch["ip"] = p["new_ip"]
        if p["new_fmt"]:
            patch["format"] = p["new_fmt"]
        if p["new_alpha"] is not None:
            patch["has_alpha"] = p["new_alpha"]
        updates.append({"asset_id": p["asset_id"], "meta_patch": patch})
    res = lib.bulk_update(updates)
    print(f"\n[applied] ok_count={res['ok_count']} missing={len(res['missing'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
