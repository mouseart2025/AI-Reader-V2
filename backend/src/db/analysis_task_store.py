"""CRUD operations for analysis_tasks table."""

import logging

from src.db.sqlite_db import get_connection

logger = logging.getLogger(__name__)


async def create_task(
    task_id: str,
    novel_id: str,
    chapter_start: int,
    chapter_end: int,
) -> None:
    """Create a new analysis task with status=running."""
    conn = await get_connection()
    try:
        await conn.execute(
            """
            INSERT INTO analysis_tasks (id, novel_id, status, chapter_start, chapter_end, current_chapter)
            VALUES (?, ?, 'running', ?, ?, ?)
            """,
            (task_id, novel_id, chapter_start, chapter_end, chapter_start),
        )
        await conn.commit()
    finally:
        await conn.close()


async def get_task(task_id: str) -> dict | None:
    """Retrieve a task by ID."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT id, novel_id, status, chapter_start, chapter_end,
                   current_chapter, created_at, updated_at
            FROM analysis_tasks WHERE id = ?
            """,
            (task_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def get_running_task(novel_id: str) -> dict | None:
    """Get the currently running or paused task for a novel."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT id, novel_id, status, chapter_start, chapter_end,
                   current_chapter, created_at, updated_at
            FROM analysis_tasks
            WHERE novel_id = ? AND status IN ('running', 'paused')
            ORDER BY created_at DESC LIMIT 1
            """,
            (novel_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def update_task_status(task_id: str, status: str) -> None:
    """Update task status (running/paused/cancelled/completed)."""
    conn = await get_connection()
    try:
        await conn.execute(
            "UPDATE analysis_tasks SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, task_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def update_task_progress(task_id: str, current_chapter: int) -> None:
    """Update the current chapter being processed."""
    conn = await get_connection()
    try:
        await conn.execute(
            "UPDATE analysis_tasks SET current_chapter = ?, updated_at = datetime('now') WHERE id = ?",
            (current_chapter, task_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def update_chapter_analysis_status(
    novel_id: str, chapter_num: int, status: str
) -> None:
    """Update chapters.analysis_status for a specific chapter."""
    conn = await get_connection()
    try:
        await conn.execute(
            """
            UPDATE chapters SET analysis_status = ?, analyzed_at = datetime('now')
            WHERE novel_id = ? AND chapter_num = ?
            """,
            (status, novel_id, chapter_num),
        )
        await conn.commit()
    finally:
        await conn.close()


async def get_chapter_content(novel_id: str, chapter_num: int) -> dict | None:
    """Get chapter content and metadata."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT id, chapter_num, title, content, word_count, analysis_status
            FROM chapters
            WHERE novel_id = ? AND chapter_num = ?
            """,
            (novel_id, chapter_num),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def get_latest_task(novel_id: str) -> dict | None:
    """Get the most recent task for a novel (any status)."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT id, novel_id, status, chapter_start, chapter_end,
                   current_chapter, created_at, updated_at
            FROM analysis_tasks
            WHERE novel_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (novel_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def recover_stale_tasks() -> int:
    """Mark any 'running' tasks as 'paused' on server startup.

    If the server was restarted (crash, manual restart, etc.), any tasks left
    in 'running' state have no active asyncio loop driving them. Mark them
    as 'paused' so the user can resume from where they left off.

    Returns the number of tasks recovered.
    """
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            UPDATE analysis_tasks
            SET status = 'paused', updated_at = datetime('now')
            WHERE status = 'running'
            """
        )
        await conn.commit()
        count = cursor.rowcount
        if count > 0:
            logger.info("Recovered %d stale running task(s) → paused", count)
        return count
    finally:
        await conn.close()
