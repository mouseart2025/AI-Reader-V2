"""WorldStructure API endpoints."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.db import novel_store, world_structure_store, world_structure_override_store
from src.models.world_structure import WorldStructure

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/novels/{novel_id}/world-structure", tags=["world-structure"])


async def _redetect_genre(novel_id: str, agent) -> None:
    """Re-detect genre from first 10 chapter texts using updated keyword lists.

    This fixes genre misdetection for novels analyzed with older keyword lists
    (e.g., Water Margin classified as 'fantasy' due to broad single-char keywords).
    """
    from src.db.sqlite_db import get_connection
    import json as _json
    from src.models.chapter_fact import ChapterFact

    ws = agent.structure
    if ws is None:
        return

    # Reset genre detection state
    ws.novel_genre_hint = None
    if hasattr(agent, "_genre_scores"):
        del agent._genre_scores

    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """SELECT c.chapter_num, c.content, cf.fact_json
            FROM chapters c
            JOIN chapter_facts cf ON c.id = cf.chapter_id AND c.novel_id = cf.novel_id
            WHERE c.novel_id = ? AND c.chapter_num <= 10
            ORDER BY c.chapter_num""",
            (novel_id,),
        )
        rows = await cursor.fetchall()
    finally:
        await conn.close()

    for row in rows:
        chapter_text = row["content"] or ""
        data = _json.loads(row["fact_json"])
        fact = ChapterFact.model_validate({**data, "chapter_id": row["chapter_num"], "novel_id": novel_id})
        agent._detect_genre(chapter_text, fact)

    # Also recalculate spatial scale
    ws.spatial_scale = agent._detect_spatial_scale()

    logger.info(
        "Re-detected genre=%s, scale=%s for novel %s",
        ws.novel_genre_hint, ws.spatial_scale, novel_id,
    )


class OverrideItem(BaseModel):
    override_type: str
    override_key: str
    override_json: dict


class OverridesBatch(BaseModel):
    overrides: list[OverrideItem]


@router.get("")
async def get_world_structure(novel_id: str):
    """Return the WorldStructure for a novel (with overrides applied)."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    ws = await world_structure_store.load_with_overrides(novel_id)
    return ws.model_dump()


@router.get("/overrides")
async def get_overrides(novel_id: str):
    """Return all user overrides for this novel's world structure."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    overrides = await world_structure_override_store.load_overrides(novel_id)
    return {"overrides": overrides}


@router.put("/overrides")
async def save_overrides(novel_id: str, body: OverridesBatch):
    """Save a batch of overrides and return the merged WorldStructure."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    valid_types = {"location_region", "location_layer", "add_portal", "delete_portal"}
    for item in body.overrides:
        if item.override_type not in valid_types:
            raise HTTPException(
                status_code=400,
                detail=f"无效的 override_type: {item.override_type}",
            )

    for item in body.overrides:
        await world_structure_override_store.save_override(
            novel_id, item.override_type, item.override_key, item.override_json,
        )

    # Invalidate layout cache since structure changed
    await world_structure_store.delete_layer_layouts(novel_id)

    ws = await world_structure_store.load_with_overrides(novel_id)
    return ws.model_dump()


@router.post("/rebuild-hierarchy")
async def rebuild_hierarchy(novel_id: str):
    """Rebuild location_parents from existing chapter_facts without re-running LLM.

    Uses the improved voting logic (generic location filtering, contains direction
    validation, defensive weight reduction) to fix hierarchy issues in already-analyzed
    novels.
    """
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    ws = await world_structure_store.load(novel_id)
    if not ws:
        raise HTTPException(status_code=404, detail="WorldStructure 不存在，请先分析小说")

    from src.services.world_structure_agent import WorldStructureAgent

    agent = WorldStructureAgent(novel_id)
    agent.structure = ws

    # Re-detect genre from chapter texts (fixes misdetection for existing data)
    await _redetect_genre(novel_id, agent)

    # Load user overrides so they take precedence
    overrides = await world_structure_override_store.load_overrides(novel_id)
    for ov in overrides:
        agent._overridden_keys.add((ov["override_type"], ov["override_key"]))

    # Rebuild parent votes from all chapter_facts with improved validation
    agent._parent_votes = await agent._rebuild_parent_votes()

    # Resolve parents with tier validation + cycle detection
    old_count = len(ws.location_parents)
    ws.location_parents = agent._resolve_parents()

    # Consolidate hierarchy: reduce roots to single digits
    from src.services.hierarchy_consolidator import consolidate_hierarchy
    ws.location_parents, ws.location_tiers = consolidate_hierarchy(
        ws.location_parents,
        ws.location_tiers,
        novel_genre_hint=ws.novel_genre_hint,
        parent_votes=agent._parent_votes,
    )
    new_count = len(ws.location_parents)

    # Count final roots
    children = set(ws.location_parents.keys())
    parents = set(ws.location_parents.values())
    final_roots = sorted(parents - children)

    await world_structure_store.save(novel_id, ws)

    # Invalidate layout cache since hierarchy changed
    await world_structure_store.delete_layer_layouts(novel_id)

    logger.info(
        "Rebuilt location hierarchy for %s: %d → %d parents, %d roots",
        novel_id, old_count, new_count, len(final_roots),
    )
    return {
        "status": "ok",
        "old_parent_count": old_count,
        "new_parent_count": new_count,
        "root_count": len(final_roots),
        "roots": final_roots[:20],
    }


@router.delete("/overrides/{override_id}")
async def delete_override(novel_id: str, override_id: int):
    """Delete a specific override and return the updated WorldStructure."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    deleted = await world_structure_override_store.delete_override(novel_id, override_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Override 不存在")

    # Invalidate layout cache
    await world_structure_store.delete_layer_layouts(novel_id)

    ws = await world_structure_store.load_with_overrides(novel_id)
    return ws.model_dump()
