"""Usage analytics — local anonymous event tracking."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/usage", tags=["usage"])


class TrackEventRequest(BaseModel):
    event_type: str
    metadata: dict | None = None


async def _is_tracking_enabled() -> bool:
    """Check if usage tracking is enabled (default: True)."""
    from src.db.sqlite_db import get_connection

    conn = await get_connection()
    try:
        rows = await conn.execute_fetchall(
            "SELECT value FROM app_settings WHERE key = 'tracking_enabled'"
        )
        if rows and rows[0][0] == "false":
            return False
        return True
    except Exception:
        return True
    finally:
        await conn.close()


@router.post("/track")
async def track_event(body: TrackEventRequest):
    """Record a usage event (anonymous, local only)."""
    from src.db import usage_event_store

    if not await _is_tracking_enabled():
        return {"ok": False, "reason": "tracking_disabled"}

    await usage_event_store.record_event(body.event_type, body.metadata)
    return {"ok": True}


@router.get("/stats")
async def get_stats(days: int = Query(default=30, ge=1, le=365)):
    """Get event frequency stats for the last N days."""
    from src.db import usage_event_store

    stats = await usage_event_store.get_event_stats(days)
    trend = await usage_event_store.get_daily_trend(days)
    total = await usage_event_store.get_total_count()

    return {
        "total_events": total,
        "by_type": stats,
        "daily_trend": trend,
        "days": days,
    }


@router.delete("/clear")
async def clear_events():
    """Delete all collected usage events."""
    from src.db import usage_event_store

    deleted = await usage_event_store.clear_all_events()
    return {"ok": True, "deleted": deleted}


@router.get("/tracking-enabled")
async def get_tracking_status():
    """Get current tracking enabled/disabled status."""
    enabled = await _is_tracking_enabled()
    return {"enabled": enabled}


@router.put("/tracking-enabled")
async def set_tracking_status(body: dict):
    """Set tracking enabled/disabled."""
    from src.db.sqlite_db import get_connection

    enabled = body.get("enabled", True)
    conn = await get_connection()
    try:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES ('tracking_enabled', ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (str(enabled).lower(),),
        )
        await conn.commit()
    finally:
        await conn.close()
    return {"ok": True, "enabled": enabled}
