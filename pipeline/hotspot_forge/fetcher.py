"""统一调度三层信号源 → 入库 → 写抓取日志。

MVP 阶段串行抓（每源 1-10s，全部跑完也就 1 分钟内），不引并发复杂度。
后续如果源 ≥ 10 个再考虑 ThreadPool。
"""
from __future__ import annotations

import time
import traceback
from typing import Callable

from .schema import HotspotSignal
from .sources import fetch_global_longtail, fetch_hotsearch_cn, fetch_rss_finance
from .storage import log_fetch_finish, log_fetch_start, upsert_signals


# (source_name, layer, fetch_func)
SOURCES: list[tuple[str, str, Callable[..., list[HotspotSignal]]]] = [
    ("hotsearch_cn", "timely", fetch_hotsearch_cn),
    ("rss_finance", "ammo", fetch_rss_finance),
    ("global_longtail", "longtail", fetch_global_longtail),
]


def fetch_all(verbose: bool = True) -> dict:
    """跑所有源。返回 {total_new, total_fetched, by_source}。"""
    results: dict[str, dict] = {}
    total_new = 0
    total_fetched = 0

    for source_name, _layer, fetch_func in SOURCES:
        if verbose:
            print(f"\n[{source_name}] ----")
        fetch_id = log_fetch_start(source_name)
        try:
            t0 = time.time()
            signals = fetch_func(verbose=verbose)
            inserted = upsert_signals(signals)
            elapsed = time.time() - t0
            log_fetch_finish(fetch_id, "success", fetched=len(signals), inserted=inserted)
            results[source_name] = {"fetched": len(signals), "inserted": inserted, "elapsed": elapsed}
            total_new += inserted
            total_fetched += len(signals)
            if verbose:
                print(f"[{source_name}] DONE: {len(signals)} 抓取 / {inserted} 新增 / {elapsed:.1f}s")
        except Exception as e:
            log_fetch_finish(fetch_id, "failed", error=f"{type(e).__name__}: {e}")
            results[source_name] = {"fetched": 0, "inserted": 0, "error": str(e)}
            if verbose:
                print(f"[{source_name}] FAILED: {type(e).__name__}: {e}")
                traceback.print_exc()

    return {
        "total_new": total_new,
        "total_fetched": total_fetched,
        "by_source": results,
    }
