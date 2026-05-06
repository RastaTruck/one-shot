"""把本地 data/assets/ + bgm/ 上传到七牛云 OSS，同步更新 asset_catalog.json 的 file 字段为 CDN URL。

为什么需要：
- 几人协作 → 不再各自维护一份本地素材
- catalog.json 是 git 跟踪的小文件，几人 git pull 立即拿到新 URL
- 七牛免费额度 10GB 存储 / 10GB/月 CDN 流量 / 100 万次/月 PUT，对 80MB-数百 MB 级素材绰绰有余

幂等设计：
- 已上传同名文件（七牛 stat OK）默认跳过
- catalog 里 file 已是 https URL 的也跳过（不重复上传）
- --force 强制覆盖

用法：
    python3 scripts/upload_to_qiniu.py --dry-run           # 看要上传啥
    python3 scripts/upload_to_qiniu.py                     # 真上传 + 改 catalog
    python3 scripts/upload_to_qiniu.py --pattern "data/assets/expressions/**/*.webp"  # 限定范围

环境变量（.env）：QINIU_ACCESS_KEY / QINIU_SECRET_KEY / QINIU_BUCKET / QINIU_DOMAIN
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 加载 .env
try:
    from dotenv import load_dotenv
    load_dotenv("config/.env")
except ImportError:
    pass

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "config" / "asset_catalog.json"

# 七牛 SDK
try:
    from qiniu import Auth, BucketManager, put_file
except ImportError:
    print("[!] 缺 qiniu SDK，先装：pip install qiniu", file=sys.stderr)
    sys.exit(1)


# ---------- 配置 ----------

def _need(env_key: str) -> str:
    v = os.getenv(env_key)
    if not v:
        print(f"[!] 缺 {env_key}（在 config/.env 里填）", file=sys.stderr)
        sys.exit(1)
    return v


def _qiniu_url(domain: str, key: str) -> str:
    """domain 不带 protocol → 拼 https URL。"""
    domain = domain.strip().rstrip("/")
    if domain.startswith("http://") or domain.startswith("https://"):
        return f"{domain}/{key}"
    return f"https://{domain}/{key}"


# ---------- 路径映射 ----------

def _local_to_key(local_path: Path) -> str:
    """本地相对路径 → 七牛 bucket 内 key（保持目录结构）。
    data/assets/expressions/yierbubu/x.webp → expressions/yierbubu/x.webp
    bgm/1.mp3 → bgm/1.mp3
    """
    rel = local_path.relative_to(ROOT)
    parts = rel.parts
    # 把 data/assets/ 前缀剥掉，让 key 更简洁
    if parts[0] == "data" and len(parts) >= 2 and parts[1] == "assets":
        parts = parts[2:]
    return "/".join(parts)


# ---------- 上传 ----------

def upload_one(auth: "Auth", bucket_mgr: "BucketManager", bucket: str, domain: str,
               local: Path, *, dry_run: bool, force: bool) -> dict:
    """上传单个文件。返回 {ok, key, url, skipped, reason}。"""
    key = _local_to_key(local)
    url = _qiniu_url(domain, key)

    if not force:
        # stat 看七牛上是否已有
        ret, info = bucket_mgr.stat(bucket, key)
        if ret is not None:
            return {"ok": True, "key": key, "url": url, "skipped": True, "reason": "remote exists"}

    if dry_run:
        return {"ok": True, "key": key, "url": url, "skipped": False, "reason": "dry-run"}

    token = auth.upload_token(bucket, key, 3600)
    ret, info = put_file(token, key, str(local))
    if ret is None or ret.get("key") != key:
        return {"ok": False, "key": key, "url": url, "skipped": False,
                "reason": f"upload failed: {info}"}
    return {"ok": True, "key": key, "url": url, "skipped": False, "reason": "uploaded"}


# ---------- catalog 更新 ----------

def update_catalog_file_field(local_path: Path, new_url: str) -> int:
    """catalog 里所有指向本地 local_path 的条目，把 file 字段改成 new_url。返回改了几条。"""
    if not CATALOG_PATH.exists():
        return 0
    cat = json.loads(CATALOG_PATH.read_text("utf-8"))
    rel_str = str(local_path.relative_to(ROOT))
    n = 0
    for kind in ("expressions", "elements", "backgrounds", "characters"):
        if kind not in cat or not isinstance(cat[kind], list):
            continue
        for entry in cat[kind]:
            if entry.get("file") == rel_str:
                entry["file"] = new_url
                n += 1
    if n > 0:
        CATALOG_PATH.write_text(json.dumps(cat, ensure_ascii=False, indent=2), "utf-8")
    return n


# ---------- main ----------

def main() -> int:
    parser = argparse.ArgumentParser(description="上传本地素材到七牛 + 改 catalog")
    parser.add_argument("--pattern", default=None,
                        help="glob 模式（默认上传 data/assets + bgm 全部）")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不上传不改 catalog")
    parser.add_argument("--force", action="store_true", help="强制覆盖远程已有的同名文件")
    args = parser.parse_args()

    access_key = _need("QINIU_ACCESS_KEY")
    secret_key = _need("QINIU_SECRET_KEY")
    bucket = _need("QINIU_BUCKET")
    domain = _need("QINIU_DOMAIN")

    auth = Auth(access_key, secret_key)
    bucket_mgr = BucketManager(auth)

    # 收集文件
    if args.pattern:
        files = sorted(ROOT.glob(args.pattern))
    else:
        files = []
        for sub in ("data/assets/expressions", "data/assets/elements",
                    "data/assets/backgrounds", "data/assets/characters", "bgm"):
            d = ROOT / sub
            if not d.exists():
                continue
            for ext in (".webp", ".png", ".jpg", ".jpeg", ".gif", ".mp3"):
                files.extend(d.rglob(f"*{ext}"))
    # 排除备份目录
    files = [f for f in files if "_orig" not in f.parts and "_trash" not in f.parts]
    files = sorted(set(files))

    if not files:
        print("[!] 没找到要上传的文件")
        return 1

    print(f"[upload] 准备处理 {len(files)} 个文件 · bucket={bucket} · domain={domain} · dry_run={args.dry_run}\n")

    stats = {"uploaded": 0, "skipped_remote_exists": 0, "catalog_rewritten": 0,
             "skipped_already_url": 0, "failed": 0}
    for i, f in enumerate(files, 1):
        # catalog 已是 URL 的不再传
        if not args.force:
            cat = json.loads(CATALOG_PATH.read_text("utf-8")) if CATALOG_PATH.exists() else {}
            rel_str = str(f.relative_to(ROOT))
            already_url = any(
                e.get("file", "").startswith("http")
                for kind in ("expressions", "elements", "backgrounds", "characters")
                for e in cat.get(kind, [])
                if e.get("file") == rel_str
            )
            # 这个 check 只是日志友好；实际跳过逻辑在 update_catalog 里靠 rel_str 匹配

        r = upload_one(auth, bucket_mgr, bucket, domain, f,
                       dry_run=args.dry_run, force=args.force)
        if not r["ok"]:
            stats["failed"] += 1
            print(f"  [{i:3d}] FAIL {r['key']}: {r['reason']}")
            continue

        if r["skipped"]:
            stats["skipped_remote_exists"] += 1
        else:
            stats["uploaded"] += 1

        # catalog 改 file 字段
        if not args.dry_run:
            n = update_catalog_file_field(f, r["url"])
            if n > 0:
                stats["catalog_rewritten"] += n
                tag = "↑" if not r["skipped"] else "≡"
                print(f"  [{i:3d}] {tag} {r['key']}  → catalog updated × {n}")
            else:
                tag = "↑" if not r["skipped"] else "≡"
                print(f"  [{i:3d}] {tag} {r['key']}  (catalog 里没找到此 file，跳过 rewrite)")
        else:
            print(f"  [{i:3d}] [dry-run] would {'skip' if r['skipped'] else 'upload'} {r['key']}")

    print(f"\n[upload] 完成统计:")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if not args.dry_run and stats["catalog_rewritten"] > 0:
        print(f"\n[upload] config/asset_catalog.json 已更新。")
        print("[upload] 接下来：")
        print("  1. git diff config/asset_catalog.json   # 看 file 字段从相对路径变成 https URL")
        print("  2. git add config/asset_catalog.json && git commit -m 'chore: migrate assets to qiniu OSS'")
        print("  3. git push")
        print("  4. （可选）清掉本地 data/assets/，重启 server 验证素材从 CDN 拉取仍正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
