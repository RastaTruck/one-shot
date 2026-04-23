"""LLM 客户端：DeepSeek（首选）/ Claude（备选）。

只用 stdlib（urllib）避免新增依赖；需要密钥就读 `.env`。
缺失密钥时返回 None，让上层 agent 走降级分支。
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


def call_deepseek_json(system: str, user: str, timeout: int = 240) -> tuple[str, float] | None:
    """返回 (content_str, cost_cny) 或 None（无密钥/失败）。content 保证是 JSON 字符串。"""
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
        # DeepSeek-chat: 输入 ~¥0.001/1k，输出 ~¥0.002/1k
        cost = (usage.get("prompt_tokens", 0) * 0.001 + usage.get("completion_tokens", 0) * 0.002) / 1000
        return content, round(cost, 4)
    except (KeyError, IndexError):
        return None


def call_claude_json(system: str, user: str, timeout: int = 240) -> tuple[str, float] | None:
    """Claude 备用通道，仅 JSON 模式。"""
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
        # Haiku 4.5: 输入 ~¥0.007/1k，输出 ~¥0.035/1k
        cost = (usage.get("input_tokens", 0) * 0.007 + usage.get("output_tokens", 0) * 0.035) / 1000
        # Claude 可能带 markdown 包裹，剥一下
        c = content.strip()
        if c.startswith("```"):
            c = c.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            if c.startswith("json"):
                c = c[4:].strip()
        return c, round(cost, 4)
    except (KeyError, IndexError):
        return None


def call_json_with_fallback(system: str, user: str, timeout: int = 240) -> tuple[str, str, float] | None:
    """依次尝试 DeepSeek → Claude。返回 (provider, content, cost) 或 None。"""
    r = call_deepseek_json(system, user, timeout=timeout)
    if r is not None:
        return "deepseek", r[0], r[1]
    r = call_claude_json(system, user, timeout=timeout)
    if r is not None:
        return "claude", r[0], r[1]
    return None
