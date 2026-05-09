"""Vidu 国内版 API 客户端（移植自 D:\\AIvideo\\src\\lib\\api\\vidu.ts）。

国内版 base = https://api.vidu.cn/ent/v2
认证: Authorization: Token <api_key>
关键差异：必填 model；轮询路径 /tasks/{id}/creations
state 5 态：created / queueing / processing / success / failed

环境变量：VIDU_API_KEY（读 config/.env）
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


VIDU_BASE = "https://api.vidu.cn/ent/v2"

# 国内版主推：viduq3-pro
DEFAULT_MODEL = "viduq3-pro"

VIDU_MODELS = (
    "viduq3-pro", "viduq3-turbo", "viduq3-pro-fast",
    "viduq2-pro", "viduq2-pro-fast", "viduq2-turbo",
    "viduq1", "viduq1-classic", "vidu2.0",
)

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


def _api_key() -> str | None:
    _load_env_once()
    k = os.environ.get("VIDU_API_KEY", "").strip()
    if not k or k.startswith("vda-xxx") or k.startswith("vda_xxx"):
        return None
    return k


def _http_post_json(url: str, key: str, payload: dict, timeout: int = 60) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
        method="POST",
    )
    body = _retry_urlopen(req, timeout=timeout)
    return json.loads(body.decode("utf-8"))


def _http_get_json(url: str, key: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Token {key}"}, method="GET")
    body = _retry_urlopen(req, timeout=timeout)
    return json.loads(body.decode("utf-8"))


def _retry_urlopen(req: urllib.request.Request, *, timeout: int) -> bytes:
    """[AI-NOTE] 2026-05-08: 5 次退避重试（SSL EOF 等抖动），HTTP 4xx/5xx 不重试。"""
    import time as _time
    last_err: Exception | None = None
    delays = [2, 5, 10, 15]
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as e:
            last_err = e
            if attempt < len(delays):
                print(f"[vidu-retry] {attempt+1}/5 抖动重试：{str(e)[:120]}",
                      file=__import__('sys').stderr)
                _time.sleep(delays[attempt])
                continue
            raise
    if last_err:
        raise last_err
    return b""


def _err(e: Exception) -> str:
    msg = str(e)[:300]
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read().decode("utf-8", "replace")[:300]
            msg = f"HTTP {e.code}: {body}"
        except Exception:
            pass
        return msg
    raw = str(e)
    if "UNEXPECTED_EOF" in raw or "SSL" in raw:
        return f"Vidu 服务 SSL 握手抖动（已重试 5 次失败）。请等 30 秒再点重试，不是 prompt 问题"
    if "timed out" in raw or "timeout" in raw.lower():
        return f"Vidu 服务超时（已重试 5 次）。请稍后重试"
    if "Connection reset" in raw or "RemoteDisconnected" in raw:
        return f"Vidu 服务连接断开（已重试 5 次）。请稍后重试"
    return msg


def vidu_create(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    images: list[str] | None = None,
    duration: int = 5,
    resolution: str = "720p",
    aspect_ratio: str = "16:9",
    movement_amplitude: str = "auto",
    style: str = "general",
    seed: int | None = None,
    watermark: bool = False,
    timeout: int = 60,
) -> dict[str, Any]:
    """创建 Vidu 国内版任务。
    有 images（首帧）走 img2video；无 images 走 text2video。
    返回 {ok, task_id, error}。
    """
    key = _api_key()
    if key is None:
        return {"ok": False, "task_id": None, "error": "VIDU_API_KEY 未配置"}
    has_image = bool(images)
    endpoint = "img2video" if has_image else "text2video"
    body: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "duration": duration,
        "resolution": resolution,
        "movement_amplitude": movement_amplitude,
        "watermark": watermark,
    }
    if has_image:
        body["images"] = images
    else:
        body["aspect_ratio"] = aspect_ratio
        body["style"] = style
    if seed is not None:
        body["seed"] = seed
    try:
        resp = _http_post_json(f"{VIDU_BASE}/{endpoint}", key, body, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, json.JSONDecodeError) as e:
        return {"ok": False, "task_id": None, "error": _err(e)}
    task_id = resp.get("task_id") or resp.get("id")
    if not task_id:
        return {"ok": False, "task_id": None, "error": f"无 task_id: {str(resp)[:200]}"}
    return {"ok": True, "task_id": task_id, "error": None}


def vidu_query(task_id: str, timeout: int = 30) -> dict[str, Any]:
    """轮询 Vidu 任务状态。返回 {ok, state, video_url, err_code, error}。

    [AI-NOTE] 2026-05-07: 国内版必须 /tasks/{id}/creations（带 /creations）才能拿 url
    """
    key = _api_key()
    if key is None:
        return {"ok": False, "state": "failed", "error": "VIDU_API_KEY 未配置"}
    try:
        resp = _http_get_json(f"{VIDU_BASE}/tasks/{task_id}/creations", key, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, json.JSONDecodeError) as e:
        return {"ok": False, "state": "failed", "error": _err(e)}
    state = resp.get("state", "unknown")
    creations = resp.get("creations") or []
    video_url = creations[0].get("url") if creations else None
    return {
        "ok": True,
        "state": state,
        "video_url": video_url,
        "err_code": resp.get("err_code"),
        "error": None,
    }
