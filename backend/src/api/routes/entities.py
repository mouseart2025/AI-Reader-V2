"""Entity aggregation endpoints."""

from fastapi import APIRouter, HTTPException, Query

from src.db import novel_store
from src.services import entity_aggregator

router = APIRouter(prefix="/api/novels/{novel_id}/entities", tags=["entities"])


@router.get("")
async def list_entities(
    novel_id: str,
    type: str | None = Query(None, description="Filter by entity type: person/location/item/org/concept"),
):
    """Get all entities for a novel, optionally filtered by type."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    entities = await entity_aggregator.get_all_entities(novel_id)
    if type:
        entities = [e for e in entities if e.type == type]

    return {"entities": [e.model_dump() for e in entities]}


@router.get("/{name}")
async def get_entity(
    novel_id: str,
    name: str,
    type: str | None = Query(None, description="Entity type hint: person/location/item/org"),
):
    """Get the full aggregated profile for a single entity."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    # If type is provided, use it directly. Otherwise, detect from entity list.
    entity_type = type
    if not entity_type:
        entities = await entity_aggregator.get_all_entities(novel_id)
        for e in entities:
            if e.name == name:
                entity_type = e.type
                break

    if not entity_type:
        raise HTTPException(status_code=404, detail="实体不存在")

    if entity_type == "person":
        profile = await entity_aggregator.aggregate_person(novel_id, name)
    elif entity_type == "location":
        profile = await entity_aggregator.aggregate_location(novel_id, name)
    elif entity_type == "item":
        profile = await entity_aggregator.aggregate_item(novel_id, name)
    elif entity_type == "org":
        profile = await entity_aggregator.aggregate_org(novel_id, name)
    else:
        raise HTTPException(status_code=400, detail=f"不支持的实体类型: {entity_type}")

    return profile.model_dump()
