"""阿里云百炼 · VideoRetalk lipsync API · 异步任务封装。

调用流程：
  1. POST 提交任务 → 拿 task_id
  2. 轮询 GET 任务状态直到 SUCCEEDED 或 FAILED
  3. 拿到输出视频 URL（24 小时有效）

文档：https://help.aliyun.com/zh/model-studio/videoretalk-api
价格：0.08 元/秒 · 免费额度 1800 秒
限制：视频 2-120 秒、≤300MB；音频 2-120 秒、≤30MB
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

# 复用 llm_client 的 .env 加载，确保 import 时就能读到 DASHSCOPE_API_KEY
from pipeline.tools import llm_client as _llm_client  # noqa: F401
_llm_client._load_env_once()


_SUBMIT_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/image2video/video-synthesis/"
_QUERY_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
_MODEL = "videoretalk"


def _api_key() -> str:
    return os.getenv("DASHSCOPE_API_KEY", "").strip()


def is_configured() -> tuple[bool, str]:
    if not _api_key():
        return False, "DASHSCOPE_API_KEY 未配置（百炼 API Key）"
    return True, "ok"


def submit_task(
    video_url: str,
    audio_url: str,
    *,
    ref_image_url: str = "",
    video_extension: bool = False,
    query_face_threshold: int = 170,
) -> dict:
    """提交 lipsync 任务，返回 {ok, task_id, error?}."""
    key = _api_key()
    if not key:
        return {"ok": False, "error": "DASHSCOPE_API_KEY 未配置"}
    payload: dict[str, Any] = {
        "model": _MODEL,
        "input": {
            "video_url": video_url,
            "audio_url": audio_url,
        },
        "parameters": {
            "video_extension": video_extension,
            "query_face_threshold": query_face_threshold,
        },
    }
    if ref_image_url:
        payload["input"]["ref_image_url"] = ref_image_url

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    try:
        r = requests.post(_SUBMIT_URL, json=payload, headers=headers, timeout=30)
        data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"请求异常: {type(e).__name__}: {e}"}

    if r.status_code >= 400:
        return {"ok": False, "error": f"提交失败 [{r.status_code}]: {data}"}

    task_id = (data.get("output") or {}).get("task_id")
    if not task_id:
        return {"ok": False, "error": f"响应里没有 task_id: {data}"}
    return {"ok": True, "task_id": task_id, "raw": data}


def query_task(task_id: str) -> dict:
    """查任务状态。返回 {ok, status, video_url, error?, raw}."""
    key = _api_key()
    if not key:
        return {"ok": False, "error": "DASHSCOPE_API_KEY 未配置"}
    try:
        r = requests.get(
            _QUERY_URL.format(task_id=task_id),
            headers={"Authorization": f"Bearer {key}"},
            timeout=30,
        )
        data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"请求异常: {type(e).__name__}: {e}"}

    if r.status_code >= 400:
        return {"ok": False, "error": f"查询失败 [{r.status_code}]: {data}"}

    output = data.get("output") or {}
    status = output.get("task_status", "UNKNOWN")
    return {
        "ok": True,
        "status": status,
        "video_url": output.get("video_url"),
        "submit_time": output.get("submit_time"),
        "scheduled_time": output.get("scheduled_time"),
        "end_time": output.get("end_time"),
        "raw": data,
    }


def poll_until_done(
    task_id: str,
    *,
    poll_interval_sec: float = 5.0,
    timeout_sec: float = 600.0,
) -> dict:
    """阻塞轮询直到任务完成或失败。
    返回 {ok, status, video_url?, error?}.
    """
    start = time.time()
    last_status = ""
    while True:
        if time.time() - start > timeout_sec:
            return {"ok": False, "error": f"轮询超时 ({timeout_sec}s) · 最后状态={last_status}"}
        r = query_task(task_id)
        if not r.get("ok"):
            return r
        last_status = r["status"]
        if last_status == "SUCCEEDED":
            return {"ok": True, "status": "SUCCEEDED", "video_url": r["video_url"], "raw": r["raw"]}
        if last_status == "FAILED":
            return {"ok": False, "error": "任务失败", "raw": r["raw"]}
        # PENDING / PRE-PROCESSING / RUNNING / POST-PROCESSING / UNKNOWN
        time.sleep(poll_interval_sec)
