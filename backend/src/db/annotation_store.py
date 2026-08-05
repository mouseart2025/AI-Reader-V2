"""Data access layer for reading annotations (划线 + 批注)."""

import logging

from src.db.sqlite_db import get_connection

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id TEXT NOT NULL,
    chapter_num INTEGER NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    anchor_text TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT 'yellow',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_SELECT_COLS = (
    "id, novel_id, chapter_num, start_offset, end_offset, "
    "anchor_text, color, note, created_at"
)


async def list_annotations(
    novel_id: str,
    chapter_num: int | None = None,
) -> list[dict]:
    """List annotations for a novel, optionally filtered by chapter."""
    conn = await get_connection()
    try:
        await conn.execute(_CREATE_TABLE_SQL)
        if chapter_num is None:
            cursor = await conn.execute(
                f"""
                SELECT {_SELECT_COLS}
                FROM annotations
                WHERE novel_id = ?
                ORDER BY chapter_num, start_offset
                """,
                (novel_id,),
            )
        else:
            cursor = await conn.execute(
                f"""
                SELECT {_SELECT_COLS}
                FROM annotations
                WHERE novel_id = ? AND chapter_num = ?
                ORDER BY start_offset
                """,
                (novel_id, chapter_num),
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def add_annotation(
    novel_id: str,
    chapter_num: int,
    start_offset: int,
    end_offset: int,
    anchor_text: str,
    color: str = "yellow",
    note: str = "",
) -> dict:
    """Add an annotation. Returns the created annotation."""
    conn = await get_connection()
    try:
        await conn.execute(_CREATE_TABLE_SQL)
        cursor = await conn.execute(
            """
            INSERT INTO annotations
                (novel_id, chapter_num, start_offset, end_offset,
                 anchor_text, color, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (novel_id, chapter_num, start_offset, end_offset,
             anchor_text, color, note),
        )
        await conn.commit()
        annotation_id = cursor.lastrowid
        cursor2 = await conn.execute(
            f"SELECT {_SELECT_COLS} FROM annotations WHERE id = ?",
            (annotation_id,),
        )
        row = await cursor2.fetchone()
        return dict(row) if row else {}
    finally:
        await conn.close()


async def update_annotation(
    annotation_id: int,
    color: str | None = None,
    note: str | None = None,
) -> dict | None:
    """Update an annotation's color and/or note.

    Returns the updated annotation, or None if it does not exist.
    """
    conn = await get_connection()
    try:
        await conn.execute(_CREATE_TABLE_SQL)
        cursor = await conn.execute(
            f"SELECT {_SELECT_COLS} FROM annotations WHERE id = ?",
            (annotation_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        if color is None and note is None:
            return dict(row)
        if color is not None:
            await conn.execute(
                "UPDATE annotations SET color = ? WHERE id = ?",
                (color, annotation_id),
            )
        if note is not None:
            await conn.execute(
                "UPDATE annotations SET note = ? WHERE id = ?",
                (note, annotation_id),
            )
        await conn.commit()
        cursor2 = await conn.execute(
            f"SELECT {_SELECT_COLS} FROM annotations WHERE id = ?",
            (annotation_id,),
        )
        row2 = await cursor2.fetchone()
        return dict(row2) if row2 else None
    finally:
        await conn.close()


async def delete_annotation(annotation_id: int) -> bool:
    """Delete an annotation by ID."""
    conn = await get_connection()
    try:
        await conn.execute(_CREATE_TABLE_SQL)
        cursor = await conn.execute(
            "DELETE FROM annotations WHERE id = ?",
            (annotation_id,),
        )
        await conn.commit()
        return cursor.rowcount > 0
    finally:
        await conn.close()
