"""阿里云 OSS 文件上传 · 给 VideoRetalk 提供公网可访问的临时 URL。

VideoRetalk API 要求 video_url / audio_url 必须公网可访问，本地路径不行。
所以上传到 OSS → 拿签名 URL 给阿里云内部去拉。

环境变量：
  ALIYUN_OSS_ACCESS_KEY_ID
  ALIYUN_OSS_ACCESS_KEY_SECRET
  ALIYUN_OSS_BUCKET
  ALIYUN_OSS_ENDPOINT (默认 oss-cn-beijing.aliyuncs.com)
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import oss2

# 复用 llm_client 的 .env 加载逻辑，确保模块导入即可读取 env
from pipeline.tools import llm_client as _llm_client  # noqa: F401
_llm_client._load_env_once()


def _config() -> dict:
    return {
        "ak_id": os.getenv("ALIYUN_OSS_ACCESS_KEY_ID", "").strip(),
        "ak_secret": os.getenv("ALIYUN_OSS_ACCESS_KEY_SECRET", "").strip(),
        "bucket": os.getenv("ALIYUN_OSS_BUCKET", "").strip(),
        "endpoint": os.getenv("ALIYUN_OSS_ENDPOINT", "oss-cn-beijing.aliyuncs.com").strip(),
    }


def is_configured() -> tuple[bool, str]:
    cfg = _config()
    missing = [k for k, v in cfg.items() if not v and k != "endpoint"]
    if missing:
        return False, f"OSS 未配置: 缺 {missing}"
    return True, "ok"


def _get_bucket() -> oss2.Bucket:
    cfg = _config()
    auth = oss2.Auth(cfg["ak_id"], cfg["ak_secret"])
    endpoint = cfg["endpoint"]
    if not endpoint.startswith("http"):
        endpoint = f"https://{endpoint}"
    return oss2.Bucket(auth, endpoint, cfg["bucket"])


def upload_file(
    local_path: str | Path,
    *,
    key_prefix: str = "lipsync",
    expires_sec: int = 3600,
) -> dict:
    """上传本地文件到 OSS，返回 {ok, url, key, error?}.

    生成的 url 是签名的临时下载链接，过期后失效。expires_sec 默认 1 小时
    （VideoRetalk 任务 ≤ 几分钟就能跑完，1 小时够用）。
    """
    ok, msg = is_configured()
    if not ok:
        return {"ok": False, "error": msg}
    p = Path(local_path)
    if not p.exists():
        return {"ok": False, "error": f"本地文件不存在: {p}"}

    # 用 hash 给 key 加唯一性，相同文件不重复上传
    digest = hashlib.md5(p.read_bytes()).hexdigest()[:12]
    key = f"{key_prefix}/{digest}_{p.name}"

    try:
        bucket = _get_bucket()
        # 已存在则跳过上传
        if not bucket.object_exists(key):
            bucket.put_object_from_file(key, str(p))
        url = bucket.sign_url("GET", key, expires_sec, slash_safe=True)
        return {"ok": True, "url": url, "key": key, "size": p.stat().st_size}
    except oss2.exceptions.OssError as e:
        return {"ok": False, "error": f"OSS 错误: {e.code} {e.message}"}
    except Exception as e:
        return {"ok": False, "error": f"上传失败: {type(e).__name__}: {e}"}


def delete_object(key: str) -> bool:
    try:
        _get_bucket().delete_object(key)
        return True
    except Exception:
        return False
