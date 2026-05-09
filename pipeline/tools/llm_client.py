"""LLM 客户端：Gemini（首选）/ DeepSeek（备）/ Claude（末备）。

只用 stdlib（urllib）避免新增依赖；需要密钥就读 `.env`。
缺失密钥时返回 None，让上层 agent 走降级分支。

调用链：call_json_with_fallback → Gemini → DeepSeek → Claude
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

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


def _http_post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={**headers, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post_json_retry(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int,
    *, retries: int = 3,
) -> dict[str, Any]:
    """[AI-NOTE] 2026-05-08: 给 Gemini 调用加 SSL EOF 重试——
    generativelanguage.googleapis.com 偶尔握手抖动，跟 Volces 同款问题。
    每次失败退避 2/5 秒；最后一次失败仍然抛原异常给调用方处理。"""
    import time as _time
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return _http_post_json(url, headers, payload, timeout)
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as e:
            last_exc = e
            err_str = str(e)
            # 仅对网络/SSL 抖动重试；HTTP 4xx/5xx（包含 quota / 安全策略）不重试
            transient = any(k in err_str for k in [
                "UNEXPECTED_EOF", "SSL", "timed out", "Connection reset",
                "WSAECONNRESET", "ConnectionError", "RemoteDisconnected",
            ])
            if not transient or attempt == retries - 1:
                raise
            print(f"[http-retry] attempt {attempt+1}/{retries} 抖动重试：{err_str[:120]}",
                  file=__import__('sys').stderr)
            _time.sleep(2 + attempt * 3)  # 2s, 5s
    if last_exc:
        raise last_exc
    raise RuntimeError("unreachable")


# ==================== Gemini 多模态（图/视频 + 文本）====================

def call_gemini_multimodal_text(
    system: str,
    user: str,
    *,
    media: list[dict[str, str]] | None = None,
    model: str | None = None,
    timeout: int = 300,
) -> tuple[str, float] | None:
    """Gemini 多模态调用，输入文本+inline 媒体（图/视频 base64），返回纯文本。

    [AI-NOTE] 2026-05-07: Outhan 步骤 1 的"上传参考视频自动总结主题"用这个。
    media: [{"mimeType": "video/mp4", "data": "<base64>"}]
    """
    _load_env_once()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or key.startswith("AIzaSy-xxx"):
        return None
    used_model = model or os.environ.get("GEMINI_MODEL_PRO", "gemini-2.5-pro").strip()
    url = (
        f"https://generativelanguage.googleapis.com/v1beta"
        f"/models/{used_model}:generateContent?key={key}"
    )
    parts: list[dict[str, Any]] = [{"text": user}]
    for m in (media or []):
        parts.append({"inlineData": {"mimeType": m["mimeType"], "data": m["data"]}})
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
        # [AI-NOTE] 2026-05-08: 4096 → 16384，给 lines schema（多段 × 多 line）足够输出空间
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 16384},
    }
    try:
        # [AI-NOTE] 2026-05-08: 用 retry 版本，对 SSL EOF 等网络抖动 3 次重试
        resp = _http_post_json_retry(url, {}, payload, timeout=timeout, retries=3)
    except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError, OSError) as e:
        # 失败时不再静默吞——打到 stderr 方便排查
        print(f"[gemini-multimodal] HTTP 失败（已重试 3 次）：{type(e).__name__}: {str(e)[:200]}",
              file=__import__('sys').stderr)
        return None
    try:
        candidates = resp.get("candidates") or []
        if not candidates:
            # 安全策略 / promptFeedback 拒绝 / 配额耗尽
            pf = resp.get("promptFeedback", {})
            print(f"[gemini-multimodal] 无 candidates：promptFeedback={pf}, resp_keys={list(resp.keys())}",
                  file=__import__('sys').stderr)
            return None
        cand = candidates[0]
        finish = cand.get("finishReason", "")
        text_parts = (cand.get("content") or {}).get("parts") or []
        content = "".join(p.get("text", "") for p in text_parts).strip()
        if not content:
            # 最常见：finishReason=MAX_TOKENS（输出截断时 parts 是空的）/ SAFETY / RECITATION
            print(f"[gemini-multimodal] 空内容 finish={finish}, parts_count={len(text_parts)}",
                  file=__import__('sys').stderr)
            return None
        if finish == "MAX_TOKENS":
            # 截断了但还是有部分内容——后端解析 JSON 时会报错；这里先打告警
            print(f"[gemini-multimodal] WARN: 输出被 MAX_TOKENS 截断（{len(content)} chars）",
                  file=__import__('sys').stderr)
        usage = resp.get("usageMetadata", {})
        in_tok = usage.get("promptTokenCount", 0)
        out_tok = usage.get("candidatesTokenCount", 0)
        # 2.5-pro 比 flash 贵约 8x；用 flash 价格估算 + 2x 安全缓冲
        cost = (in_tok * 0.00216 + out_tok * 0.00864) / 1000
        return content, round(cost, 6)
    except (KeyError, IndexError, TypeError) as e:
        print(f"[gemini-multimodal] 解析响应失败 {type(e).__name__}: {e}; resp_keys={list(resp.keys()) if isinstance(resp, dict) else type(resp)}",
              file=__import__('sys').stderr)
        return None


# ==================== Gemini ====================

def call_gemini_json(system: str, user: str, timeout: int = 240) -> tuple[str, float] | None:
    """Gemini 2.5 Flash，JSON 模式。返回 (content_str, cost_cny) 或 None。

    定价（2025-05）：
      gemini-2.5-flash  输入 $0.15/1M tok  输出 $0.60/1M tok
      折合人民币（×7.2）≈ 输入 ¥0.00108/1k  输出 ¥0.00432/1k
    """
    _load_env_once()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or key.startswith("AIzaSy-xxx"):
        return None
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
    url = (
        f"https://generativelanguage.googleapis.com/v1beta"
        f"/models/{model}:generateContent?key={key}"
    )
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.8,
            "maxOutputTokens": 8192,
        },
    }
    try:
        # [AI-NOTE] 2026-05-08: SSL EOF 等抖动 3 次重试
        resp = _http_post_json_retry(url, {}, payload, timeout=timeout, retries=3)
    except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError, OSError):
        return None
    try:
        content = resp["candidates"][0]["content"]["parts"][0]["text"]
        usage = resp.get("usageMetadata", {})
        in_tok  = usage.get("promptTokenCount", 0)
        out_tok = usage.get("candidatesTokenCount", 0)
        cost = (in_tok * 0.00108 + out_tok * 0.00432) / 1000
        return content.strip(), round(cost, 6)
    except (KeyError, IndexError):
        return None


# ==================== DeepSeek ====================

def call_deepseek_json(system: str, user: str, timeout: int = 240) -> tuple[str, float] | None:
    """返回 (content_str, cost_cny) 或 None（无密钥/失败）。"""
    _load_env_once()
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key or key.startswith("sk-xxx"):
        return None
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    try:
        resp = _http_post_json(
            f"{base}/chat/completions",
            {"Authorization": f"Bearer {key}"},
            {
                "model": "deepseek-chat",
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_object"},
                "temperature": 0.8,
            },
            timeout=timeout,
        )
    except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError, OSError):
        return None
    try:
        content = resp["choices"][0]["message"]["content"]
        usage = resp.get("usage", {})
        cost = (usage.get("prompt_tokens", 0) * 0.001 + usage.get("completion_tokens", 0) * 0.002) / 1000
        return content, round(cost, 4)
    except (KeyError, IndexError):
        return None


# ==================== Claude ====================

def call_claude_json(system: str, user: str, timeout: int = 240) -> tuple[str, float] | None:
    """Claude 末备通道，仅 JSON 模式。"""
    _load_env_once()
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key or key.startswith("sk-ant-xxx"):
        return None
    try:
        resp = _http_post_json(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 4000,
                "system": system + "\n\n必须返回纯 JSON，不要 markdown 代码块。",
                "messages": [{"role": "user", "content": user}],
            },
            timeout=timeout,
        )
    except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError, OSError):
        return None
    try:
        content = resp["content"][0]["text"]
        usage = resp.get("usage", {})
        cost = (usage.get("input_tokens", 0) * 0.007 + usage.get("output_tokens", 0) * 0.035) / 1000
        c = content.strip()
        if c.startswith("```"):
            c = c.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            if c.startswith("json"):
                c = c[4:].strip()
        return c, round(cost, 4)
    except (KeyError, IndexError):
        return None


# ==================== 统一入口 ====================

def call_json_with_fallback(system: str, user: str, timeout: int = 240) -> tuple[str, str, float] | None:
    """依次尝试 Gemini → DeepSeek → Claude。返回 (provider, content, cost) 或 None。"""
    r = call_gemini_json(system, user, timeout=timeout)
    if r is not None:
        return "gemini", r[0], r[1]
    r = call_deepseek_json(system, user, timeout=timeout)
    if r is not None:
        return "deepseek", r[0], r[1]
    r = call_claude_json(system, user, timeout=timeout)
    if r is not None:
        return "claude", r[0], r[1]
    return None
