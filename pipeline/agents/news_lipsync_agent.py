"""新闻口播管线 · Lipsync 业务编排层。

把 OSS 上传 + VideoRetalk 调用 + 结果回写 黏在一起：
  1. 把本地最终视频和配音上传 OSS 拿临时 URL
  2. 调 VideoRetalk 提交任务 + 轮询
  3. 下载 lipsync 后的视频回本地 → 写入 news_meta
  4. 清理 OSS 临时文件（可选）

调用：
  result = run_lipsync(video_id, log=print)
  → {"ok": True, "lipsync_video_path": "..."}  # 项目相对路径
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import requests

from pipeline import news_meta as _news_meta
from pipeline.tools import lipsync_client as _lc
from pipeline.tools import oss_uploader as _oss


_DATA_ROOT = Path(__file__).resolve().parent.parent.parent / "data"
_OUT_DIR = _DATA_ROOT / "news_video"


def _log(msg: str, log: Callable[[str], None] | None) -> None:
    if log:
        try:
            log(msg)
        except Exception:
            pass


def run_lipsync(
    video_id: str,
    *,
    poll_interval_sec: float = 5.0,
    timeout_sec: float = 600.0,
    log: Callable[[str], None] | None = None,
) -> dict:
    """主入口 · 同步阻塞跑完整个 lipsync 流程。"""
    # ---- 0. 环境检查 ----
    ok, msg = _lc.is_configured()
    if not ok:
        return {"ok": False, "error": msg}
    ok, msg = _oss.is_configured()
    if not ok:
        return {"ok": False, "error": msg}

    # ---- 1. 取项目状态 ----
    data = _news_meta.get_news_data(video_id)
    video_rel = data.get("final_video_path")
    audio_rel = data.get("audio_path")
    if not video_rel or not audio_rel:
        return {"ok": False, "error": "项目还没有合成视频或配音，请先跑步骤 4-5"}

    video_local = _DATA_ROOT.parent / video_rel
    audio_local = _DATA_ROOT.parent / audio_rel
    if not video_local.exists():
        return {"ok": False, "error": f"视频文件丢失: {video_rel}"}
    if not audio_local.exists():
        return {"ok": False, "error": f"配音文件丢失: {audio_rel}"}

    # ---- 2. 上传 OSS 拿临时 URL ----
    _log(f"[lipsync] 上传视频到 OSS: {video_local.name}", log)
    r_v = _oss.upload_file(video_local, key_prefix=f"lipsync/{video_id}", expires_sec=3600)
    if not r_v.get("ok"):
        return {"ok": False, "error": f"视频上传失败: {r_v.get('error')}"}
    _log(f"[lipsync] 上传配音到 OSS: {audio_local.name}", log)
    r_a = _oss.upload_file(audio_local, key_prefix=f"lipsync/{video_id}", expires_sec=3600)
    if not r_a.get("ok"):
        return {"ok": False, "error": f"配音上传失败: {r_a.get('error')}"}

    video_url = r_v["url"]
    audio_url = r_a["url"]
    oss_keys = [r_v["key"], r_a["key"]]

    # ---- 3. 提交 VideoRetalk 任务 ----
    _log(f"[lipsync] 提交 VideoRetalk 任务...", log)
    sub = _lc.submit_task(video_url=video_url, audio_url=audio_url)
    if not sub.get("ok"):
        _cleanup_oss(oss_keys)
        return {"ok": False, "error": f"提交任务失败: {sub.get('error')}"}
    task_id = sub["task_id"]
    _log(f"[lipsync] task_id={task_id} · 开始轮询...", log)

    # ---- 4. 轮询 ----
    start = time.time()
    last_status = ""
    while True:
        if time.time() - start > timeout_sec:
            _cleanup_oss(oss_keys)
            return {"ok": False, "error": f"轮询超时 ({timeout_sec}s) · 最后状态={last_status}", "task_id": task_id}
        q = _lc.query_task(task_id)
        if not q.get("ok"):
            _cleanup_oss(oss_keys)
            return {"ok": False, "error": q.get("error"), "task_id": task_id}
        s = q["status"]
        if s != last_status:
            _log(f"[lipsync] status: {s}", log)
            last_status = s
        if s == "SUCCEEDED":
            out_url = q.get("video_url")
            if not out_url:
                _cleanup_oss(oss_keys)
                return {"ok": False, "error": "任务成功但没有 video_url", "task_id": task_id}
            break
        if s == "FAILED":
            _cleanup_oss(oss_keys)
            return {"ok": False, "error": "任务失败 · 请检查 video/audio 是否符合 VideoRetalk 输入要求（清晰正脸 / 2-120s / ≤300MB）", "task_id": task_id, "raw": q.get("raw")}
        time.sleep(poll_interval_sec)

    # ---- 5. 下载结果到本地 ----
    out_dir = _OUT_DIR / video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "lipsync.mp4"
    _log(f"[lipsync] 下载结果到 {out_path.name}...", log)
    try:
        with requests.get(out_url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
    except Exception as e:
        _cleanup_oss(oss_keys)
        return {"ok": False, "error": f"下载结果失败: {type(e).__name__}: {e}", "task_id": task_id, "remote_url": out_url}

    rel_path = str(out_path.relative_to(_DATA_ROOT.parent))
    _news_meta.update_news_data(video_id, lipsync_video_path=rel_path)

    _cleanup_oss(oss_keys)
    return {
        "ok": True,
        "lipsync_video_path": rel_path,
        "task_id": task_id,
        "elapsed_sec": round(time.time() - start, 1),
    }


def _cleanup_oss(keys: list[str]) -> None:
    for k in keys:
        try:
            _oss.delete_object(k)
        except Exception:
            pass
