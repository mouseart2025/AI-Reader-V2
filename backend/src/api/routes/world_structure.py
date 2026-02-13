"""WorldStructure API endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.db import novel_store, world_structure_store, world_structure_override_store
from src.models.world_structure import WorldStructure

router = APIRouter(prefix="/api/novels/{novel_id}/world-structure", tags=["world-structure"])


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
