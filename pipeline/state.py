"""状态机 + SQLite 持久化。

两张表:
- videos: 每条视频的生命周期状态
- tasks:  每次 Agent 调用/API 调用的执行日志（成本、耗时、错误）

所有 Agent 在进入和退出时通过这里写状态。Orchestrator 读状态决定派单。
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterator


DB_PATH = Path(__file__).resolve().parents[1] / "data" / "pipeline.db"


class VideoStatus(str, Enum):
    """一条视频的生命周期状态（对应 plan 中的状态机）。"""
    DRAFT = "draft"
    TOPIC_LOCKED = "topic_locked"
    SCRIPT_DRAFTING = "script_drafting"
    SCRIPT_PENDING_REVIEW = "script_pending_review"
    SCRIPT_LOCKED = "script_locked"
    SHOT_TABLE_GENERATING = "shot_table_generating"
    SHOT_TABLE_LOCKED = "shot_table_locked"
    ASSETS_PRODUCING = "assets_producing"
    ASSETS_READY = "assets_ready"
    SUBTITLE_DONE = "subtitle_done"
    EDITING = "editing"
    CUT_PENDING_REVIEW = "cut_pending_review"
    CUT_APPROVED = "cut_approved"
    PACKAGING = "packaging"
    READY_TO_PUBLISH = "ready_to_publish"
    FAILED = "failed"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id        TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    topic           TEXT,
    status          TEXT NOT NULL,
    shot_table_path TEXT,
    final_video_path TEXT,
    meta_json       TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id      TEXT NOT NULL,
    shot_id       TEXT,
    agent         TEXT NOT NULL,
    provider      TEXT,
    status        TEXT NOT NULL,
    cost_cny      REAL DEFAULT 0,
    retry_count   INTEGER DEFAULT 0,
    input_json    TEXT,
    output_json   TEXT,
    error         TEXT,
    started_at    REAL,
    finished_at   REAL,
    FOREIGN KEY (video_id) REFERENCES videos(video_id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_video ON tasks(video_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);

CREATE TABLE IF NOT EXISTS script_drafts (
    video_id    TEXT PRIMARY KEY,
    drafts_json TEXT NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS script_working (
    video_id      TEXT PRIMARY KEY,
    working_json  TEXT NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS script_archives (
    archive_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     TEXT NOT NULL,
    name         TEXT NOT NULL,
    drafts_json  TEXT NOT NULL,
    working_json TEXT NOT NULL,
    created_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_archives_video ON script_archives(video_id);

CREATE TABLE IF NOT EXISTS project_sources (
    video_id     TEXT PRIMARY KEY,
    sources_json TEXT NOT NULL,
    updated_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS project_topic (
    video_id    TEXT PRIMARY KEY,
    topic_json  TEXT NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS project_topic_pool (
    video_id    TEXT PRIMARY KEY,
    pool_json   TEXT NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS project_style (
    video_id    TEXT PRIMARY KEY,
    style_json  TEXT NOT NULL,
    updated_at  REAL NOT NULL
);

-- 完整文稿（按 video_id + duration_sec 唯一）
CREATE TABLE IF NOT EXISTS script_full (
    full_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     TEXT NOT NULL,
    outline_id   TEXT NOT NULL,
    duration_sec INTEGER NOT NULL,
    full_json    TEXT NOT NULL,
    updated_at   REAL NOT NULL,
    UNIQUE(video_id, duration_sec)
);
CREATE INDEX IF NOT EXISTS idx_full_video ON script_full(video_id);

-- 片段灵感库（hook / golden / cta / case 任意收藏）
CREATE TABLE IF NOT EXISTS snippet_library (
    snippet_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     TEXT NOT NULL,
    kind         TEXT NOT NULL,
    text         TEXT NOT NULL,
    source_note  TEXT,
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snippet_video ON snippet_library(video_id);
CREATE INDEX IF NOT EXISTS idx_snippet_kind ON snippet_library(kind);

-- 画风锁定：style_bible 快照 + 生成的参考图列表
CREATE TABLE IF NOT EXISTS project_style_bible (
    video_id       TEXT PRIMARY KEY,
    bible_json     TEXT NOT NULL,
    images_json    TEXT NOT NULL,
    total_cost_cny REAL NOT NULL DEFAULT 0,
    locked         INTEGER NOT NULL DEFAULT 0,
    updated_at     REAL NOT NULL
);

-- 分镜表（shot_table 完整序列化）
CREATE TABLE IF NOT EXISTS project_shot_table (
    video_id   TEXT PRIMARY KEY,
    table_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);

-- 语义断句表（LLM 切分 + 用户审核后的最终单元列表）
CREATE TABLE IF NOT EXISTS project_segments (
    video_id      TEXT PRIMARY KEY,
    segments_json TEXT NOT NULL,
    updated_at    REAL NOT NULL
);
"""


