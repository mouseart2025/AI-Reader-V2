"""CRUD operations for world_structures and layer_layouts tables."""

from __future__ import annotations

import json
import logging

from src.db.sqlite_db import get_connection
from src.models.world_structure import Portal, WorldStructure

logger = logging.getLogger(__name__)


def _break_cycles(location_parents: dict[str, str]) -> int:
    """Break any cycles in location_parents in-place. Returns count of broken cycles."""
    checked: set[str] = set()
    broken = 0
    for start in list(location_parents):
        if start in checked:
            continue
        visited_set: set[str] = set()
        node = start
        while node in location_parents and node not in visited_set:
            visited_set.add(node)
            node = location_parents[node]
        checked.update(visited_set)
        if node in visited_set:
            # Cycle detected — break the edge FROM node
            del location_parents[node]
            broken += 1
    return broken


async def save(novel_id: str, structure: WorldStructure) -> None:
    """Insert or update a world structure for a novel."""
    # Safety net: break any cycles before persisting
    broken = _break_cycles(structure.location_parents)
    if broken:
        logger.warning(
            "Broke %d cycle(s) in location_parents before saving novel %s",
            broken, novel_id,
        )

    conn = await get_connection()
    try:
        await conn.execute(
            """
            INSERT INTO world_structures (novel_id, structure_json, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(novel_id) DO UPDATE SET
                structure_json = excluded.structure_json,
                updated_at = datetime('now')
            """,
            (novel_id, structure.model_dump_json(ensure_ascii=False)),
        )
        await conn.commit()
    finally:
        await conn.close()


async def load(novel_id: str) -> WorldStructure | None:
    """Load the world structure for a novel. Returns Pydantic model or None."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT structure_json FROM world_structures WHERE novel_id = ?",
            (novel_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        data = json.loads(row["structure_json"])
        return WorldStructure.model_validate(data)
    finally:
        await conn.close()


async def delete(novel_id: str) -> None:
    """Delete the world structure for a novel."""
    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM world_structures WHERE novel_id = ?",
            (novel_id,),
        )
        await conn.commit()
    finally:
        await conn.close()


async def save_layer_layout(
    novel_id: str,
    layer_id: str,
    chapter_hash: str,
    layout_json: str,
    layout_mode: str = "hierarchy",
    terrain_path: str | None = None,
) -> None:
    """Insert or update a cached layer layout."""
    conn = await get_connection()
    try:
        await conn.execute(
            """
            INSERT INTO layer_layouts
                (novel_id, layer_id, chapter_hash, layout_json, layout_mode, terrain_path)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(novel_id, layer_id, chapter_hash) DO UPDATE SET
                layout_json = excluded.layout_json,
                layout_mode = excluded.layout_mode,
                terrain_path = excluded.terrain_path,
                created_at = datetime('now')
            """,
            (novel_id, layer_id, chapter_hash, layout_json, layout_mode, terrain_path),
        )
        await conn.commit()
    finally:
        await conn.close()


async def load_layer_layout(
    novel_id: str, layer_id: str, chapter_hash: str
) -> dict | None:
    """Load a cached layer layout. Returns parsed dict or None."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT layout_json, layout_mode, terrain_path, created_at
            FROM layer_layouts
            WHERE novel_id = ? AND layer_id = ? AND chapter_hash = ?
            """,
            (novel_id, layer_id, chapter_hash),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "layout": json.loads(row["layout_json"]),
            "layout_mode": row["layout_mode"],
            "terrain_path": row["terrain_path"],
            "created_at": row["created_at"],
        }
    finally:
        await conn.close()


async def delete_layer_layouts(novel_id: str) -> None:
    """Delete all cached layer layouts for a novel (cache invalidation)."""
    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM layer_layouts WHERE novel_id = ?",
            (novel_id,),
        )
        await conn.commit()
    finally:
        await conn.close()


def _apply_overrides(ws: WorldStructure, overrides: list[dict]) -> WorldStructure:
    """Apply user overrides to a WorldStructure (pure function, no DB)."""
    for ov in overrides:
        ov_type = ov["override_type"]
        ov_key = ov["override_key"]
        ov_data = ov["override_json"]

        if ov_type == "location_region":
            # override_key = location name, override_json = {"region": "..."}
            new_region = ov_data.get("region", "")
            ws.location_region_map[ov_key] = new_region

        elif ov_type == "location_layer":
            # override_key = location name, override_json = {"layer_id": "..."}
            new_layer = ov_data.get("layer_id", "overworld")
            ws.location_layer_map[ov_key] = new_layer

        elif ov_type == "add_portal":
            # override_key = portal name, override_json = portal fields
            # Remove existing portal with same name first
            ws.portals = [p for p in ws.portals if p.name != ov_key]
            ws.portals.append(Portal.model_validate(ov_data))

        elif ov_type == "delete_portal":
            # override_key = portal name to delete
            ws.portals = [p for p in ws.portals if p.name != ov_key]

        elif ov_type == "location_parent":
            # override_key = location name, override_json = {"parent": "..."}
            new_parent = ov_data.get("parent", "")
            if new_parent:
                ws.location_parents[ov_key] = new_parent
            elif ov_key in ws.location_parents:
                del ws.location_parents[ov_key]

        elif ov_type == "location_tier":
            # override_key = location name, override_json = {"tier": "..."}
            new_tier = ov_data.get("tier", "")
            if new_tier:
                ws.location_tiers[ov_key] = new_tier

        else:
            logger.warning("Unknown override type: %s", ov_type)

    return ws


async def load_with_overrides(novel_id: str) -> WorldStructure:
    """Load WorldStructure with user overrides applied.

    Returns default structure if none exists.
    """
    from src.db import world_structure_override_store

    ws = await load(novel_id)
    if ws is None:
        ws = WorldStructure.create_default(novel_id)

    overrides = await world_structure_override_store.load_overrides(novel_id)
    if overrides:
        ws = _apply_overrides(ws, overrides)

    return ws
