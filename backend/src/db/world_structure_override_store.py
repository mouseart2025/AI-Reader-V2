"""CRUD operations for world_structure_overrides table."""

from __future__ import annotations

import json

from src.db.sqlite_db import get_connection


async def save_override(
    novel_id: str,
    override_type: str,
    override_key: str,
    override_json: dict,
) -> int:
    """Insert or update an override. Returns the override id."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            INSERT INTO world_structure_overrides
                (novel_id, override_type, override_key, override_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(novel_id, override_type, override_key) DO UPDATE SET
                override_json = excluded.override_json,
                created_at = datetime('now')
            """,
            (novel_id, override_type, override_key, json.dumps(override_json, ensure_ascii=False)),
        )
        await conn.commit()
        return cursor.lastrowid or 0
    finally:
        await conn.close()


async def load_overrides(novel_id: str) -> list[dict]:
    """Load all overrides for a novel. Returns list of dicts with id, type, key, json."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT id, override_type, override_key, override_json, created_at
            FROM world_structure_overrides
            WHERE novel_id = ?
            ORDER BY created_at
            """,
            (novel_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "override_type": row["override_type"],
                "override_key": row["override_key"],
                "override_json": json.loads(row["override_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    finally:
        await conn.close()


async def delete_override(novel_id: str, override_id: int) -> bool:
    """Delete a specific override. Returns True if a row was deleted."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "DELETE FROM world_structure_overrides WHERE id = ? AND novel_id = ?",
            (override_id, novel_id),
        )
        await conn.commit()
        return cursor.rowcount > 0
    finally:
        await conn.close()


async def delete_all_overrides(novel_id: str) -> int:
    """Delete all overrides for a novel. Returns count deleted."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "DELETE FROM world_structure_overrides WHERE novel_id = ?",
            (novel_id,),
        )
        await conn.commit()
        return cursor.rowcount
    finally:
        await conn.close()


async def get_overridden_keys(novel_id: str) -> set[tuple[str, str]]:
    """Return set of (override_type, override_key) for quick lookup."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT override_type, override_key FROM world_structure_overrides WHERE novel_id = ?",
            (novel_id,),
        )
        rows = await cursor.fetchall()
        return {(row["override_type"], row["override_key"]) for row in rows}
    finally:
        await conn.close()