# ---------- 完整文稿 ----------

def save_script_full(video_id: str, outline_id: str, duration_sec: int, full: dict[str, Any]) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO script_full (video_id, outline_id, duration_sec, full_json, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(video_id, duration_sec) DO UPDATE SET
                 outline_id = excluded.outline_id,
                 full_json = excluded.full_json,
                 updated_at = excluded.updated_at""",
            (video_id, outline_id, duration_sec, json.dumps(full, ensure_ascii=False), time.time()),
        )
        # 拿 full_id（无论是 INSERT 还是 UPDATE）
        row = conn.execute(
            "SELECT full_id FROM script_full WHERE video_id = ? AND duration_sec = ?",
            (video_id, duration_sec),
        ).fetchone()
        return row["full_id"]


def load_script_full(video_id: str, duration_sec: int | None = None) -> dict[str, Any] | None:
    with get_conn() as conn:
        if duration_sec is not None:
            row = conn.execute(
                "SELECT * FROM script_full WHERE video_id = ? AND duration_sec = ? ORDER BY updated_at DESC LIMIT 1",
                (video_id, duration_sec),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM script_full WHERE video_id = ? ORDER BY updated_at DESC LIMIT 1",
                (video_id,),
            ).fetchone()
        if not row:
            return None
        d = json.loads(row["full_json"])
        d["full_id"] = row["full_id"]
        d["outline_id"] = row["outline_id"]
        d["duration_sec"] = row["duration_sec"]
        d["updated_at"] = row["updated_at"]
        return d


def list_script_fulls(video_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT full_id, outline_id, duration_sec, updated_at FROM script_full WHERE video_id = ? ORDER BY duration_sec",
            (video_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- 片段灵感库 ----------

def save_snippet(video_id: str, kind: str, text: str, source_note: str | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO snippet_library (video_id, kind, text, source_note, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (video_id, kind, text, source_note, time.time()),
        )
        return cur.lastrowid


def list_snippets(video_id: str, kind: str | None = None) -> list[dict[str, Any]]:
    with get_conn() as conn:
        if kind:
            rows = conn.execute(
                "SELECT * FROM snippet_library WHERE video_id = ? AND kind = ? ORDER BY created_at DESC",
                (video_id, kind),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM snippet_library WHERE video_id = ? ORDER BY created_at DESC",
                (video_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def delete_snippet(snippet_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM snippet_library WHERE snippet_id = ?", (snippet_id,))


def save_project_style(video_id: str, style: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO project_style (video_id, style_json, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(video_id) DO UPDATE SET style_json = excluded.style_json, updated_at = excluded.updated_at""",
            (video_id, json.dumps(style, ensure_ascii=False), time.time()),
        )


