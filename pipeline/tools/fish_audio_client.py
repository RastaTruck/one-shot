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


# [AI-NOTE] 2026-05-08: 用户自定义音色 — 持久化到 config/user_voices.json
# 排在 _OWN_VOICES 之后、公共热门之前。
_USER_VOICES_PATH = Path(__file__).resolve().parents[2] / "config" / "user_voices.json"


def _load_user_voices() -> list[dict]:
    """读用户自定义音色文件。文件不存在/解析失败时返回空 list。"""
    if not _USER_VOICES_PATH.exists():
        return []
    try:
        import json as _json
        data = _json.loads(_USER_VOICES_PATH.read_text(encoding="utf-8"))
        items = data.get("voices") or []
        # 校验最小字段
        return [
            {
                "id": str(v.get("id", "")),
                "name": str(v.get("name") or "未命名").strip(),
                "desc": str(v.get("desc") or "用户添加").strip(),
                "provider": "fish_audio",
                "user": True,  # 标记是用户添加的（前端可显示删除按钮）
            }
            for v in items if v.get("id")
        ]
    except Exception:
        return []


def _save_user_voices(voices: list[dict]) -> None:
    """写回用户自定义音色文件。"""
    import json as _json
    _USER_VOICES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"voices": [
        {"id": v["id"], "name": v.get("name", ""),
         "desc": v.get("desc", "用户添加"),
         "provider": "fish_audio"}
        for v in voices if v.get("id")
    ]}
    _USER_VOICES_PATH.write_text(
        _json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_user_voice(voice_id: str, name: str = "", desc: str = "") -> dict:
    """追加一条用户自定义音色（去重）。返回最终落盘的音色项。"""
    voice_id = (voice_id or "").strip()
    if not voice_id:
        return {"error": "voice_id 必填"}
    voices = _load_user_voices()
    if any(v["id"] == voice_id for v in voices):
        return {"error": "该音色 ID 已存在"}
    new_v = {
        "id": voice_id,
        "name": (name or f"自定义 {voice_id[:8]}").strip(),
        "desc": (desc or "用户添加").strip(),
        "provider": "fish_audio",
    }
    voices.append(new_v)
    _save_user_voices(voices)
    return {"ok": True, "voice": {**new_v, "user": True}}


def remove_user_voice(voice_id: str) -> bool:
    voices = _load_user_voices()
    before = len(voices)
    voices = [v for v in voices if v["id"] != voice_id]
    if len(voices) == before:
        return False
    _save_user_voices(voices)
    return True


def list_voices_sync(page_size: int = 30) -> list[dict]:
    """模块加载时同步拉取：自有声线（固定）+ 用户自定义（user_voices.json）+ 热门公共中文模型。
    失败仅返回自有 + 用户自定义。
    """
    user_voices = _load_user_voices()
    base = list(_OWN_VOICES) + user_voices  # 排序：自有 → 用户自定义 → 公共
    base_ids = {v["id"] for v in base}
    key = _api_key()
    if not key:
        return base
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
            public = [
                {
                    "id":   m["_id"],
                    "name": (m.get("title") or "未命名音色").strip(),
                    "desc": _fmt_tags(m.get("tags") or []),
                    "provider": "fish_audio",
                }
                for m in data.get("items", [])
                if m.get("_id") and m["_id"] not in base_ids   # 去重
            ]
            return base + public
    except Exception:
        return base


def voice_exists(voice_id: str, presets: list[dict]) -> bool:
    return any(v["id"] == voice_id for v in presets)


async def synthesize_s1_async(
    text: str, out_path: str | Path, *, normalize: bool = True
) -> dict:
    """[AI-NOTE] 2026-05-07: Outhan 步骤 5 用——S1 模型 + 默认音色（无 reference_id）。
    与一二布布的 synthesize_async（reference_id 必填）共存。
    """
    key = _api_key()
    if not key:
        return {"ok": False, "error": "FISH_AUDIO_API_KEY not set"}
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not text.strip():
        return {"ok": False, "error": "empty text"}
    try:
        async with httpx.AsyncClient(timeout=_SYNTH_TIMEOUT) as client:
            r = await client.post(
                f"{BASE_URL}/v1/tts",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "model": "s1",
                },
                json={
                    "text": text,
                    "format": "mp3",
                    "normalize": normalize,
                    "mp3_bitrate": 128,
                    "streaming": False,
                },
            )
            r.raise_for_status()
            out.write_bytes(r.content)
            return {"ok": True, "path": str(out), "bytes": out.stat().st_size}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:300]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


async def synthesize_async(
    text: str, reference_id: str, out_path: str | Path,
    *, use_s1: bool = False,
) -> dict:
    """异步合成一段配音 mp3，成功返回 {ok, path, bytes}。

    [AI-NOTE] 2026-05-08: 加 use_s1 开关——
    · use_s1=False（默认，一二布布老流程兼容）：走 _preprocess_text 去标点防呼吸音 + 用默认 TTS-1 模型
    · use_s1=True（Outhan 流程）：保留 (happy)(calm) 等 Fish S1 情感标签 +
      走 model=s1 让标签生效 + 跳过 _preprocess_text（不能删 () 圆括号）+ 跳过呼吸音去除（S1 输出干净）
    """
    key = _api_key()
    if not key:
        return {"ok": False, "error": "FISH_AUDIO_API_KEY not set"}
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not text.strip():
        return {"ok": False, "error": "empty text"}
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if use_s1:
        headers["model"] = "s1"
    try:
        # S1 模式必须保留括号给情感标签识别；老模式按一二布布旧逻辑去标点
        cleaned = text if use_s1 else _preprocess_text(text)
        body: dict = {
            "reference_id": reference_id,
            "text": cleaned,
            "format": "mp3",
            "mp3_bitrate": 128,
            "streaming": False,
        }
        if use_s1:
            body["normalize"] = True  # S1 数字/英文归一化
            # [AI-NOTE] 2026-05-08: model 同时放 body 双保险——Fish 文档说 header 即可，
            # 但部分网关版本只看 body。S1 + reference_id 组合下情感标签识别有时不稳，加这层冗余
            body["model"] = "s1"
        async with httpx.AsyncClient(timeout=_SYNTH_TIMEOUT) as client:
            r = await client.post(
                f"{BASE_URL}/v1/tts",
                headers=headers,
                json=body,
            )
            r.raise_for_status()
            out.write_bytes(r.content)
            # 老 TTS-1 模式才做呼吸音裁剪；S1 自带干净不裁
            if not use_s1:
                await _remove_breath_audio(out)
            return {"ok": True, "path": str(out), "bytes": out.stat().st_size}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
