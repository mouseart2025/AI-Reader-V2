"""Encyclopedia endpoints: category stats, entry list, concept detail."""

from fastapi import APIRouter, HTTPException, Query

from src.db import novel_store
from src.services import encyclopedia_service

router = APIRouter(prefix="/api/novels/{novel_id}/encyclopedia", tags=["encyclopedia"])


@router.get("")
async def get_stats(novel_id: str):
    """Get category statistics for the encyclopedia."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    stats = await encyclopedia_service.get_category_stats(novel_id)
    return stats


@router.get("/entries")
async def get_entries(
    novel_id: str,
    category: str | None = Query(None, description="Category filter: person/location/item/org/concept or sub-category"),
    sort: str = Query("name", description="Sort by: name or chapter"),
):
    """Get encyclopedia entries, optionally filtered by category."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    entries = await encyclopedia_service.get_encyclopedia_entries(novel_id, category, sort)
    return {"entries": entries}


@router.get("/location-conflicts")
async def get_location_conflicts(novel_id: str):
    """Get location hierarchy conflicts grouped by location name."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    return await encyclopedia_service.get_location_conflicts_summary(novel_id)


@router.get("/{name}")
async def get_concept(novel_id: str, name: str):
    """Get concept detail by name."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    detail = await encyclopedia_service.get_concept_detail(novel_id, name)
    if detail is None:
        raise HTTPException(status_code=404, detail="概念不存在")
    return detail


@router.get("/{name}/spatial")
async def get_spatial_summary(novel_id: str, name: str):
    """Get spatial relationships for a location."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    return await encyclopedia_service.get_location_spatial_summary(novel_id, name)


@router.get("/{name}/scenes")
async def get_entity_scenes(novel_id: str, name: str):
    """Get scenes involving an entity."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    return await encyclopedia_service.get_entity_scenes(novel_id, name)
