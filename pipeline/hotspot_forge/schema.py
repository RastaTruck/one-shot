"""HotspotSignal 数据契约 + SQLite 建表。

复用 pipeline.db（和 pipeline.state 同库不同表），不引入新 DB 文件，
避免备份/迁移时漏一份。
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional


DB_PATH = Path(__file__).resolve().parents[2] / "data" / "pipeline.db"


# source_layer 三层语义：
#   timely    — 时效信号（热搜聚合），保鲜期短，决定"做什么"
#   ammo      — 专业弹药（财经 RSS），保鲜期长，决定"怎么深入"
#   longtail  — 长尾雷达（HN/GTrends），英文圈早期信号，差异化护城河
SOURCE_LAYERS = ("timely", "ammo", "longtail")


SCHEMA = """
CREATE TABLE IF NOT EXISTS hotspot_signals (
    signal_id        TEXT PRIMARY KEY,
    source           TEXT NOT NULL,        -- 'weibo' / 'rss:caixin' / 'hn' ...
    source_layer     TEXT NOT NULL,        -- 'timely' / 'ammo' / 'longtail'
    title            TEXT NOT NULL,
    url              TEXT,
    summary          TEXT,
    raw_score        REAL,                 -- 各源原始热度（不归一）
    category_hint    TEXT,                 -- 源给的分类提示
    lang             TEXT NOT NULL DEFAULT 'zh',
    fetched_at       REAL NOT NULL,        -- 我们抓到的时刻
    published_at     REAL,                 -- 源声明的发布时刻（可空）
    raw_data         TEXT,                 -- JSON，原始字段，便于扩展
    track_match      TEXT,                 -- JSON，赛道匹配分（Step 2 才填）
    dedup_master_id  TEXT                  -- 同事件聚合的主信号 ID（Step 2 才填）
);
CREATE INDEX IF NOT EXISTS idx_hotspot_fetched ON hotspot_signals(fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_hotspot_source  ON hotspot_signals(source);
CREATE INDEX IF NOT EXISTS idx_hotspot_layer   ON hotspot_signals(source_layer);

CREATE TABLE IF NOT EXISTS hotspot_fetch_log (
    fetch_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,
    started_at   REAL NOT NULL,
    finished_at  REAL,
    status       TEXT NOT NULL,            -- 'running' / 'success' / 'failed'
    inserted     INTEGER DEFAULT 0,        -- 本次新增条数
    fetched      INTEGER DEFAULT 0,        -- 本次抓取条数（含重复）
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_hotspot_fetch_time ON hotspot_fetch_log(started_at DESC);

CREATE TABLE IF NOT EXISTS forge_script_jobs (
    video_id          TEXT PRIMARY KEY,
    mode              TEXT NOT NULL,        -- 'news' / 'yierbubu' / 'outhan'
    status            TEXT NOT NULL,        -- 'pending' / 'running' / 'completed' / 'failed'
    brief_json        TEXT,
    supplements_json  TEXT,
    signal_json       TEXT,
    result_json       TEXT,                 -- {title, script, units, references_used}
    error             TEXT,
    provider          TEXT,
    cost              REAL DEFAULT 0,
    created_at        REAL NOT NULL,
    completed_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_forge_jobs_status ON forge_script_jobs(status, created_at DESC);
"""


@dataclass
class HotspotSignal:
    """一条原始热点信号。signal_id 由 source + (url or title) 派生，天然去重。"""
    source: str
    source_layer: str
    title: str
    lang: str = "zh"
    url: Optional[str] = None
    summary: Optional[str] = None
    raw_score: Optional[float] = None
    category_hint: Optional[str] = None
    fetched_at: float = field(default_factory=time.time)
    published_at: Optional[float] = None
    raw_data: dict = field(default_factory=dict)
    track_match: dict = field(default_factory=dict)
    dedup_master_id: Optional[str] = None

    @property
    def signal_id(self) -> str:
        # 同一条信号反复抓取 → 同一 ID → INSERT OR IGNORE 自动去重
        key = f"{self.source}|{(self.url or self.title).strip()}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """打开一个连接，自动 commit/close。与 pipeline.state 风格一致。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """建表。可重复调用——独立于 pipeline.state.init_db，让模块自包含。"""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
