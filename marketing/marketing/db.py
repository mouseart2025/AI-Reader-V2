"""SQLite 数据库 — Pipeline 状态管理"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from marketing.config import get_db_path
from marketing.logger import get_logger

log = get_logger("db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    novel_title TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    output_dir TEXT,
    step_timings JSON,
    error TEXT
);

CREATE TABLE IF NOT EXISTS content_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT REFERENCES pipeline_runs(id),
    novel_title TEXT NOT NULL,
    novel_id INTEGER,
    status TEXT NOT NULL DEFAULT 'selected',
    narrative_angle TEXT,
    platform TEXT,
    step_outputs JSON,
    publish_url TEXT,
    published_at TEXT,
    llm_tokens_used INTEGER DEFAULT 0,
    llm_cost_yuan REAL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id INTEGER NOT NULL REFERENCES content_items(id),
    platform TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    bookmarks INTEGER DEFAULT 0,
    raw_json JSON
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id INTEGER REFERENCES content_items(id),
    alert_type TEXT NOT NULL,
    data_json JSON,
    created_at TEXT NOT NULL,
    acknowledged INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_content_status ON content_items(status);
CREATE INDEX IF NOT EXISTS idx_content_run ON content_items(run_id);
CREATE INDEX IF NOT EXISTS idx_metrics_content ON metrics(content_id, collected_at);
CREATE INDEX IF NOT EXISTS idx_alerts_ack ON alerts(acknowledged);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_db(db_path: str | Path | None = None) -> aiosqlite.Connection:
    """获取数据库连接（自动创建表）"""
    path = Path(db_path) if db_path else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(path))
    db.row_factory = aiosqlite.Row
    await db.executescript(_SCHEMA)
    await db.commit()
    log.info("数据库已连接: %s", path)
    return db


async def create_pipeline_run(
    db: aiosqlite.Connection,
    run_id: str,
    novel_title: str | None = None,
    output_dir: str | None = None,
) -> None:
    await db.execute(
        "INSERT INTO pipeline_runs (id, created_at, novel_title, output_dir) VALUES (?, ?, ?, ?)",
        (run_id, _now(), novel_title, output_dir),
    )
    await db.commit()


async def update_pipeline_run(
    db: aiosqlite.Connection,
    run_id: str,
    **kwargs: Any,
) -> None:
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [run_id]
    await db.execute(f"UPDATE pipeline_runs SET {sets} WHERE id = ?", vals)
    await db.commit()


async def create_content_item(
    db: aiosqlite.Connection,
    novel_title: str,
    run_id: str | None = None,
    novel_id: int | None = None,
    status: str = "selected",
) -> int:
    now = _now()
    cursor = await db.execute(
        """INSERT INTO content_items
           (run_id, novel_title, novel_id, status, step_outputs, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (run_id, novel_title, novel_id, status, "{}", now, now),
    )
    await db.commit()
    return cursor.lastrowid  # type: ignore[return-value]


async def update_content_item(
    db: aiosqlite.Connection,
    content_id: int,
    **kwargs: Any,
) -> None:
    kwargs["updated_at"] = _now()
    # step_outputs 特殊处理: 合并而非覆盖
    if "step_outputs" in kwargs and isinstance(kwargs["step_outputs"], dict):
        row = await db.execute_fetchall(
            "SELECT step_outputs FROM content_items WHERE id = ?", (content_id,)
        )
        if row:
            existing = json.loads(row[0][0] or "{}")
            existing.update(kwargs["step_outputs"])
            kwargs["step_outputs"] = json.dumps(existing, ensure_ascii=False)
        else:
            kwargs["step_outputs"] = json.dumps(kwargs["step_outputs"], ensure_ascii=False)
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [content_id]
    await db.execute(f"UPDATE content_items SET {sets} WHERE id = ?", vals)
    await db.commit()


async def get_content_items_by_status(
    db: aiosqlite.Connection,
    status: str,
) -> list[dict[str, Any]]:
    rows = await db.execute_fetchall(
        "SELECT * FROM content_items WHERE status = ? ORDER BY created_at DESC",
        (status,),
    )
    return [dict(r) for r in rows]


async def get_content_item(
    db: aiosqlite.Connection,
    content_id: int,
) -> dict[str, Any] | None:
    rows = await db.execute_fetchall(
        "SELECT * FROM content_items WHERE id = ?", (content_id,)
    )
    return dict(rows[0]) if rows else None


async def is_novel_selected(
    db: aiosqlite.Connection,
    novel_title: str,
) -> bool:
    rows = await db.execute_fetchall(
        "SELECT 1 FROM content_items WHERE novel_title = ? LIMIT 1",
        (novel_title,),
    )
    return len(rows) > 0
