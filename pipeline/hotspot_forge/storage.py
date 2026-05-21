"""HotspotSignal CRUD + 抓取日志。"""
from __future__ import annotations

import json
import time
from typing import Optional

from .schema import HotspotSignal, get_conn, init_db


def upsert_signals(signals: list[HotspotSignal]) -> int:
    """批量写入；signal_id 重复时跳过。返回**新增**条数。"""
    if not signals:
        return 0
    init_db()
    inserted = 0
    with get_conn() as conn:
        for sig in signals:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO hotspot_signals
                    (signal_id, source, source_layer, title, url, summary,
                     raw_score, category_hint, lang, fetched_at, published_at,
                     raw_data, track_match, dedup_master_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sig.signal_id,
                    sig.source,
                    sig.source_layer,
                    sig.title,
                    sig.url,
                    sig.summary,
                    sig.raw_score,
                    sig.category_hint,
                    sig.lang,
                    sig.fetched_at,
                    sig.published_at,
                    json.dumps(sig.raw_data, ensure_ascii=False) if sig.raw_data else None,
                    json.dumps(sig.track_match, ensure_ascii=False) if sig.track_match else None,
                    sig.dedup_master_id,
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
    return inserted


def log_fetch_start(source: str) -> int:
    init_db()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO hotspot_fetch_log (source, started_at, status) VALUES (?, ?, 'running')",
            (source, time.time()),
        )
        return cur.lastrowid


def log_fetch_finish(
    fetch_id: int,
    status: str,
    fetched: int = 0,
    inserted: int = 0,
    error: Optional[str] = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE hotspot_fetch_log
               SET finished_at = ?, status = ?, fetched = ?, inserted = ?, error = ?
               WHERE fetch_id = ?""",
            (time.time(), status, fetched, inserted, error, fetch_id),
        )


def list_recent_signals(
    layer: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """按抓取时间倒序取最近信号。layer/source 可选过滤。"""
    init_db()
    clauses: list[str] = []
    params: list = []
    if layer:
        clauses.append("source_layer = ?")
        params.append(layer)
    if source:
        clauses.append("source = ?")
        params.append(source)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM hotspot_signals {where} ORDER BY fetched_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def fetch_log_summary(limit: int = 10) -> list[dict]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM hotspot_fetch_log ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- 立意 → 文案 异步任务（forge_script_jobs） ----------

def create_script_job(video_id: str, mode: str, brief: dict,
                      supplements: dict, signal: dict) -> None:
    """新建 pending 状态的 job。同 video_id 重入 = 覆盖。"""
    init_db()
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO forge_script_jobs
                (video_id, mode, status, brief_json, supplements_json, signal_json, created_at)
               VALUES (?, ?, 'pending', ?, ?, ?, ?)""",
            (video_id, mode,
             json.dumps(brief, ensure_ascii=False) if brief else None,
             json.dumps(supplements, ensure_ascii=False) if supplements else None,
             json.dumps(signal, ensure_ascii=False) if signal else None,
             now),
        )


def get_script_job(video_id: str) -> Optional[dict]:
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM forge_script_jobs WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        return dict(row) if row else None


def update_script_job(video_id: str, **fields) -> None:
    """允许更新：status / result_json / error / provider / cost / completed_at"""
    allowed = {"status", "result_json", "error", "provider", "cost", "completed_at"}
    keys = [k for k in fields if k in allowed]
    if not keys:
        return
    sets = ", ".join(f"{k} = ?" for k in keys)
    values = [fields[k] for k in keys]
    values.append(video_id)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE forge_script_jobs SET {sets} WHERE video_id = ?",
            tuple(values),
        )
