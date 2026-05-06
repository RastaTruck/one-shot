"""火山引擎声音复刻 API 客户端（V1 · mega_tts 体系）。

三个核心动作：
1. `upload_audio(speaker_id, audio_bytes, audio_format)` — 提交音频样本到 /upload
2. `query_status(speaker_id)` — /status 查询训练状态（status 2 或 4 = 可用）
3. `synthesize_text(speaker_id, text)` — 合成 mp3（HTTP unidirectional 流式，简单版）

鉴权：header `Authorization: Bearer;{access_token}`（**注意分号**，火山规定）。

环境变量读自 config/.env：
  VOLC_SPEECH_APPID    应用 ID（纯数字）
  VOLC_SPEECH_TOKEN    Access Token
  VOLC_SPEECH_CLUSTER  集群（默认 volcano_icl）

返回 status 数值参考：
  0=待处理  1=训练中  2=可用（ICL 1.0）  3=失败  4=可用（ICL 2.0 / DiT）
"""
from __future__ import annotations

import base64
import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_ENV_LOADED = False

BASE = "https://openspeech.bytedance.com"
UPLOAD_URL = f"{BASE}/api/v1/mega_tts/audio/upload"
STATUS_URL = f"{BASE}/api/v1/mega_tts/status"
TTS_URL = f"{BASE}/api/v1/tts"  # 合成 · http 普通版
DEFAULT_CLUSTER = "volcano_icl"  # ICL 1.0；ICL 2.0 用 volcano_icl


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


def _get_credential(appid: str | None = None, token: str | None = None) -> tuple[str, str]:
    _load_env_once()
    appid = appid or os.environ.get("VOLC_SPEECH_APPID", "").strip()
    token = token or os.environ.get("VOLC_SPEECH_TOKEN", "").strip()
    if not appid or not token:
        raise RuntimeError("VOLC_SPEECH_APPID / VOLC_SPEECH_TOKEN 未配置，去 config/.env 填写")
    return appid, token


def _auth_header(token: str) -> dict[str, str]:
    # 火山要求 "Bearer;${token}"，分号不是冒号
    return {
        "Authorization": f"Bearer;{token}",
        "Content-Type": "application/json",
    }


def _http_post_json(url: str, headers: dict, payload: dict, timeout: int = 60) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return {"ok": True, "status_code": resp.status, "body": json.loads(raw)}
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
            parsed = json.loads(body)
        except Exception:
            parsed = None
        return {"ok": False, "status_code": e.code, "body": parsed,
                "error": f"HTTP {e.code}: {body[:300]}"}
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, json.JSONDecodeError) as e:
        return {"ok": False, "status_code": None, "body": None, "error": str(e)[:300]}


# =========================================================================
# 核心 API
# =========================================================================

def query_status(
    speaker_id: str,
    *,
    appid: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """查询 speaker_id 训练状态。

    正常响应 body.BaseResp.StatusCode = 0。status 含义见模块 docstring。
    """
    appid, token = _get_credential(appid, token)
    r = _http_post_json(
        STATUS_URL,
        headers=_auth_header(token),
        payload={"appid": appid, "speaker_id": speaker_id},
    )
    return r


def upload_audio(
    speaker_id: str,
    audio_path: str | Path,
    *,
    audio_format: str | None = None,
    language: int = 0,     # 0=中文
    model_type: int = 1,   # 1=ICL 1.0, 2=DiT 标准, 3=DiT 还原, 4=ICL 2.0
    source: int = 2,
    appid: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """上传一条音频样本进 speaker_id 训练队列。"""
    appid, token = _get_credential(appid, token)
    p = Path(audio_path)
    if not p.exists():
        raise FileNotFoundError(f"音频不存在：{p}")
    fmt = (audio_format or p.suffix.lstrip(".") or "mp3").lower()
    if fmt == "mpeg":
        fmt = "mp3"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    payload = {
        "speaker_id": speaker_id,
        "appid": appid,
        "audios": [{"audio_bytes": b64, "audio_format": fmt}],
        "source": source,
        "language": language,
        "model_type": model_type,
    }
    return _http_post_json(UPLOAD_URL, headers=_auth_header(token),
                            payload=payload, timeout=180)


def detect_speaker_id(
    candidates: list[str],
    *,
    appid: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """轮询几个候选 speaker_id 字符串，返回哪个能被 /status 识别。

    返回 {"speaker_id": <命中者> or None, "details": [(cand, status_code, raw_body)...]}.
    """
    results = []
    hit = None
    for c in candidates:
        r = query_status(c, appid=appid, token=token)
        body = r.get("body") or {}
        base = body.get("BaseResp") or {}
        sc = base.get("StatusCode")
        results.append({
            "candidate": c,
            "http_status": r.get("status_code"),
            "biz_code": sc,
            "biz_msg": base.get("StatusMessage"),
            "body": body,
        })
        if sc == 0:
            hit = c
            break
    return {"speaker_id": hit, "details": results}
