"""时效热搜层 · 微博 / 知乎 / 百度 三大公开热搜聚合。

合规性：全部调公开 JSON 端点（同浏览器自动会请求的接口），不爬网页，
不需要任何用户登录或 cookie 凭证，不会泄漏个人信息。

端点稳定性：这些公开 API 偶尔会调整结构。单源失败不影响其他源。
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from ..schema import HotspotSignal


_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _fetch_weibo(client: httpx.Client, verbose: bool) -> list[HotspotSignal]:
    """微博热搜：data.realtime 列表。"""
    t0 = time.time()
    try:
        r = client.get(
            "https://weibo.com/ajax/side/hotSearch",
            headers={**_DEFAULT_HEADERS, "Referer": "https://weibo.com/"},
        )
        r.raise_for_status()
        data = r.json()
        items = (data.get("data") or {}).get("realtime") or []
        out: list[HotspotSignal] = []
        for it in items:
            word = (it.get("word") or "").strip()
            if not word:
                continue
            num = it.get("num") or it.get("raw_hot") or 0
            url = f"https://s.weibo.com/weibo?q=%23{word}%23"
            out.append(HotspotSignal(
                source="weibo_hot",
                source_layer="timely",
                title=word,
                url=url,
                summary=it.get("label_name") or None,
                raw_score=float(num) if num else None,
                category_hint=it.get("category") or "social",
                lang="zh",
                raw_data={"rank": it.get("rank"), "label_name": it.get("label_name")},
            ))
        if verbose:
            print(f"  ✓ weibo_hot                {len(out):3d} 条  ({time.time()-t0:.1f}s)")
        return out
    except Exception as e:
        if verbose:
            print(f"  ✗ weibo_hot                {type(e).__name__}: {e}")
        return []


def _fetch_zhihu(client: httpx.Client, verbose: bool) -> list[HotspotSignal]:
    """知乎热榜：移动 API 比 web 端稳定。"""
    t0 = time.time()
    try:
        r = client.get(
            "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=50&desktop=true",
            headers=_DEFAULT_HEADERS,
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("data") or []
        out: list[HotspotSignal] = []
        for it in items:
            target = it.get("target") or {}
            title = (target.get("title") or "").strip()
            if not title:
                continue
            url = target.get("url") or f"https://www.zhihu.com/question/{target.get('id')}"
            # 知乎 API url 是 api.zhihu.com 格式，转成 web url
            if "api.zhihu.com/questions/" in url:
                qid = url.split("/")[-1]
                url = f"https://www.zhihu.com/question/{qid}"
            excerpt = target.get("excerpt") or ""
            detail = it.get("detail_text") or ""  # "1234 万热度"
            score = None
            if detail:
                digits = "".join(c for c in detail if c.isdigit())
                if digits:
                    score = float(digits)
            out.append(HotspotSignal(
                source="zhihu_hot",
                source_layer="timely",
                title=title,
                url=url,
                summary=excerpt[:300] if excerpt else None,
                raw_score=score,
                category_hint="qa_discussion",
                lang="zh",
                raw_data={"detail_text": detail},
            ))
        if verbose:
            print(f"  ✓ zhihu_hot                {len(out):3d} 条  ({time.time()-t0:.1f}s)")
        return out
    except Exception as e:
        if verbose:
            print(f"  ✗ zhihu_hot                {type(e).__name__}: {e}")
        return []


def _fetch_baidu(client: httpx.Client, verbose: bool) -> list[HotspotSignal]:
    """百度热搜（移动端 API 最稳）。"""
    t0 = time.time()
    try:
        r = client.get(
            "https://top.baidu.com/api/board?platform=wise&tab=realtime",
            headers={**_DEFAULT_HEADERS, "Referer": "https://top.baidu.com/"},
        )
        r.raise_for_status()
        data = r.json()
        cards = (data.get("data") or {}).get("cards") or []
        items: list[dict] = []
        for card in cards:
            content = card.get("content") or []
            items.extend(content)
        out: list[HotspotSignal] = []
        for it in items:
            word = (it.get("word") or it.get("query") or "").strip()
            if not word:
                continue
            url = it.get("url") or f"https://www.baidu.com/s?wd={word}"
            score = it.get("hotScore") or 0
            out.append(HotspotSignal(
                source="baidu_hot",
                source_layer="timely",
                title=word,
                url=url,
                summary=it.get("desc") or None,
                raw_score=float(score) if score else None,
                category_hint=it.get("show", [{}])[0].get("hotTag") if it.get("show") else "search",
                lang="zh",
                raw_data={"hotChange": it.get("hotChange")},
            ))
        if verbose:
            print(f"  ✓ baidu_hot                {len(out):3d} 条  ({time.time()-t0:.1f}s)")
        return out
    except Exception as e:
        if verbose:
            print(f"  ✗ baidu_hot                {type(e).__name__}: {e}")
        return []


def fetch_hotsearch_cn(timeout: float = 10.0, verbose: bool = True) -> list[HotspotSignal]:
    """抓微博 + 知乎 + 百度三个时效热搜源。单源失败不影响其他源。"""
    all_signals: list[HotspotSignal] = []
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        all_signals.extend(_fetch_weibo(client, verbose))
        all_signals.extend(_fetch_zhihu(client, verbose))
        all_signals.extend(_fetch_baidu(client, verbose))
    return all_signals
