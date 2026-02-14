"""Entity aggregation endpoints."""

from fastapi import APIRouter, HTTPException, Query

from src.db import novel_store
from src.services import entity_aggregator
from src.services.alias_resolver import build_alias_map

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

    alias_map = await build_alias_map(novel_id)

    return {
        "entities": [e.model_dump() for e in entities],
        "alias_map": alias_map,
    }


@router.get("/{name}")
async def get_entity(
    novel_id: str,
    name: str,
    type: str | None = Query(None, description="Entity type hint: person/location/item/org"),
):
    """Get the full aggregated profile for a single entity.

    If `name` is an alias, it is automatically resolved to the canonical name.
    """
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    # Resolve alias to canonical name
    alias_map = await build_alias_map(novel_id)
    resolved_name = alias_map.get(name, name)

    # If type is provided, use it directly. Otherwise, detect from entity list.
    entity_type = type
    if not entity_type:
        entities = await entity_aggregator.get_all_entities(novel_id)
        for e in entities:
            if e.name == resolved_name:
                entity_type = e.type
                break

    if not entity_type:
        raise HTTPException(status_code=404, detail="实体不存在")

    if entity_type == "person":
        profile = await entity_aggregator.aggregate_person(novel_id, resolved_name)
    elif entity_type == "location":
        profile = await entity_aggregator.aggregate_location(novel_id, resolved_name)
    elif entity_type == "item":
        profile = await entity_aggregator.aggregate_item(novel_id, resolved_name)
    elif entity_type == "org":
        profile = await entity_aggregator.aggregate_org(novel_id, resolved_name)
    else:
        raise HTTPException(status_code=400, detail=f"不支持的实体类型: {entity_type}")

    return profile.model_dump()
