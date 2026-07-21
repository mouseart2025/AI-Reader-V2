"""CRUD operations for entity_overrides table.

Stores user-edited alias merges/splits as an override layer applied on top of
automatic alias resolution (see alias_resolver._apply_user_overrides). Mirrors
world_structure_override_store; the same (novel_id, type, key) UPSERT contract.

override_type semantics:
- "alias_merge": override_key = chosen canonical; override_json =
    {"members": [...all aliases incl. former canonicals...], "canonical": str,
     "auto_snapshot": {alias: auto_canonical_or_null}}  # snapshot optional
- "alias_split": override_key = source canonical; override_json =
    {"aliases": [...detached aliases...], "to": str | None,  # None => new entity
     "auto_snapshot": {alias: auto_canonical_or_null}}
"""

from __future__ import annotations

import json

from src.db.sqlite_db import get_connection


async def save_override(
    novel_id: str,
    override_type: str,
    override_key: str,
    override_json: dict,
) -> int:
    """Insert or update an entity override. Returns the override id."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            INSERT INTO entity_overrides
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
    """Load all entity overrides for a novel, oldest first (apply order)."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT id, override_type, override_key, override_json, created_at
            FROM entity_overrides
            WHERE novel_id = ?
            ORDER BY created_at, id
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
            "DELETE FROM entity_overrides WHERE id = ? AND novel_id = ?",
            (override_id, novel_id),
        )
        await conn.commit()
        return cursor.rowcount > 0
    finally:
        await conn.close()


async def delete_all_overrides(novel_id: str) -> int:
    """Delete all entity overrides for a novel. Returns count deleted."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "DELETE FROM entity_overrides WHERE novel_id = ?",
            (novel_id,),
        )
        await conn.commit()
        return cursor.rowcount
    finally:
        await conn.close()
