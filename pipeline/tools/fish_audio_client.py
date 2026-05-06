"""Fish Audio TTS 客户端。

对外暴露：
- list_voices_sync()  → 拉公共中文模型 Top-N（同步，供模块初始化用）
- synthesize_async()  → 异步合成 mp3（FastAPI 内调用）

依赖：httpx（已在 fastapi 依赖链中），FISH_AUDIO_API_KEY 环境变量。
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import httpx

BASE_URL = "https://api.fish.audio"


def _preprocess_text(text: str) -> str:
    """预处理 TTS 文本，减少标点触发呼吸音概率。"""
    text = re.sub(r'[，,、；;]', ' ', text)
    text = re.sub(r'[。.！!？?…]+', ' ', text)
    text = re.sub(r'[：:【】（）《》""''—～「」\[\]()]+', ' ', text)
    text = re.sub(r'[ \s]{2,}', ' ', text)
    return text.strip()


async def _remove_breath_audio(path: Path) -> None:
    """ffmpeg 噪声门 + 尾部静音裁剪，清除呼吸音。"""
    tmp = path.with_suffix('.nb.mp3')
    af = (
        'agate=threshold=0.013:ratio=50:attack=2:release=100,'
        'areverse,'
        'silenceremove=start_periods=1:start_silence=0.08:start_threshold=-50dB,'
        'areverse'
    )
    cmd = ['ffmpeg', '-y', '-i', str(path), '-af', af, '-q:a', '2', str(tmp)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 500:
            tmp.replace(path)
        elif tmp.exists():
            tmp.unlink()
    except Exception:
        if tmp.exists():
            tmp.unlink()
_SYNTH_TIMEOUT = 60.0   # TTS 生成可能较慢，给足时间
_LIST_TIMEOUT  = 15.0

_ENV_LOADED = False


def _load_env_once() -> None:
    """从 config/.env 读取并注入环境变量（同 ark_client 同款逻辑）。"""
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
    return os.environ.get("FISH_AUDIO_API_KEY", "")


def _fmt_tags(tags: list) -> str:
    """把英文 tag 列表转成可读中文描述。"""
    tag_map = {
        "female": "女声", "male": "男声",
        "young": "年轻", "middle-aged": "中年",
        "professional": "专业", "educational": "教育",
        "narration": "叙述", "documentary": "纪录片",
        "energetic": "活力", "calm": "沉稳",
        "friendly": "亲和", "authoritative": "权威",
        "social-media": "社媒向", "advertisement": "广告",
        "Mandarin": "普通话", "Mandarin Chinese": "普通话",
        "cinematic": "影视感",
    }
    zh = [tag_map[t] for t in tags if t in tag_map]
    return "·".join(zh[:4]) if zh else "Fish Audio 音色"


# 项目自有声线（私有模型，固定置顶）
_OWN_VOICES: list[dict] = [
    {
        "id":       "60b2fdc4111a46a591e0aef4a1c99f1c",
        "name":     "一二布布 · 专属声线",
        "desc":     "项目定制音色",
        "provider": "fish_audio",
        "own":      True,
    },
]


def list_voices_sync(page_size: int = 8) -> list[dict]:
    """模块加载时同步拉取：自有声线（固定）+ 热门公共中文模型。失败仅返回自有声线。"""
    key = _api_key()
    if not key:
        return list(_OWN_VOICES)
    try:
        with httpx.Client(timeout=_LIST_TIMEOUT) as client:
            r = client.get(
                f"{BASE_URL}/model",
                params={
                    "page_size": page_size,
                    "page_number": 1,
                    "language": "zh",
                    "sort_by": "task_count",
                },
                headers={"Authorization": f"Bearer {key}"},
            )
            r.raise_for_status()
            data = r.json()
            own_ids = {v["id"] for v in _OWN_VOICES}
            public = [
                {
                    "id":   m["_id"],
                    "name": (m.get("title") or "未命名音色").strip(),
                    "desc": _fmt_tags(m.get("tags") or []),
                    "provider": "fish_audio",
                }
                for m in data.get("items", [])
                if m.get("_id") and m["_id"] not in own_ids   # 去重
            ]
            return list(_OWN_VOICES) + public
    except Exception:
        return list(_OWN_VOICES)


def voice_exists(voice_id: str, presets: list[dict]) -> bool:
    return any(v["id"] == voice_id for v in presets)


async def synthesize_async(text: str, reference_id: str, out_path: str | Path) -> dict:
    """异步合成一段配音 mp3，成功返回 {ok, path, bytes}。"""
    key = _api_key()
    if not key:
        return {"ok": False, "error": "FISH_AUDIO_API_KEY not set"}
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not text.strip():
        return {"ok": False, "error": "empty text"}
    try:
        cleaned = _preprocess_text(text)
        async with httpx.AsyncClient(timeout=_SYNTH_TIMEOUT) as client:
            r = await client.post(
                f"{BASE_URL}/v1/tts",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "reference_id": reference_id,
                    "text": cleaned,
                    "format": "mp3",
                    "mp3_bitrate": 128,
                    "streaming": False,
                },
            )
            r.raise_for_status()
            out.write_bytes(r.content)
            await _remove_breath_audio(out)
            return {"ok": True, "path": str(out), "bytes": out.stat().st_size}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
