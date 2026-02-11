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


async def insert_chapters(novel_id: str, chapters: list[ChapterInfo]) -> None:
    conn = await get_connection()
    try:
        await conn.executemany(
            """
            INSERT INTO chapters (novel_id, chapter_num, volume_num, volume_title, title, content, word_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
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
            "SELECT id, title, author, total_chapters, total_words, created_at, updated_at FROM novels ORDER BY created_at DESC"
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