def load_project_style(video_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT style_json FROM project_style WHERE video_id = ?", (video_id,)).fetchone()
        return json.loads(row["style_json"]) if row else None


# ---------- 选题页：用户输入的灵感源 + 选定的题材 + 候选池 ----------

def save_project_sources(video_id: str, sources: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO project_sources (video_id, sources_json, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(video_id) DO UPDATE SET sources_json = excluded.sources_json, updated_at = excluded.updated_at""",
            (video_id, json.dumps(sources, ensure_ascii=False), time.time()),
        )


def load_project_sources(video_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT sources_json FROM project_sources WHERE video_id = ?", (video_id,)).fetchone()
        return json.loads(row["sources_json"]) if row else None


def save_project_topic(video_id: str, topic: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO project_topic (video_id, topic_json, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(video_id) DO UPDATE SET topic_json = excluded.topic_json, updated_at = excluded.updated_at""",
            (video_id, json.dumps(topic, ensure_ascii=False), time.time()),
        )


def load_project_topic(video_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT topic_json FROM project_topic WHERE video_id = ?", (video_id,)).fetchone()
        return json.loads(row["topic_json"]) if row else None


def save_project_topic_pool(video_id: str, pool: list[dict[str, Any]]) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO project_topic_pool (video_id, pool_json, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(video_id) DO UPDATE SET pool_json = excluded.pool_json, updated_at = excluded.updated_at""",
            (video_id, json.dumps(pool, ensure_ascii=False), time.time()),
        )


def load_project_topic_pool(video_id: str) -> list[dict[str, Any]] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT pool_json FROM project_topic_pool WHERE video_id = ?", (video_id,)).fetchone()
        return json.loads(row["pool_json"]) if row else None


# ---------- 画风锁定（style_bible + 参考图） ----------

def save_project_style_bible(
    video_id: str, bible: dict[str, Any], images: list[dict[str, Any]],
    total_cost_cny: float = 0.0, locked: bool = False,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO project_style_bible
                 (video_id, bible_json, images_json, total_cost_cny, locked, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(video_id) DO UPDATE SET
                 bible_json = excluded.bible_json,
                 images_json = excluded.images_json,
                 total_cost_cny = excluded.total_cost_cny,
                 locked = excluded.locked,
                 updated_at = excluded.updated_at""",
            (
                video_id,
                json.dumps(bible, ensure_ascii=False),
                json.dumps(images, ensure_ascii=False),
                float(total_cost_cny),
                1 if locked else 0,
                time.time(),
            ),
        )


def load_project_style_bible(video_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT bible_json, images_json, total_cost_cny, locked, updated_at "
            "FROM project_style_bible WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "bible": json.loads(row["bible_json"]),
            "images": json.loads(row["images_json"]),
            "total_cost_cny": row["total_cost_cny"],
            "locked": bool(row["locked"]),
            "updated_at": row["updated_at"],
        }


def set_project_style_bible_locked(video_id: str, locked: bool) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE project_style_bible SET locked = ?, updated_at = ? WHERE video_id = ?",
            (1 if locked else 0, time.time(), video_id),
        )
        return cur.rowcount > 0


# ---------- 分镜表（整表 JSON） ----------

def save_project_segments(video_id: str, segments: list[dict[str, Any]]) -> None:
    """保存 LLM 断句 / 用户审核后的 segments 列表。"""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO project_segments (video_id, segments_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(video_id) DO UPDATE SET
                 segments_json = excluded.segments_json,
                 updated_at = excluded.updated_at""",
            (video_id, json.dumps(segments, ensure_ascii=False), time.time()),
        )


def load_project_segments(video_id: str) -> list[dict[str, Any]] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT segments_json FROM project_segments WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        return json.loads(row["segments_json"]) if row else None


def save_project_shot_table(video_id: str, table_dict: dict[str, Any]) -> None:
    """保存 ShotTable.to_dict() 输出。"""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO project_shot_table (video_id, table_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(video_id) DO UPDATE SET
                 table_json = excluded.table_json,
                 updated_at = excluded.updated_at""",
            (video_id, json.dumps(table_dict, ensure_ascii=False), time.time()),
        )


def load_project_shot_table(video_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT table_json FROM project_shot_table WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        return json.loads(row["table_json"]) if row else None


# ---------- script 表操作 ----------

def save_script_drafts(video_id: str, drafts: list[dict[str, Any]]) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO script_drafts (video_id, drafts_json, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(video_id) DO UPDATE SET drafts_json = excluded.drafts_json, updated_at = excluded.updated_at""",
            (video_id, json.dumps(drafts, ensure_ascii=False), time.time()),
        )


def load_script_drafts(video_id: str) -> list[dict[str, Any]] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT drafts_json FROM script_drafts WHERE video_id = ?", (video_id,)).fetchone()
        return json.loads(row["drafts_json"]) if row else None


def save_script_working(video_id: str, working: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO script_working (video_id, working_json, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(video_id) DO UPDATE SET working_json = excluded.working_json, updated_at = excluded.updated_at""",
            (video_id, json.dumps(working, ensure_ascii=False), time.time()),
        )


def load_script_working(video_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT working_json FROM script_working WHERE video_id = ?", (video_id,)).fetchone()
        return json.loads(row["working_json"]) if row else None


def archive_script(video_id: str, name: str, drafts: list[dict[str, Any]], working: dict[str, Any]) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO script_archives (video_id, name, drafts_json, working_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (video_id, name, json.dumps(drafts, ensure_ascii=False),
             json.dumps(working, ensure_ascii=False), time.time()),
        )
        return cur.lastrowid


def list_script_archives(video_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT archive_id, name, created_at FROM script_archives WHERE video_id = ? ORDER BY created_at DESC",
            (video_id,),
        ).fetchall()
        return [{"archive_id": r["archive_id"], "name": r["name"], "created_at": r["created_at"]} for r in rows]


def load_script_archive(archive_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM script_archives WHERE archive_id = ?", (archive_id,)).fetchone()
        if not row:
            return None
        return {
            "archive_id": row["archive_id"],
            "video_id": row["video_id"],
            "name": row["name"],
            "drafts": json.loads(row["drafts_json"]),
            "working": json.loads(row["working_json"]),
            "created_at": row["created_at"],
        }


@dataclass
class VideoRecord:
    video_id: str
    title: str
    topic: str | None
    status: str
    shot_table_path: str | None = None
    final_video_path: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass
class TaskRecord:
    task_id: int | None
    video_id: str
    shot_id: str | None
    agent: str
    provider: str | None
    status: str
    cost_cny: float = 0.0
    retry_count: int = 0
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


# ---------- 底层连接 ----------

def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """建表。可重复调用。"""
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ---------- videos 表 ----------

def create_video(video_id: str, title: str, topic: str | None = None) -> VideoRecord:
    now = time.time()
    record = VideoRecord(
        video_id=video_id,
        title=title,
        topic=topic,
        status=VideoStatus.DRAFT.value,
        created_at=now,
        updated_at=now,
    )
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO videos (video_id, title, topic, status, meta_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (video_id, title, topic, record.status, "{}", now, now),
        )
    return record


def get_video(video_id: str) -> VideoRecord | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        if not row:
            return None
        return _row_to_video(row)


def update_video_status(video_id: str, status: VideoStatus | str) -> None:
    status_value = status.value if isinstance(status, VideoStatus) else status
    with get_conn() as conn:
        conn.execute(
            "UPDATE videos SET status = ?, updated_at = ? WHERE video_id = ?",
            (status_value, time.time(), video_id),
        )


def update_video_fields(video_id: str, **fields: Any) -> None:
    """通用字段更新，用于回写 shot_table_path / final_video_path 等。"""
    if not fields:
        return
    allowed = {"title", "topic", "status", "shot_table_path", "final_video_path"}
    cols, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            raise ValueError(f"unknown field: {k}")
        cols.append(f"{k} = ?")
        vals.append(v)
    cols.append("updated_at = ?")
    vals.append(time.time())
    vals.append(video_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE videos SET {', '.join(cols)} WHERE video_id = ?", vals)


def list_videos(status: VideoStatus | str | None = None) -> list[VideoRecord]:
    with get_conn() as conn:
        if status:
            status_value = status.value if isinstance(status, VideoStatus) else status
            rows = conn.execute(
                "SELECT * FROM videos WHERE status = ? ORDER BY updated_at DESC",
                (status_value,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM videos ORDER BY updated_at DESC").fetchall()
        return [_row_to_video(r) for r in rows]


def _row_to_video(row: sqlite3.Row) -> VideoRecord:
    return VideoRecord(
        video_id=row["video_id"],
        title=row["title"],
        topic=row["topic"],
        status=row["status"],
        shot_table_path=row["shot_table_path"],
        final_video_path=row["final_video_path"],
        meta=json.loads(row["meta_json"] or "{}"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------- tasks 表 ----------

def start_task(
    video_id: str,
    agent: str,
    provider: str | None = None,
    shot_id: str | None = None,
    input_data: dict[str, Any] | None = None,
) -> int:
    """创建一条任务记录，返回 task_id。"""
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO tasks
               (video_id, shot_id, agent, provider, status, input_json, started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                video_id,
                shot_id,
                agent,
                provider,
                TaskStatus.RUNNING.value,
                json.dumps(input_data or {}, ensure_ascii=False),
                time.time(),
            ),
        )
        return cur.lastrowid


def finish_task(
    task_id: int,
    success: bool,
    output: dict[str, Any] | None = None,
    error: str | None = None,
    cost_cny: float = 0.0,
) -> None:
    status = TaskStatus.SUCCESS.value if success else TaskStatus.FAILED.value
    with get_conn() as conn:
        conn.execute(
            """UPDATE tasks
               SET status = ?, output_json = ?, error = ?, cost_cny = ?, finished_at = ?
               WHERE task_id = ?""",
            (
                status,
                json.dumps(output or {}, ensure_ascii=False),
                error,
                cost_cny,
                time.time(),
                task_id,
            ),
        )


def total_cost(video_id: str) -> float:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_cny), 0) AS total FROM tasks WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        return float(row["total"])


if __name__ == "__main__":
    # 直接跑这个文件 = 建库 + 打印当前视频列表
    init_db()
    videos = list_videos()
    print(f"[state] DB ready at {DB_PATH}")
    print(f"[state] {len(videos)} video(s) on file:")
    for v in videos:
        print(f"  - {v.video_id}  {v.status:<25}  {v.title}")
