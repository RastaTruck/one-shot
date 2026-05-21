#!/usr/bin/env python
"""热点工坊 · 抓取 CLI。

用法：
    python scripts/fetch_hotspots.py                # 抓所有源
    python scripts/fetch_hotspots.py --recent       # 看最近 30 条
    python scripts/fetch_hotspots.py --recent --layer ammo  # 只看 ammo 层
    python scripts/fetch_hotspots.py --log          # 看最近抓取日志
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeline.hotspot_forge.fetcher import fetch_all
from pipeline.hotspot_forge.schema import init_db
from pipeline.hotspot_forge.storage import fetch_log_summary, list_recent_signals


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "—"
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def cmd_fetch() -> int:
    print("=" * 60)
    print("热点工坊 · 抓取所有源")
    print("=" * 60)
    result = fetch_all(verbose=True)
    print()
    print("=" * 60)
    print(f"总计: 抓取 {result['total_fetched']} 条 / 新增 {result['total_new']} 条")
    print("=" * 60)
    return 0


def cmd_recent(layer: str | None) -> int:
    rows = list_recent_signals(layer=layer, limit=30)
    if not rows:
        print("信号池为空。先跑一次抓取：python scripts/fetch_hotspots.py")
        return 0
    print(f"\n最近 {len(rows)} 条信号 (layer={layer or 'all'}):\n")
    for r in rows:
        published = _fmt_ts(r.get("published_at"))
        print(f"  [{r['source']:25s}] [{published}] {r['title'][:60]}")
    return 0


def cmd_log() -> int:
    rows = fetch_log_summary(limit=10)
    if not rows:
        print("还没有抓取日志。")
        return 0
    print(f"\n最近 {len(rows)} 次抓取:\n")
    print(f"  {'started':12s}  {'source':15s}  {'status':8s}  {'fetched':>7s}  {'new':>4s}  error")
    for r in rows:
        started = _fmt_ts(r["started_at"])
        print(f"  {started:12s}  {r['source']:15s}  {r['status']:8s}  "
              f"{r['fetched'] or 0:>7d}  {r['inserted'] or 0:>4d}  {r['error'] or ''}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="热点工坊抓取 CLI")
    parser.add_argument("--recent", action="store_true", help="显示最近 30 条信号")
    parser.add_argument("--layer", default=None, choices=["timely", "ammo", "longtail"],
                        help="过滤层级")
    parser.add_argument("--log", action="store_true", help="显示最近抓取日志")
    args = parser.parse_args()

    init_db()

    if args.log:
        return cmd_log()
    if args.recent:
        return cmd_recent(args.layer)
    return cmd_fetch()


if __name__ == "__main__":
    sys.exit(main())
