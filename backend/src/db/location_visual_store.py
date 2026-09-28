"""CRUD for the `location_visuals` table (derived terrain/landmark per location)."""

from __future__ import annotations

import json
import logging

from src.db.sqlite_db import get_connection
from src.models.location_visual import LocationVisual

logger = logging.getLogger(__name__)


async def save(novel_id: str, visuals: list[LocationVisual], model: str = "") -> int:
    """Upsert one row per location. Returns the number of rows written.

    Rows are keyed on (novel_id, location_name), so re-running an extraction
    replaces rather than appends — this table is a derived cache, and a stale row
    from an older prompt would otherwise survive forever.
    """
    if not visuals:
        return 0
    rows = [
        (
            novel_id,
            v.name,
            v.terrain.value if v.terrain else None,
            v.landmark.value if v.landmark else None,
            v.evidence or "",
            v.raw or "",
            model or "",
        )
        for v in visuals
    ]
    conn = await get_connection()
    try:
        await conn.executemany(
            """
            INSERT INTO location_visuals
                (novel_id, location_name, terrain, landmark, evidence, raw_json,
                 llm_model, extracted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(novel_id, location_name) DO UPDATE SET
                terrain=excluded.terrain,
                landmark=excluded.landmark,
                evidence=excluded.evidence,
                raw_json=excluded.raw_json,
                llm_model=excluded.llm_model,
                extracted_at=excluded.extracted_at
            """,
            rows,
        )
        await conn.commit()
    finally:
        await conn.close()
    logger.info("Saved %d location visuals for %s", len(rows), novel_id)
    return len(rows)


async def load(novel_id: str) -> dict[str, LocationVisual]:
    """All visuals for a novel, keyed by location name."""
    conn = await get_connection()
    try:
        cur = await conn.execute(
            "SELECT location_name, terrain, landmark, evidence, raw_json "
            "FROM location_visuals WHERE novel_id=?",
            (novel_id,),
        )
        rows = await cur.fetchall()
    finally:
        await conn.close()

    out: dict[str, LocationVisual] = {}
    for name, terrain, landmark, evidence, raw in rows:
        try:
            out[name] = LocationVisual(
                name=name,
                terrain=terrain,
                landmark=landmark,
                evidence=evidence or "",
                raw=raw or "",
            )
        except (ValueError, TypeError):
            # A hand-edited or older row can hold a value the enum no longer has.
            # Skipping keeps the map bake running; the row stays visible in the DB.
            logger.warning("Skipping unreadable location visual row: %s", name)
    return out


async def terrain_by_name(novel_id: str) -> dict[str, str]:
    """Just the non-null terrain values — what the terrain bake actually needs."""
    return {
        name: v.terrain.value
        for name, v in (await load(novel_id)).items()
        if v.terrain is not None
    }


async def clear(novel_id: str) -> int:
    conn = await get_connection()
    try:
        cur = await conn.execute(
            "DELETE FROM location_visuals WHERE novel_id=?", (novel_id,)
        )
        await conn.commit()
        return cur.rowcount or 0
    finally:
        await conn.close()


def dump(visuals: dict[str, LocationVisual]) -> str:
    """JSON form, for scripts that want to eyeball a whole novel at once."""
    return json.dumps(
        {n: v.model_dump(mode="json") for n, v in visuals.items()},
        ensure_ascii=False,
        indent=1,
    )
