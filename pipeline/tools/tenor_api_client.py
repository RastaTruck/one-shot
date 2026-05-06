"""Tenor API v2 客户端（Google Cloud · 需 TENOR_API_KEY）。

文档: https://developers.google.com/tenor/guides/endpoints

核心优势：可强制 media_filter 拿 webp / webp_transparent，相比 HTML 抓取稳定。
缺 key 时 search() 返回 None，调用方退化到 tenor_scraper.harvest_search 走 HTML。
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


TENOR_BASE = "https://tenor.googleapis.com/v2"
DEFAULT_MEDIA_FILTER = "webp_transparent,webp,tinywebp,gif,tinygif"


_ENV_LOADED = False


def _load_env_once() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    env_path = Path(__file__).resolve().parents[2] / "config" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def api_key() -> str | None:
    _load_env_once()
    k = (os.environ.get("TENOR_API_KEY") or "").strip()
    return k or None


def search(
    query: str,
    *,
    limit: int = 30,
    media_filter: str = DEFAULT_MEDIA_FILTER,
    locale: str = "zh_CN",
    timeout: int = 30,
) -> dict[str, Any] | None:
    """调 Tenor v2 /search。无 key 返回 None；有 key 但失败返回 {ok: False, error}.

    成功返回 {ok: True, results: [...]}，每个 result：
      {id, content_description, tags, media_formats: {webp: {...}, gif: {...}, ...}}
    """
    key = api_key()
    if key is None:
        return None
    params = {
        "q": query,
        "key": key,
        "client_key": "yierbubu_pipeline",
        "limit": str(min(max(limit, 1), 50)),
        "media_filter": media_filter,
        "locale": locale,
        "contentfilter": "off",
    }
    url = f"{TENOR_BASE}/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            socket.timeout, OSError, json.JSONDecodeError) as e:
        return {"ok": False, "error": str(e)[:300], "results": []}
    return {"ok": True, "results": data.get("results", [])}


def pick_best_url(media_formats: dict, prefer_alpha: bool = True) -> tuple[str | None, str | None]:
    """从 media_formats 选最佳 URL。返回 (url, format_name)。

    优先级：webp_transparent → webp → tinywebp → gif → tinygif
    prefer_alpha=False 时降级 webp_transparent 优先级。
    """
    order = (["webp_transparent"] if prefer_alpha else []) + \
            ["webp", "tinywebp", "gif", "tinygif"]
    for k in order:
        f = media_formats.get(k)
        if isinstance(f, dict) and f.get("url"):
            return f["url"], k
    return None, None


def download_to(url: str, dst: str | Path, *, timeout: int = 60) -> dict[str, Any]:
    """简单下载到 dst。返回 {ok, path, error}."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size > 0:
        return {"ok": True, "path": str(dst), "skipped": True, "error": None}
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (yierbubu-pipeline)",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(dst, "wb") as f:
            f.write(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            socket.timeout, OSError) as e:
        return {"ok": False, "path": None, "error": str(e)[:200]}
    return {"ok": True, "path": str(dst), "skipped": False, "error": None}


def harvest(
    query: str,
    out_dir: str | Path,
    *,
    limit: int = 30,
    prefer_alpha: bool = True,
    on_progress=None,
) -> list[dict[str, Any]]:
    """高层入口：搜 → 选最佳 URL → 下载到 out_dir。返回每条 metadata 列表。

    每条：{id, description, tags, url, format, local_path, error?}
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    res = search(query, limit=limit)
    if res is None:
        return []  # 无 key
    if not res.get("ok"):
        return [{"error": res.get("error")}]
    out: list[dict[str, Any]] = []
    results = res.get("results", [])
    for i, r in enumerate(results, start=1):
        media = r.get("media_formats", {}) or {}
        url, fmt = pick_best_url(media, prefer_alpha=prefer_alpha)
        if not url:
            out.append({"id": r.get("id"), "error": "no usable media url"})
            continue
        rid = r.get("id") or f"x{i}"
        ext = ".webp" if "webp" in (fmt or "") else ".gif"
        # 文件名 = <id>__<slug>.<ext>
        slug = (r.get("content_description") or "").lower()
        slug = "_".join(slug.split())[:60] or "tenor"
        slug = "".join(c if c.isalnum() or c in "_-" else "_" for c in slug)
        filename = f"{rid}__{slug}{ext}"
        dst = out_dir / filename
        dl = download_to(url, dst)
        item = {
            "id": rid,
            "description": r.get("content_description"),
            "tags": r.get("tags") or [],
            "url": url,
            "format": fmt,
            "local_path": dl.get("path"),
            "error": dl.get("error"),
        }
        out.append(item)
        if on_progress:
            on_progress(i, len(results), item)
    return out
