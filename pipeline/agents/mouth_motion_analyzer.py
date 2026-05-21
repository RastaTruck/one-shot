"""嘴动节奏识别 · 用 OpenCV 帧差法对 take 视频做嘴部运动强度分析。

不用 mediapipe / 不用 ML 模型 —— 卡通角色也能跑。
默认 ROI 取下半画面（卡通角色嘴+下巴+脖子的位置），后续可加用户手动框定。

输出：
  motion_per_frame: list[float]  归一化 0-1，每帧嘴动强度
  motion_per_sec:   list[float]  每秒平均
  motion_curve_resampled: list[float]  重采样到固定密度（10/s）便于跨 take 比较
  avg_motion, peak_motion, peak_density
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np


def analyze_take(
    video_path: str | Path,
    *,
    roi: tuple[int, int, int, int] | None = None,
    samples_per_sec: int = 10,
) -> dict[str, Any]:
    """对视频做帧差分析。
    roi=(x,y,w,h) 嘴部矩形；None 则默认取下半画面。
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return {"ok": False, "error": f"无法打开视频: {video_path}"}
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w_total = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h_total = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    motion_per_frame: list[float] = []
    prev_gray = None

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if roi:
            x, y, rw, rh = roi
            patch = frame[y:y + rh, x:x + rw]
        else:
            # 默认 ROI：图像下半画面（卡通角色嘴+下巴大致位置）
            patch = frame[h_total // 2:, :]
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        if prev_gray is not None and gray.shape == prev_gray.shape:
            diff = np.abs(gray.astype(np.int16) - prev_gray.astype(np.int16))
            motion_per_frame.append(float(diff.mean()))
        prev_gray = gray
    cap.release()

    if not motion_per_frame:
        return {"ok": False, "error": "没有读到帧"}

    arr = np.asarray(motion_per_frame, dtype=np.float32)
    # 用 95 分位数归一化，避免极端值把曲线压扁
    norm_factor = max(float(np.percentile(arr, 95)), 1.0)
    motion_norm = np.clip(arr / norm_factor, 0.0, 1.0)

    # 每秒平均
    fps_int = max(1, int(round(fps)))
    motion_per_sec = [
        float(motion_norm[i:i + fps_int].mean())
        for i in range(0, len(motion_norm), fps_int)
    ]

    # 重采样到固定密度（默认 10 点/秒），便于跨 take 用同长度向量做相似度
    duration_sec = n_frames / fps
    n_resamp = max(1, int(round(duration_sec * samples_per_sec)))
    if len(motion_norm) >= n_resamp:
        idx = np.linspace(0, len(motion_norm) - 1, n_resamp).astype(int)
        resampled = motion_norm[idx]
    else:
        resampled = np.interp(
            np.linspace(0, len(motion_norm) - 1, n_resamp),
            np.arange(len(motion_norm)),
            motion_norm,
        )

    return {
        "ok": True,
        "fps": fps,
        "duration_sec": duration_sec,
        "frame_count": n_frames,
        "video_size": [w_total, h_total],
        "roi": list(roi) if roi else None,
        "samples_per_sec": samples_per_sec,
        "motion_per_sec": [round(x, 4) for x in motion_per_sec],
        "motion_curve": [round(float(x), 4) for x in resampled.tolist()],
        "avg_motion": float(arr.mean()),
        "peak_motion": float(arr.max()),
        "peak_density": float((motion_norm > 0.5).mean()),  # 强动帧占比
    }


def motion_window(
    motion_curve: list[float],
    *,
    start_sec: float,
    length_sec: float,
    samples_per_sec: int = 10,
) -> list[float]:
    """从已计算好的 motion_curve 上截取 [start, start+length] 这段。
    可在 take 内做"子窗口"匹配时用。
    """
    s = int(round(start_sec * samples_per_sec))
    n = int(round(length_sec * samples_per_sec))
    s = max(0, min(s, len(motion_curve) - 1))
    return motion_curve[s:s + n]
