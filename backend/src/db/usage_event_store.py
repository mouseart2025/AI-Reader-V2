"""CRUD operations for usage_events table — anonymous local analytics."""

import json
import logging

from src.db.sqlite_db import get_connection

logger = logging.getLogger(__name__)


async def record_event(event_type: str, metadata: dict | None = None) -> None:
    """Insert a usage event."""
    conn = await get_connection()
    try:
        await conn.execute(
            "INSERT INTO usage_events (event_type, metadata) VALUES (?, ?)",
            (event_type, json.dumps(metadata or {}, ensure_ascii=False)),
        )
        await conn.commit()
    finally:
        await conn.close()


async def get_event_stats(days: int = 30) -> list[dict]:
    """Get event type frequency counts for the last N days."""
    conn = await get_connection()
    try:
        rows = await conn.execute_fetchall(
            """
            SELECT event_type, COUNT(*) as count
            FROM usage_events
            WHERE created_at >= datetime('now', ?)
            GROUP BY event_type
            ORDER BY count DESC
            """,
            (f"-{days} days",),
        )
        return [{"event_type": r[0], "count": r[1]} for r in rows]
    finally:
        await conn.close()


async def get_daily_trend(days: int = 30) -> list[dict]:
    """Get daily event counts for the last N days."""
    conn = await get_connection()
    try:
        rows = await conn.execute_fetchall(
            """
            SELECT DATE(created_at) as day, COUNT(*) as count
            FROM usage_events
            WHERE created_at >= datetime('now', ?)
            GROUP BY day
            ORDER BY day
            """,
            (f"-{days} days",),
        )
        return [{"day": r[0], "count": r[1]} for r in rows]
    finally:
        await conn.close()


async def get_total_count() -> int:
    """Get total event count."""
    conn = await get_connection()
    try:
        rows = await conn.execute_fetchall("SELECT COUNT(*) FROM usage_events")
        return rows[0][0] if rows else 0
    finally:
        await conn.close()


async def clear_all_events() -> int:
    """Delete all usage events. Returns number of deleted rows."""
    conn = await get_connection()
    try:
        cursor = await conn.execute("DELETE FROM usage_events")
        await conn.commit()
        return cursor.rowcount
    finally:
        await conn.close()
