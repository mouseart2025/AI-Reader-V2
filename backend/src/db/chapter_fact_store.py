"""CRUD operations for chapter_facts table."""

import json

from src.db.sqlite_db import get_connection
from src.models.chapter_fact import ChapterFact


async def insert_chapter_fact(
    novel_id: str,
    chapter_id: int,
    fact: ChapterFact,
    llm_model: str,
    extraction_ms: int,
) -> None:
    """Insert or replace a chapter fact record."""
    conn = await get_connection()
    try:
        await conn.execute(
            """
            INSERT OR REPLACE INTO chapter_facts
                (novel_id, chapter_id, fact_json, llm_model, extraction_ms)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                novel_id,
                chapter_id,
                fact.model_dump_json(ensure_ascii=False),
                llm_model,
                extraction_ms,
            ),
        )
        await conn.commit()
    finally:
        await conn.close()


async def get_chapter_fact(novel_id: str, chapter_id: int) -> dict | None:
    """Retrieve a single chapter fact. Returns parsed dict or None."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT fact_json, llm_model, extracted_at, extraction_ms
            FROM chapter_facts
            WHERE novel_id = ? AND chapter_id = ?
            """,
            (novel_id, chapter_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "fact": json.loads(row["fact_json"]),
            "llm_model": row["llm_model"],
            "extracted_at": row["extracted_at"],
            "extraction_ms": row["extraction_ms"],
        }
    finally:
        await conn.close()


async def get_all_chapter_facts(novel_id: str) -> list[dict]:
    """Retrieve all chapter facts for a novel, ordered by chapter_id."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT chapter_id, fact_json, llm_model, extracted_at, extraction_ms
            FROM chapter_facts
            WHERE novel_id = ?
            ORDER BY chapter_id
            """,
            (novel_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "chapter_id": row["chapter_id"],
                "fact": json.loads(row["fact_json"]),
                "llm_model": row["llm_model"],
                "extracted_at": row["extracted_at"],
                "extraction_ms": row["extraction_ms"],
            }
            for row in rows
        ]
    finally:
        await conn.close()


async def delete_chapter_facts(
    novel_id: str, chapter_ids: list[int] | None = None
) -> None:
    """Delete chapter facts. If chapter_ids is None, delete all for the novel."""
    conn = await get_connection()
    try:
        if chapter_ids is None:
            await conn.execute(
                "DELETE FROM chapter_facts WHERE novel_id = ?",
                (novel_id,),
            )
        else:
            placeholders = ",".join("?" for _ in chapter_ids)
            await conn.execute(
                f"DELETE FROM chapter_facts WHERE novel_id = ? AND chapter_id IN ({placeholders})",
                (novel_id, *chapter_ids),
            )
        await conn.commit()
    finally:
        await conn.close()
