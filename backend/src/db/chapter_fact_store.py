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
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    cost_cny: float = 0.0,
    is_truncated: bool = False,
    segment_count: int = 1,
) -> None:
    """Insert or replace a chapter fact record."""
    conn = await get_connection()
    try:
        await conn.execute(
            """
            INSERT OR REPLACE INTO chapter_facts
                (novel_id, chapter_id, fact_json, llm_model, extraction_ms,
                 input_tokens, output_tokens, cost_usd, cost_cny,
                 is_truncated, segment_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                novel_id,
                chapter_id,
                fact.model_dump_json(ensure_ascii=False),
                llm_model,
                extraction_ms,
                input_tokens,
                output_tokens,
                cost_usd,
                cost_cny,
                1 if is_truncated else 0,
                segment_count,
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
            SELECT fact_json, llm_model, extracted_at, extraction_ms,
                   input_tokens, output_tokens, cost_usd, cost_cny
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
            "input_tokens": row["input_tokens"] or 0,
            "output_tokens": row["output_tokens"] or 0,
            "cost_usd": row["cost_usd"] or 0.0,
            "cost_cny": row["cost_cny"] or 0.0,
        }
    finally:
        await conn.close()


async def get_all_chapter_facts(novel_id: str) -> list[dict]:
    """Retrieve all chapter facts for a novel, ordered by chapter_id."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT chapter_id, fact_json, llm_model, extracted_at, extraction_ms,
                   input_tokens, output_tokens, cost_usd, cost_cny,
                   is_truncated, segment_count
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
                "input_tokens": row["input_tokens"] or 0,
                "output_tokens": row["output_tokens"] or 0,
                "cost_usd": row["cost_usd"] or 0.0,
                "cost_cny": row["cost_cny"] or 0.0,
                "is_truncated": bool(row["is_truncated"]) if row["is_truncated"] is not None else False,
                "segment_count": row["segment_count"] or 1,
            }
            for row in rows
        ]
    finally:
        await conn.close()


async def update_scenes(
    novel_id: str, chapter_id: int, scenes: list[dict]
) -> None:
    """Store LLM-extracted scenes for a chapter."""
    conn = await get_connection()
    try:
        scenes_text = json.dumps(scenes, ensure_ascii=False)
        await conn.execute(
            """
            UPDATE chapter_facts SET scenes_json = ?
            WHERE novel_id = ? AND chapter_id = ?
            """,
            (scenes_text, novel_id, chapter_id),
        )
        await conn.commit()
    finally:
        await conn.close()


async def get_chapter_scenes(novel_id: str, chapter_id: int) -> list[dict] | None:
    """Retrieve LLM-extracted scenes for a chapter. Returns list or None."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT scenes_json FROM chapter_facts
            WHERE novel_id = ? AND chapter_id = ?
            """,
            (novel_id, chapter_id),
        )
        row = await cursor.fetchone()
        if row is None or row["scenes_json"] is None:
            return None
        return json.loads(row["scenes_json"])
    finally:
        await conn.close()


async def get_all_scenes(novel_id: str) -> list[dict]:
    """Retrieve all scenes across all chapters for a novel.

    Returns a flat list of scene dicts, each augmented with ``chapter``
    (the chapter_id).  Only chapters that have non-null ``scenes_json``
    are included.  The list is ordered by chapter_id, then by the scene's
    original index within the chapter.
    """
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT chapter_id, scenes_json FROM chapter_facts
            WHERE novel_id = ? AND scenes_json IS NOT NULL
            ORDER BY chapter_id
            """,
            (novel_id,),
        )
        rows = await cursor.fetchall()
        result: list[dict] = []
        for row in rows:
            scenes = json.loads(row["scenes_json"])
            for idx, scene in enumerate(scenes):
                scene["chapter"] = row["chapter_id"]
                scene.setdefault("index", idx)
                result.append(scene)
        return result
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
