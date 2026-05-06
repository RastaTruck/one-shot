"""火山方舟 Ark API 客户端 —— 生图（同步）+ 图生视频（异步任务 + 轮询）。

依赖 stdlib：urllib。无密钥时返回 stub 占位 url，让前端仍可演示。

环境变量：ARK_API_KEY（读 config/.env）。

模型：
- 生图 doubao-seedream-4-0-250828：POST /api/v3/images/generations（同步）
- 图生视频 doubao-seedance-1-0-pro-fast-251015：POST /api/v3/contents/generations/tasks（提交） +
  GET /api/v3/contents/generations/tasks/{id}（轮询）

成本估算写在常量里，实际以火山账单为准。
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
MODEL_IMAGE = "doubao-seedream-4-0-250828"
MODEL_VIDEO = "doubao-seedance-1-0-pro-fast-251015"
MODEL_VISION_DEFAULT = "doubao-seed-1-6-250615"

# vision 单次调用成本估算（CNY）
VISION_COST_CNY = 0.005

# 成本估算（CNY）——实际以火山账单为准，仅供前端展示
IMAGE_COST_CNY = 0.30
VIDEO_COST_CNY = 2.50

# stub 占位图（无密钥时）
_STUB_IMG = "https://placehold.co/1024x1792/F48A0C/0D0D0B?text=STUB+IMAGE"
_STUB_VIDEO = "https://placehold.co/1024x1792/1E40AF/FFFFFF?text=STUB+VIDEO"

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
    k = os.environ.get("ARK_API_KEY", "").strip()
    if not k or k.startswith("ark-xxx"):
        return None
    return k


def _http_post_json(url: str, key: str, payload: dict, timeout: int = 180) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get_json(url: str, key: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _err(e: Exception) -> str:
    msg = str(e)[:300]
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read().decode("utf-8", "replace")[:300]
            msg = f"HTTP {e.code}: {body}"
        except Exception:
            pass
    return msg


def generate_image(
    prompt: str,
    *,
    size: str = "2K",
    watermark: bool = True,
    timeout: int = 180,
) -> dict[str, Any]:
    """同步生图。返回 {ok, url, cost, provider, error, prompt}."""
    key = _api_key()
    if key is None:
        return {
            "ok": True, "url": _STUB_IMG, "cost": 0.0,
            "provider": "stub", "error": None, "prompt": prompt,
        }
    try:
        resp = _http_post_json(
            f"{ARK_BASE}/images/generations", key,
            {
                "model": MODEL_IMAGE,
                "prompt": prompt,
                "sequential_image_generation": "disabled",
                "response_format": "url",
                "size": size,
                "stream": False,
                "watermark": watermark,
            },
            timeout=timeout,
        )
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, json.JSONDecodeError) as e:
        return {"ok": False, "url": None, "cost": 0.0, "provider": "ark",
                "error": _err(e), "prompt": prompt}
    # 响应兼容：data[0].url 或 images[0].url
    url = None
    try:
        if "data" in resp and resp["data"]:
            url = resp["data"][0].get("url")
        elif "images" in resp and resp["images"]:
            url = resp["images"][0].get("url")
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
    if not url:
        return {"ok": False, "url": None, "cost": 0.0, "provider": "ark",
                "error": f"no url in resp: {str(resp)[:200]}", "prompt": prompt}
    return {"ok": True, "url": url, "cost": IMAGE_COST_CNY,
            "provider": "ark", "error": None, "prompt": prompt}


def create_video_task(
    image_url: str,
    motion_prompt: str,
    *,
    duration: int = 5,
    resolution: str = "1080p",
    camera_fixed: bool = False,
    watermark: bool = True,
    timeout: int = 60,
) -> dict[str, Any]:
    """提交图生视频任务。返回 {ok, task_id, provider, error}."""
    key = _api_key()
    if key is None:
        return {"ok": True, "task_id": f"stub-{int(time.time())}", "provider": "stub", "error": None}
    full_text = (
        f"{motion_prompt}  --resolution {resolution}  --duration {duration} "
        f"--camerafixed {str(camera_fixed).lower()} --watermark {str(watermark).lower()}"
    )
    try:
        resp = _http_post_json(
            f"{ARK_BASE}/contents/generations/tasks", key,
            {
                "model": MODEL_VIDEO,
                "content": [
                    {"type": "text", "text": full_text},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
            timeout=timeout,
        )
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, json.JSONDecodeError) as e:
        return {"ok": False, "task_id": None, "provider": "ark", "error": _err(e)}
    task_id = resp.get("id") or resp.get("task_id")
    if not task_id:
        return {"ok": False, "task_id": None, "provider": "ark",
                "error": f"no task_id in resp: {str(resp)[:200]}"}
    return {"ok": True, "task_id": task_id, "provider": "ark", "error": None}


def get_video_task(task_id: str) -> dict[str, Any]:
    """查询图生视频任务状态。返回 {ok, status, video_url, cost, error}.

    status 取值：queued / running / succeeded / failed（遵循 Ark 规范，有差异兼容）.
    """
    key = _api_key()
    if key is None or task_id.startswith("stub-"):
        return {"ok": True, "status": "succeeded", "video_url": _STUB_VIDEO,
                "cost": 0.0, "error": None}
    try:
        resp = _http_get_json(f"{ARK_BASE}/contents/generations/tasks/{task_id}", key, timeout=30)
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, json.JSONDecodeError) as e:
        return {"ok": False, "status": "failed", "video_url": None, "cost": 0.0, "error": _err(e)}
    raw_status = (resp.get("status") or "").lower()
    mapping = {
        "queued": "queued", "pending": "queued",
        "running": "running", "in_progress": "running",
        "succeeded": "succeeded", "success": "succeeded", "completed": "succeeded",
        "failed": "failed", "error": "failed", "cancelled": "failed",
    }
    status = mapping.get(raw_status, raw_status or "queued")
    video_url = None
    if status == "succeeded":
        # 兼容 content.video_url / data.video_url / video_url
        if isinstance(resp.get("content"), dict):
            video_url = resp["content"].get("video_url")
        if not video_url and isinstance(resp.get("data"), dict):
            video_url = resp["data"].get("video_url")
        if not video_url:
            video_url = resp.get("video_url")
    return {
        "ok": True, "status": status, "video_url": video_url,
        "cost": VIDEO_COST_CNY if status == "succeeded" else 0.0,
        "error": resp.get("error") or resp.get("error_message"),
    }


def _image_to_data_url(path: str | Path, *, max_size: int = 768) -> str | None:
    """读图片（含 webp/gif 第一帧）→ JPEG base64 → data url。失败返回 None."""
    import base64
    import io
    try:
        from PIL import Image
    except ImportError:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        with Image.open(p) as im:
            # 动图取第一帧
            if hasattr(im, "is_animated") and im.is_animated:
                im.seek(0)
            im = im.convert("RGB")
            # 缩到 max_size 内（doubao vision 输入大小敏感，省 token）
            w, h = im.size
            if max(w, h) > max_size:
                ratio = max_size / max(w, h)
                im = im.resize((int(w * ratio), int(h * ratio)))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
            data = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{data}"
    except (OSError, ValueError):
        return None


def classify_image(
    image_path: str | Path,
    *,
    closed_set: list[str],
    description_hint: str = "",
    model: str | None = None,
    max_choices: int = 3,
    timeout: int = 90,
) -> dict[str, Any]:
    """让豆包 vision 从 closed_set 选 1~max_choices 个标签描述图片情绪/场景。

    返回 {ok, scene_tags: [...], rationale, cost, provider, error}.
    无 key 时返回 stub。
    """
    key = _api_key()
    if key is None:
        return {"ok": True, "scene_tags": [], "rationale": "(stub: 无 ARK_API_KEY)",
                "cost": 0.0, "provider": "stub", "error": None}
    data_url = _image_to_data_url(image_path)
    if data_url is None:
        return {"ok": False, "scene_tags": [], "rationale": "",
                "cost": 0.0, "provider": "ark",
                "error": f"failed to load image: {image_path}"}

    model = model or os.environ.get("ARK_VISION_MODEL") or MODEL_VISION_DEFAULT
    closed = "、".join(closed_set)
    prompt = (
        f"你看到一张视频素材图（表情/物件/场景元素）。请从下面的闭集标签中选 1~{max_choices} 个最贴切描述这张图的标签。\n"
        f"闭集（只能从其中选取）：{closed}\n"
        f"原描述参考（可能不准）：{description_hint or '(无)'}\n\n"
        f"判断要点：图里物体/场景是什么 + 体现了哪种概念。优先选具体的，避免泛标签。\n"
        f"严格输出 JSON：{{\"scene_tags\": [...], \"rationale\": \"一句话原因\"}}\n"
        f"不要在 JSON 之外输出任何内容。scene_tags 元素必须出自闭集。"
    )
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": 200,
        "temperature": 0.2,
    }
    try:
        resp = _http_post_json(f"{ARK_BASE}/chat/completions", key, payload, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, json.JSONDecodeError) as e:
        return {"ok": False, "scene_tags": [], "rationale": "",
                "cost": 0.0, "provider": "ark", "error": _err(e)}

    text = ""
    try:
        text = resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return {"ok": False, "scene_tags": [], "rationale": "",
                "cost": 0.0, "provider": "ark",
                "error": f"unexpected resp: {str(resp)[:200]}"}
    # 模型偶尔包 ```json ... ``` 外壳
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # fallback：粗暴匹配 closed_set 词
        hits = [t for t in closed_set if t in text]
        return {"ok": True, "scene_tags": hits[:max_choices],
                "rationale": text[:120], "cost": VISION_COST_CNY,
                "provider": "ark", "error": "json parse fallback"}
    tags = [t for t in (parsed.get("scene_tags") or []) if t in closed_set][:max_choices]
    rationale = (parsed.get("rationale") or "")[:200]
    return {"ok": True, "scene_tags": tags, "rationale": rationale,
            "cost": VISION_COST_CNY, "provider": "ark", "error": None}


def wait_video_task(
    task_id: str, *, interval: float = 5.0, timeout_sec: int = 600,
) -> dict[str, Any]:
    """阻塞轮询视频任务直至 succeeded / failed / 超时。调试脚本用；Web 端应走异步查询避免阻塞。"""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout_sec:
        last = get_video_task(task_id)
        if last["status"] in ("succeeded", "failed"):
            return last
        time.sleep(interval)
    return last or {"ok": False, "status": "failed", "error": "timeout", "video_url": None, "cost": 0.0}
