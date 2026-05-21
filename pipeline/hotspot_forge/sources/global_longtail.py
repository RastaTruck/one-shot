"""长尾雷达层 · HN + Google Trends（免注册的英文圈早期信号源）。

HN：用 Algolia 公开 API 按关键词搜（无需 API key），按金融/经济关键词组合多次。
Google Trends：当日全球/美国热搜 RSS（有时被墙——失败即跳过，不强求）。
"""
from __future__ import annotations

import time
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import httpx

from ..schema import HotspotSignal


# HN 搜索关键词（金融科普 / 经济分析赛道相关）
HN_KEYWORDS = [
    "economy", "finance", "fed", "inflation", "interest rate",
    "stock market", "macro", "recession", "currency",
]


def _fetch_hn(client: httpx.Client, verbose: bool) -> list[HotspotSignal]:
    """HN Algolia API · 多关键词搜索 + 时间倒序 + 去重。"""
    all_hits: dict[str, dict] = {}  # objectID → hit
    for kw in HN_KEYWORDS:
        t0 = time.time()
        try:
            r = client.get(
                "https://hn.algolia.com/api/v1/search_by_date",
                params={
                    "tags": "story",
                    "query": kw,
                    "hitsPerPage": 10,
                    "numericFilters": "points>20",  # 过滤低质
                },
            )
            r.raise_for_status()
            data = r.json()
            hits = data.get("hits") or []
            for h in hits:
                oid = h.get("objectID")
                if oid and oid not in all_hits:
                    all_hits[oid] = h
            if verbose:
                print(f"  ✓ hn:{kw:18s}    {len(hits):3d} 条  ({time.time()-t0:.1f}s)")
        except Exception as e:
            if verbose:
                print(f"  ✗ hn:{kw:18s}    {type(e).__name__}: {e}")

    out: list[HotspotSignal] = []
    for h in all_hits.values():
        title = (h.get("title") or "").strip()
        if not title:
            continue
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        created_at = h.get("created_at_i") or 0
        out.append(HotspotSignal(
            source="hn",
            source_layer="longtail",
            title=title,
            url=url,
            summary=(h.get("story_text") or "")[:300] or None,
            raw_score=float(h.get("points") or 0),
            category_hint="hn_story",
            lang="en",
            published_at=float(created_at) if created_at else None,
            raw_data={"author": h.get("author"), "num_comments": h.get("num_comments")},
        ))
    return out


def _fetch_gtrends(client: httpx.Client, verbose: bool) -> list[HotspotSignal]:
    """Google Trends 当日热搜 RSS。可能被墙——失败即跳过。"""
    out: list[HotspotSignal] = []
    for geo in ("US",):  # 国内访问可能慢/超时，先只抓 US
        t0 = time.time()
        try:
            r = client.get(
                f"https://trends.google.com/trending/rss?geo={geo}",
                headers={"User-Agent": "Mozilla/5.0 (HotspotForge)"},
                timeout=8.0,
            )
            r.raise_for_status()
            root = ET.fromstring(r.content)
            items = root.findall(".//item")
            for it in items:
                title_el = it.find("title")
                title = (title_el.text or "").strip() if title_el is not None else ""
                if not title:
                    continue
                link_el = it.find("link")
                url = (link_el.text or "").strip() if link_el is not None else None
                # ht:approx_traffic 给热度估算
                traffic = None
                ht_traffic = it.find("{https://trends.google.com/trending/rss}approx_traffic")
                if ht_traffic is not None and ht_traffic.text:
                    digits = "".join(c for c in ht_traffic.text if c.isdigit())
                    traffic = float(digits) if digits else None
                pub = it.find("pubDate")
                pub_ts = None
                if pub is not None and pub.text:
                    try:
                        pub_ts = parsedate_to_datetime(pub.text).timestamp()
                    except Exception:
                        pass
                out.append(HotspotSignal(
                    source=f"gtrends_{geo.lower()}",
                    source_layer="longtail",
                    title=title,
                    url=url,
                    summary=None,
                    raw_score=traffic,
                    category_hint="search_trends",
                    lang="en",
                    published_at=pub_ts,
                    raw_data={"geo": geo},
                ))
            if verbose:
                print(f"  ✓ gtrends_{geo:14s}     {len(items):3d} 条  ({time.time()-t0:.1f}s)")
        except Exception as e:
            if verbose:
                print(f"  ✗ gtrends_{geo:14s}     {type(e).__name__}: {str(e)[:60]}")
    return out


def fetch_global_longtail(timeout: float = 12.0, verbose: bool = True) -> list[HotspotSignal]:
    """抓 HN + Google Trends。"""
    all_signals: list[HotspotSignal] = []
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        all_signals.extend(_fetch_hn(client, verbose))
        all_signals.extend(_fetch_gtrends(client, verbose))
    return all_signals
