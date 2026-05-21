"""配音节奏识别 · librosa RMS 算每秒说话强度曲线。

输出 rhythm_curve（同 samples_per_sec=10）便于跟嘴动曲线直接做相似度比对。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import librosa
import numpy as np


def analyze_audio(
    audio_path: str | Path,
    *,
    samples_per_sec: int = 10,
) -> dict[str, Any]:
    """RMS 音量 → 归一化的"说话强度曲线"。"""
    y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    duration = len(y) / sr

    # hop_length 让一秒约 samples_per_sec 个 frame
    hop_length = max(1, int(sr / samples_per_sec))
    rms = librosa.feature.rms(y=y, frame_length=hop_length * 2, hop_length=hop_length)[0]

    # 归一化（95 分位避免极端值）
    norm_factor = max(float(np.percentile(rms, 95)), 1e-6)
    rms_norm = np.clip(rms / norm_factor, 0.0, 1.0)

    # 重采样到固定密度
    n_resamp = max(1, int(round(duration * samples_per_sec)))
    if len(rms_norm) >= n_resamp:
        idx = np.linspace(0, len(rms_norm) - 1, n_resamp).astype(int)
        resampled = rms_norm[idx]
    else:
        resampled = np.interp(
            np.linspace(0, len(rms_norm) - 1, n_resamp),
            np.arange(len(rms_norm)),
            rms_norm,
        )

    return {
        "ok": True,
        "duration_sec": duration,
        "samples_per_sec": samples_per_sec,
        "rhythm_curve": [round(float(x), 4) for x in resampled.tolist()],
        "avg_volume": float(rms_norm.mean()),
        "speech_density": float((rms_norm > 0.2).mean()),  # 说话帧占比
    }


def rhythm_window(
    rhythm_curve: list[float],
    *,
    start_sec: float,
    length_sec: float,
    samples_per_sec: int = 10,
) -> list[float]:
    s = int(round(start_sec * samples_per_sec))
    n = int(round(length_sec * samples_per_sec))
    s = max(0, min(s, len(rhythm_curve) - 1))
    return rhythm_curve[s:s + n]
