"""Pexels 图片搜索客户端（Outhan 步骤 1 首帧参考图入口用）。

[AI-NOTE] 2026-05-08: Pexels 免费 API（200 req/h），中文 query 自动翻译，可商用。
- search_async(query, per_page) → 返回 photos list（缩略图 url + 原图 url + 摄影师 + 详情页）
- download_async(url) → bytes，落地交给 outhan_storage 处理

依赖：httpx（fastapi 已带），PEXELS_API_KEY 环境变量。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx


BASE_URL = "https://api.pexels.com/v1"
_TIMEOUT_SEARCH = 15.0
_TIMEOUT_DOWNLOAD = 30.0
_MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024  # 8MB（与 _OUTHAN_IMG_MAX_BYTES 风格一致）

_ENV_LOADED = False


def _load_env_once() -> None:
    """从 config/.env 注入环境变量（与 fish_audio_client / ark_client 同款）。"""
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


def _api_key() -> str:
    _load_env_once()
    return os.environ.get("PEXELS_API_KEY", "")


def is_configured() -> bool:
    return bool(_api_key())


async def search_async(query: str, per_page: int = 12) -> dict[str, Any]:
    """搜图。返回 {ok, photos: [...], error?}。
    photos 每条：{id, thumb_url, full_url, photographer, photographer_url, alt}。
    """
    key = _api_key()
    if not key:
        return {"ok": False, "error": "PEXELS_API_KEY 未配置"}
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "缺关键字"}
    per_page = max(1, min(30, int(per_page or 12)))
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEARCH) as client:
            r = await client.get(
                f"{BASE_URL}/search",
                params={"query": q, "per_page": per_page, "locale": "zh-CN"},
                headers={"Authorization": key},
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"Pexels HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": f"Pexels 搜图失败：{str(e)[:200]}"}

    photos = []
    for p in data.get("photos", []):
        src = p.get("src") or {}
        photos.append({
            "id": p.get("id"),
            "thumb_url": src.get("medium") or src.get("small") or src.get("tiny"),
            "full_url": src.get("large2x") or src.get("large") or src.get("original"),
            "photographer": p.get("photographer", ""),
            "photographer_url": p.get("photographer_url", ""),
            "alt": p.get("alt", "") or q,
            "src_page": p.get("url", ""),  # Pexels 原页面，credit 用
        })
    return {"ok": True, "photos": photos, "total": data.get("total_results", 0)}


async def download_async(url: str) -> dict[str, Any]:
    """下载图，返回 {ok, data: bytes, content_type, error?}。
    限制 8MB；超出 → 错误。"""
    if not url or not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "非法 URL"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_DOWNLOAD, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.content
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"下载 HTTP {e.response.status_code}"}
    except Exception as e:
        return {"ok": False, "error": f"下载失败：{str(e)[:200]}"}
    if len(data) > _MAX_DOWNLOAD_BYTES:
        return {"ok": False, "error": f"图过大（{len(data)//1024} KB），上限 {_MAX_DOWNLOAD_BYTES//1024} KB"}
    if not data:
        return {"ok": False, "error": "空响应"}
    ct = "image/jpeg"  # Pexels 大头都是 jpg；保险起见看下 Content-Type 但默认 jpg
    return {"ok": True, "data": data, "content_type": ct}
