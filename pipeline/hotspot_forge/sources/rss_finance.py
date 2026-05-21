"""专业弹药层 · 财经媒体 RSS 抓取。

设计要点：
- 用标准库 xml.etree（不引 feedparser，零新依赖）
- 兼容 RSS 2.0 (rss/channel/item) 和 Atom (feed/entry) 两种格式
- 单 feed 失败不影响其他 feed
- RSS URL 是候选清单，实测后清理无效项

RSS_FEEDS 是 best-effort 列表——国内财经 RSS 长期维护不一，
首次跑完后看抓取日志清理掉持续失败的源。
"""
from __future__ import annotations

import re
import time
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import httpx

from ..schema import HotspotSignal


# 财经 RSS 源候选。实测后清理。
# - native_*  : 媒体官方 feed
# - mirror_*  : 第三方 RSS 镜像/聚合
RSS_FEEDS: list[dict] = [
    # —— 国内官方源 ——
    {"name": "wallstreetcn_global",
     "url": "https://api.wallstreetcn.com/apiv1/content/articles/rss",
     "category_hint": "global_market", "lang": "zh"},
    {"name": "ftchinese_news",
     "url": "https://www.ftchinese.com/rss/news",
     "category_hint": "international", "lang": "zh"},
    {"name": "36kr_feed",
     "url": "https://www.36kr.com/feed",
     "category_hint": "business_tech", "lang": "zh"},
    {"name": "sina_finance",
     "url": "https://rss.sina.com.cn/roll/finance/hot_roll.xml",
     "category_hint": "finance_daily", "lang": "zh"},
    # —— 第三方 RSS 镜像（feedx.net）——
    {"name": "feedx_economist",
     "url": "https://feedx.net/rss/economist.xml",
     "category_hint": "macro_analysis", "lang": "zh"},
    {"name": "feedx_ft",
     "url": "https://feedx.net/rss/ft.xml",
     "category_hint": "international", "lang": "zh"},
    {"name": "feedx_wsj",
     "url": "https://feedx.net/rss/wsj.xml",
     "category_hint": "us_market", "lang": "zh"},
]


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_text(s: str | None) -> str:
    """去 HTML 标签 + 折叠空白。"""
    if not s:
        return ""
    s = _HTML_TAG_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def _parse_pubdate(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return parsedate_to_datetime(s.strip()).timestamp()
    except Exception:
        # ISO8601 兜底（Atom 用 RFC3339）
        try:
            import datetime as _dt
            # 去掉末尾 Z
            iso = s.strip().replace("Z", "+00:00")
            return _dt.datetime.fromisoformat(iso).timestamp()
        except Exception:
            return None


_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _findtext(el, tag: str) -> str | None:
    """同时尝试无命名空间 + Atom 命名空间。"""
    for path in (tag, f"{_ATOM_NS}{tag}"):
        node = el.find(path)
        if node is not None and node.text:
            return node.text
    return None


def _find_link(el) -> str | None:
    # RSS: <link>url</link>; Atom: <link href="url"/>
    node = el.find("link")
    if node is not None:
        if node.text and node.text.strip():
            return node.text.strip()
        href = node.attrib.get("href")
        if href:
            return href.strip()
    node = el.find(f"{_ATOM_NS}link")
    if node is not None:
        href = node.attrib.get("href") or (node.text or "").strip()
        if href:
            return href.strip()
    return None


def _parse_rss_xml(xml_bytes: bytes, feed_name: str, category: str, lang: str) -> list[HotspotSignal]:
    root = ET.fromstring(xml_bytes)
    # 兼容两种格式
    items = root.findall(".//item")
    if not items:
        items = root.findall(f".//{_ATOM_NS}entry")

    signals: list[HotspotSignal] = []
    for it in items:
        title = _clean_text(_findtext(it, "title"))
        if not title:
            continue
        url = _find_link(it)
        summary = _clean_text(_findtext(it, "description") or _findtext(it, "summary"))
        pub_raw = _findtext(it, "pubDate") or _findtext(it, "published") or _findtext(it, "updated")
        published_at = _parse_pubdate(pub_raw)

        signals.append(HotspotSignal(
            source=f"rss:{feed_name}",
            source_layer="ammo",
            title=title[:300],
            url=url,
            summary=summary[:500] if summary else None,
            category_hint=category,
            lang=lang,
            published_at=published_at,
            raw_data={"feed": feed_name},
        ))
    return signals


def fetch_rss_finance(timeout: float = 10.0, verbose: bool = True) -> list[HotspotSignal]:
    """抓所有配置的 RSS 源。单源失败不影响其他源。"""
    all_signals: list[HotspotSignal] = []
    headers = {
        "User-Agent": "Mozilla/5.0 (HotspotForge/0.1) AppleWebKit/537.36",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
    }
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        for feed in RSS_FEEDS:
            t0 = time.time()
            try:
                r = client.get(feed["url"])
                r.raise_for_status()
                signals = _parse_rss_xml(r.content, feed["name"], feed["category_hint"], feed["lang"])
                all_signals.extend(signals)
                if verbose:
                    print(f"  ✓ {feed['name']:25s} {len(signals):3d} 条  ({time.time()-t0:.1f}s)")
            except httpx.HTTPStatusError as e:
                if verbose:
                    print(f"  ✗ {feed['name']:25s} HTTP {e.response.status_code}")
            except httpx.TimeoutException:
                if verbose:
                    print(f"  ✗ {feed['name']:25s} TIMEOUT")
            except ET.ParseError as e:
                if verbose:
                    print(f"  ✗ {feed['name']:25s} XML 解析失败: {e}")
            except Exception as e:
                if verbose:
                    print(f"  ✗ {feed['name']:25s} {type(e).__name__}: {e}")
    return all_signals
