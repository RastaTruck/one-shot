"""Outhan API 用量记账（独立于一二布布的 tasks 表）。

[AI-NOTE] 2026-05-08: 同事的 tasks 表是 agent 框架专用，Outhan 流程直接调 client，
不走 start_task/finish_task。新加 outhan_usage_log 表单调追加，用于：
- 每天工作结束时汇报「自上次汇报以来 + 累计」消耗
- 长期追踪用量趋势

记账约定：
- 单次成功 API 调用 = 一条记录
- 失败重试本身不记（因为可能 Fish/Volces 不扣费），仅记最终成功的那次
- cost_cny 是估算值（按公开价目表），最终账单以各家控制台为准

公开价目表（2026-05，估算用）：
- Doubao Seedream 4 (image gen): ¥0.10 / image
- Doubao Seedance (video gen): ¥0.50 / 5s + 0.10/s 起步价（按时长粗估）
- Vidu Q3 国内: ¥0.30-1.00 / 5s（按 resolution）
- Fish Audio S1: ¥0.0008 / char（中文，含标签）
- Gemini 2.5 Pro: input $1.25/M tok, output $5/M tok（×7.2 ≈ ¥9/M, ¥36/M）
- Gemini 2.5 Flash: input $0.15/M, output $0.60/M（×7.2 ≈ ¥1.08/M, ¥4.32/M）
"""
from __future__ import annotations

import datetime
import json
import time
from pathlib import Path
from typing import Any, Optional

from pipeline import state


# 价目表（人民币 / 单位）
COST_TABLE = {
    "seedream_image": 0.10,           # 每张图
    "seedance_video_per_sec": 0.10,   # 每秒视频（5s ≈ 0.5）
    "vidu_video_per_sec": 0.10,       # 每秒视频（粗估）
    "fish_s1_per_char": 0.0008,       # 每个中文字符
    "gemini_pro_in_per_1k": 0.009,    # 每千输入 token
    "gemini_pro_out_per_1k": 0.036,   # 每千输出 token
    "gemini_flash_in_per_1k": 0.00108,
    "gemini_flash_out_per_1k": 0.00432,
}


_SCHEMA_READY = False


