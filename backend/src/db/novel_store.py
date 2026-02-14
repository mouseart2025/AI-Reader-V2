"""Data access layer for novels and chapters."""

from src.db.sqlite_db import get_connection
from src.utils.chapter_splitter import ChapterInfo


async def insert_novel(
    novel_id: str,
    title: str,
    author: str | None,
    file_hash: str,
    total_chapters: int,
    total_words: int,
) -> None:
    conn = await get_connection()
    try:
        await conn.execute(
            """
            INSERT INTO novels (id, title, author, file_hash, total_chapters, total_words)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (novel_id, title, author, file_hash, total_chapters, total_words),
        )
        await conn.commit()
    finally:
        await conn.close()


async def insert_chapters(
    novel_id: str,
    chapters: list[ChapterInfo],
    excluded_nums: set[int] | None = None,
) -> None:
    conn = await get_connection()
    try:
        await conn.executemany(
            """
            INSERT INTO chapters (novel_id, chapter_num, volume_num, volume_title, title, content, word_count, is_excluded)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    novel_id,
                    ch.chapter_num,
                    ch.volume_num,
                    ch.volume_title,
                    ch.title,
                    ch.content,
                    ch.word_count,
                    1 if excluded_nums and ch.chapter_num in excluded_nums else 0,
                )
                for ch in chapters
            ],
        )
        await conn.commit()
    finally:
        await conn.close()


async def list_novels() -> list[dict]:
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT
                n.id, n.title, n.author, n.total_chapters, n.total_words,
                n.created_at, n.updated_at,
                COALESCE(
                    CAST(SUM(CASE WHEN c.analysis_status = 'completed' THEN 1 ELSE 0 END) AS REAL)
                    / NULLIF(COUNT(c.id), 0),
                    0
                ) AS analysis_progress,
                COALESCE(
                    CAST(us.last_chapter AS REAL) / NULLIF(n.total_chapters, 0),
                    0
                ) AS reading_progress,
                us.updated_at AS last_opened
            FROM novels n
            LEFT JOIN chapters c ON c.novel_id = n.id
            LEFT JOIN user_state us ON us.novel_id = n.id
            GROUP BY n.id
            ORDER BY COALESCE(us.updated_at, n.updated_at) DESC
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def get_novel(novel_id: str) -> dict | None:
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT id, title, author, file_hash, total_chapters, total_words, created_at, updated_at FROM novels WHERE id = ?",
            (novel_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def delete_novel(novel_id: str) -> bool:
    """Delete a novel and all associated data. Returns True if a row was deleted."""
    conn = await get_connection()
    try:
        cursor = await conn.execute("DELETE FROM novels WHERE id = ?", (novel_id,))
        await conn.commit()
        return cursor.rowcount > 0
    finally:
        await conn.close()


async def find_by_hash(file_hash: str) -> dict | None:
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT id, title, author, total_chapters, total_words, created_at, updated_at FROM novels WHERE file_hash = ?",
            (file_hash,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()
