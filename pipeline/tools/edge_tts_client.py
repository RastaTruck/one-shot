"""Edge-TTS 客户端（微软免费 TTS）—— 用作火山声音复刻未开并发前的权宜之计。

对外暴露 2 个函数：
- `list_voices()` 列出中文预置音色
- `synthesize(text, voice, out_path)` 合成 mp3 到本地

依赖：edge-tts (pip install edge-tts)
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import edge_tts


# 中文预置音色精选 · 一期视频常用的几种声线
ZH_VOICES: list[dict] = [
    {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓 · 女声温暖亲和", "gender": "Female",
     "desc": "自然女声，偏日常口语，适合通用科普"},
    {"id": "zh-CN-YunxiNeural",    "name": "云希 · 男声轻快随性", "gender": "Male",
     "desc": "年轻男声，节奏明快，适合段子向"},
    {"id": "zh-CN-YunjianNeural",  "name": "云健 · 男声沉稳讲解", "gender": "Male",
     "desc": "低沉男声，严肃正式，适合硬核分析"},
    {"id": "zh-CN-XiaoyiNeural",   "name": "晓伊 · 女声灵动", "gender": "Female",
     "desc": "清脆女声，偏甜美，适合故事叙述"},
    {"id": "zh-CN-YunyangNeural",  "name": "云扬 · 男声新闻播报", "gender": "Male",
     "desc": "标准播音男声，字正腔圆，适合权威感内容"},
]


def _preprocess_text(text: str) -> str:
    """预处理 TTS 文本，减少标点触发呼吸音的概率。

    edge-tts 在中文标点处会插入 breath phoneme，把标点换成空格
    让引擎靠韵律停顿（breath phoneme 减少但无法完全消除，
    剩余的由 _remove_breath_audio 做音频级清除）。
    """
    text = re.sub(r'[，,、；;]', ' ', text)
    text = re.sub(r'[。！!？?…]+', ' ', text)
    text = re.sub(r'[：:【】（）《》""''—～「」\[\]()]+', ' ', text)
    text = re.sub(r'[ \s]{2,}', ' ', text)
    return text.strip()


async def _remove_breath_audio(path: Path) -> None:
    """用 ffmpeg 两步清除音频中的呼吸音。

    原理：
    1. agate（噪声门）：语音 ≈ -15dB，软音节 ≈ -30dB，呼吸音 ≈ -35~-45dB。
       threshold=-38dB(≈0.013)，ratio=50 → 呼吸音被压至 ≈ -72dB（听不见）。
       attack=2ms（快速闭门），release=100ms（自然收尾，不截断软音节）。
    2. areverse + silenceremove + areverse：把尾部被门控后的静音段裁掉，
       消除"气音被掐断"的尾巴。
    """
    tmp = path.with_suffix('.nb.mp3')
    af = (
        'agate=threshold=0.013:ratio=50:attack=2:release=100,'
        'areverse,'
        'silenceremove=start_periods=1:start_silence=0.08:start_threshold=-50dB,'
        'areverse'
    )
    cmd = [
        'ffmpeg', '-y', '-i', str(path),
        '-af', af,
        '-q:a', '2',          # VBR ~190 kbps，保持音质
        str(tmp),
    ]
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


def list_voices() -> list[dict]:
    return [dict(v) for v in ZH_VOICES]


def voice_exists(voice_id: str) -> bool:
    return any(v["id"] == voice_id for v in ZH_VOICES)


async def synthesize_async(text: str, voice: str, out_path: str | Path,
                            rate: str = "+0%", volume: str = "+0%") -> dict:
    """异步合成（FastAPI 内调用此版本）。"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not text.strip():
        return {"ok": False, "error": "empty text"}
    if not voice_exists(voice):
        return {"ok": False, "error": f"voice not in preset list: {voice}"}
    try:
        text = _preprocess_text(text)
        communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
        await communicate.save(str(out))
        # 音频级呼吸音清除
        await _remove_breath_audio(out)
        return {"ok": True, "path": str(out), "bytes": out.stat().st_size}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def synthesize(text: str, voice: str, out_path: str | Path,
               rate: str = "+0%", volume: str = "+0%") -> dict:
    """同步封装（脚本 / CLI 用）。不要在已有 event loop 里调。"""
    try:
        return asyncio.run(synthesize_async(text, voice, out_path, rate, volume))
    except RuntimeError as e:
        return {"ok": False,
                "error": f"already in event loop, use synthesize_async: {e}"}