def _ensure_table() -> None:
    """模块加载时建表（幂等）。"""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with state.get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS outhan_usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                provider TEXT NOT NULL,
                model TEXT,
                video_id TEXT,
                agent TEXT,
                cost_cny REAL DEFAULT 0,
                tokens_in INTEGER DEFAULT 0,
                tokens_out INTEGER DEFAULT 0,
                meta_json TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_outhan_usage_ts ON outhan_usage_log(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_outhan_usage_provider ON outhan_usage_log(provider)")
    _SCHEMA_READY = True


_ensure_table()


def log(
    provider: str,
    *,
    model: str = "",
    video_id: str = "",
    agent: str = "",
    cost_cny: float = 0.0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    meta: Optional[dict[str, Any]] = None,
) -> None:
    """记一笔 API 调用。失败不抛（不能因为记账阻塞主流程）。"""
    try:
        _ensure_table()
        with state.get_conn() as conn:
            conn.execute("""
                INSERT INTO outhan_usage_log
                (ts, provider, model, video_id, agent, cost_cny, tokens_in, tokens_out, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                time.time(),
                provider, model or "", video_id or "", agent or "",
                float(cost_cny or 0),
                int(tokens_in or 0),
                int(tokens_out or 0),
                json.dumps(meta, ensure_ascii=False) if meta else None,
            ))
    except Exception as e:
        print(f"[outhan_usage_log] failed: {e}", file=__import__('sys').stderr)


# ===== 价目计算辅助 =====

def cost_seedream(images: int = 1) -> float:
    return COST_TABLE["seedream_image"] * images


def cost_seedance(duration_sec: float) -> float:
    """Seedance 按秒粗估（实际按 5s 阶梯计费但这里线性）。"""
    return COST_TABLE["seedance_video_per_sec"] * float(duration_sec or 5)


def cost_vidu(duration_sec: float) -> float:
    return COST_TABLE["vidu_video_per_sec"] * float(duration_sec or 5)


def cost_fish_s1(text: str) -> float:
    """中文字符按 char 计；标签 (excited) 也算字符（保守估）。"""
    chars = len((text or "").replace(" ", "").replace("\n", ""))
    return COST_TABLE["fish_s1_per_char"] * chars


def cost_gemini(in_tok: int, out_tok: int, *, model: str = "") -> float:
    """Gemini 按 token 计，pro 比 flash 贵。"""
    is_pro = "pro" in (model or "").lower() or "2.5-pro" in (model or "")
    if is_pro:
        return (
            in_tok * COST_TABLE["gemini_pro_in_per_1k"] / 1000
            + out_tok * COST_TABLE["gemini_pro_out_per_1k"] / 1000
        )
    return (
        in_tok * COST_TABLE["gemini_flash_in_per_1k"] / 1000
        + out_tok * COST_TABLE["gemini_flash_out_per_1k"] / 1000
    )


# ===== 查询 =====

def query_summary(since: float, until: Optional[float] = None) -> dict[str, Any]:
    """按 provider 分组返回时段汇总。"""
    _ensure_table()
    until = until if until is not None else time.time()
    with state.get_conn() as conn:
        rows = conn.execute("""
            SELECT provider,
                   COUNT(*) as cnt,
                   ROUND(SUM(cost_cny), 4) as total_cny,
                   COALESCE(SUM(tokens_in), 0) as tokens_in,
                   COALESCE(SUM(tokens_out), 0) as tokens_out
            FROM outhan_usage_log
            WHERE ts >= ? AND ts < ?
            GROUP BY provider
            ORDER BY total_cny DESC
        """, (since, until)).fetchall()
        rows_d = [dict(r) for r in rows]

        # 按 agent 子分组（更细粒度）
        agent_rows = conn.execute("""
            SELECT provider, agent, COUNT(*) as cnt,
                   ROUND(SUM(cost_cny), 4) as total_cny
            FROM outhan_usage_log
            WHERE ts >= ? AND ts < ?
            GROUP BY provider, agent
            ORDER BY total_cny DESC
        """, (since, until)).fetchall()
        agent_d = [dict(r) for r in agent_rows]

    return {
        "since": since,
        "until": until,
        "since_iso": datetime.datetime.fromtimestamp(since).isoformat(timespec="seconds"),
        "until_iso": datetime.datetime.fromtimestamp(until).isoformat(timespec="seconds"),
        "by_provider": rows_d,
        "by_agent": agent_d,
        "total_cny": round(sum(float(r["total_cny"] or 0) for r in rows_d), 4),
        "total_calls": sum(int(r["cnt"]) for r in rows_d),
    }


def query_total() -> dict[str, Any]:
    """累计总用量。"""
    _ensure_table()
    with state.get_conn() as conn:
        rows = conn.execute("""
            SELECT provider, COUNT(*) as cnt, ROUND(SUM(cost_cny), 4) as total_cny
            FROM outhan_usage_log
            GROUP BY provider
            ORDER BY total_cny DESC
        """).fetchall()
        rows_d = [dict(r) for r in rows]
        first_ts = conn.execute("SELECT MIN(ts) FROM outhan_usage_log").fetchone()[0]
    return {
        "first_ts": first_ts,
        "first_iso": datetime.datetime.fromtimestamp(first_ts).isoformat(timespec="seconds") if first_ts else None,
        "by_provider": rows_d,
        "total_cny": round(sum(float(r["total_cny"] or 0) for r in rows_d), 4),
        "total_calls": sum(int(r["cnt"]) for r in rows_d),
    }


# ===== 上次汇报时间持久化 =====

_REPORT_STATE = Path(__file__).resolve().parents[1] / "data" / "outhan_last_report.txt"


def get_last_report_ts() -> float:
    """返回上次汇报的时间戳；从未汇报过 → 返回今日 0 点。"""
    if _REPORT_STATE.exists():
        try:
            return float(_REPORT_STATE.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    # 没记录 → 今天零点
    return datetime.datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()


def update_last_report_ts(ts: Optional[float] = None) -> None:
    """汇报后更新时间戳（默认 now）。"""
    _REPORT_STATE.parent.mkdir(parents=True, exist_ok=True)
    _REPORT_STATE.write_text(str(ts if ts is not None else time.time()), encoding="utf-8")
