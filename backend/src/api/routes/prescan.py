"""Pre-scan and entity dictionary endpoints."""

import asyncio

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.db import entity_dictionary_store, novel_store

router = APIRouter(prefix="/api", tags=["prescan"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class PrescanStatusResponse(BaseModel):
    status: str
    entity_count: int
    created_at: str | None = None


class EntityDictItem(BaseModel):
    name: str
    entity_type: str
    frequency: int
    confidence: str
    aliases: list[str]
    source: str
    sample_context: str | None = None


class EntityDictionaryResponse(BaseModel):
    data: list[EntityDictItem]
    total: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/novels/{novel_id}/prescan")
async def trigger_prescan(novel_id: str):
    """Manually trigger a pre-scan. Returns 409 if already running."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    status = await entity_dictionary_store.get_prescan_status(novel_id)
    if status == "running":
        raise HTTPException(status_code=409, detail="预扫描正在进行中")

    # If completed, delete existing and re-scan
    if status == "completed":
        await entity_dictionary_store.delete_all(novel_id)

    # Trigger in background
    async def _run() -> None:
        from src.extraction.entity_pre_scanner import EntityPreScanner
        scanner = EntityPreScanner()
        await scanner.scan(novel_id)

    asyncio.create_task(_run())

    return {"status": "running"}


@router.get("/novels/{novel_id}/prescan", response_model=PrescanStatusResponse)
async def get_prescan_status(novel_id: str):
    """Query pre-scan status and entity count."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    status = await entity_dictionary_store.get_prescan_status(novel_id)
    entries = await entity_dictionary_store.get_all(novel_id)

    return PrescanStatusResponse(
        status=status,
        entity_count=len(entries),
        created_at=novel.get("created_at"),
    )


@router.get(
    "/novels/{novel_id}/entity-dictionary",
    response_model=EntityDictionaryResponse,
)
async def get_entity_dictionary(
    novel_id: str,
    type: str | None = Query(None, description="Filter by entity type"),
    limit: int = Query(100, ge=1, le=500),
):
    """Get entity dictionary contents."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    if type:
        entries = await entity_dictionary_store.get_by_type(novel_id, type, limit)
    else:
        entries = await entity_dictionary_store.get_all(novel_id)
        entries = entries[:limit]

    data = [
        EntityDictItem(
            name=e.name,
            entity_type=e.entity_type,
            frequency=e.frequency,
            confidence=e.confidence,
            aliases=e.aliases,
            source=e.source,
            sample_context=e.sample_context,
        )
        for e in entries
    ]

    return EntityDictionaryResponse(data=data, total=len(data))
