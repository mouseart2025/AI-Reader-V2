"""CRUD operations for entity_dictionary table and novels.prescan_status."""

import json

from src.db.sqlite_db import get_connection
from src.models.entity_dict import EntityDictEntry


async def insert_batch(novel_id: str, entries: list[EntityDictEntry]) -> int:
    """Batch insert dictionary entries. Uses INSERT OR REPLACE for dedup."""
    if not entries:
        return 0
    conn = await get_connection()
    try:
        await conn.executemany(
            """
            INSERT OR REPLACE INTO entity_dictionary
                (novel_id, name, entity_type, frequency, confidence, aliases, source, sample_context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    novel_id,
                    e.name,
                    e.entity_type,
                    e.frequency,
                    e.confidence,
                    json.dumps(e.aliases, ensure_ascii=False),
                    e.source,
                    e.sample_context,
                )
                for e in entries
            ],
        )
        await conn.commit()
        return len(entries)
    finally:
        await conn.close()


async def get_all(novel_id: str) -> list[EntityDictEntry]:
    """Get all dictionary entries for a novel, ordered by frequency DESC."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT name, entity_type, frequency, confidence, aliases, source, sample_context
            FROM entity_dictionary
            WHERE novel_id = ?
            ORDER BY frequency DESC
            """,
            (novel_id,),
        )
        rows = await cursor.fetchall()
        return [
            EntityDictEntry(
                name=row["name"],
                entity_type=row["entity_type"] or "unknown",
                frequency=row["frequency"],
                confidence=row["confidence"],
                aliases=json.loads(row["aliases"]) if row["aliases"] else [],
                source=row["source"],
                sample_context=row["sample_context"],
            )
            for row in rows
        ]
    finally:
        await conn.close()


async def get_by_type(
    novel_id: str, entity_type: str, limit: int = 50
) -> list[EntityDictEntry]:
    """Get dictionary entries filtered by entity type."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT name, entity_type, frequency, confidence, aliases, source, sample_context
            FROM entity_dictionary
            WHERE novel_id = ? AND entity_type = ?
            ORDER BY frequency DESC
            LIMIT ?
            """,
            (novel_id, entity_type, limit),
        )
        rows = await cursor.fetchall()
        return [
            EntityDictEntry(
                name=row["name"],
                entity_type=row["entity_type"] or "unknown",
                frequency=row["frequency"],
                confidence=row["confidence"],
                aliases=json.loads(row["aliases"]) if row["aliases"] else [],
                source=row["source"],
                sample_context=row["sample_context"],
            )
            for row in rows
        ]
    finally:
        await conn.close()


async def delete_all(novel_id: str) -> int:
    """Delete all dictionary entries for a novel. Returns deleted count."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "DELETE FROM entity_dictionary WHERE novel_id = ?",
            (novel_id,),
        )
        await conn.commit()
        return cursor.rowcount
    finally:
        await conn.close()


async def get_prescan_status(novel_id: str) -> str:
    """Get prescan status for a novel. Returns 'pending' if not found."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT prescan_status FROM novels WHERE id = ?",
            (novel_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return "pending"
        return row["prescan_status"] or "pending"
    finally:
        await conn.close()


async def update_prescan_status(novel_id: str, status: str) -> None:
    """Update prescan status for a novel."""
    conn = await get_connection()
    try:
        await conn.execute(
            "UPDATE novels SET prescan_status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, novel_id),
        )
        await conn.commit()
    finally:
        await conn.close()
