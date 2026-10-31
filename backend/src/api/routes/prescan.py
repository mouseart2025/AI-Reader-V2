"""Pre-scan and entity dictionary endpoints."""

import asyncio

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.db import entity_dictionary_store, novel_store
from src.services import prescan_merge

router = APIRouter(prefix="/api", tags=["prescan"])

# Strong refs to fire-and-forget tasks (prevents GC mid-run, RUF006)
_background_tasks: set[asyncio.Task] = set()


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
    # issue #18: names merged into this entry by the user (empty when auto)
    merged_from: list[str] = []


class PrescanMerge(BaseModel):
    source: str
    target: str


class EntityDictionaryResponse(BaseModel):
    data: list[EntityDictItem]
    total: int
    # active merges, so the UI can offer undo without a second request
    merges: list[PrescanMerge] = []


class MergeRequest(BaseModel):
    source: str
    target: str


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

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

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
        # Type filtering happens before merging: merging never changes a type.
        raw = await entity_dictionary_store.get_by_type(novel_id, type, limit)
        entries, absorbed, merges = raw, {}, []
    else:
        entries, absorbed, merges = await prescan_merge.get_merged_dictionary(novel_id)
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
            merged_from=absorbed.get(e.name, []),
        )
        for e in entries
    ]

    return EntityDictionaryResponse(
        data=data,
        total=len(data),
        merges=[PrescanMerge(**m) for m in merges],
    )


# ---------------------------------------------------------------------------
# Pre-scan candidate merges (issue #18)
#
# Raw `entity_dictionary` rows are never rewritten: each merge is an override
# row, the merged view is computed on read, and DELETE restores the auto view.
# ---------------------------------------------------------------------------

@router.post("/novels/{novel_id}/entity-dictionary/merge")
async def merge_dictionary_entries(novel_id: str, req: MergeRequest):
    """Merge `source` into `target` (issue #18: translation variants, nicknames)."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    source, target = req.source.strip(), req.target.strip()
    if not source or not target:
        raise HTTPException(status_code=400, detail="source 与 target 不能为空")
    if source == target:
        raise HTTPException(status_code=400, detail="不能把实体合并到自己")

    raw = await entity_dictionary_store.get_all(novel_id)
    existing = {e.name for e in raw}
    missing = [n for n in (source, target) if n not in existing]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"实体字典中没有这些候选：{'、'.join(missing)}",
        )

    # Detect cycles cheaply: target must not already be absorbed into source.
    # (merge_entries() would collapse the cycle, but silently picking a
    # canonical would surprise the user.)
    current = {m["source"]: m["target"] for m in await prescan_merge.list_merges(novel_id)}
    probe, seen = target, set()
    while probe in current and probe not in seen:
        seen.add(probe)
        probe = current[probe]
    if probe == source:
        raise HTTPException(status_code=400, detail="合并会形成环，请先解除已有合并")

    await prescan_merge.save_merge(novel_id, source, target)
    entries, absorbed, merges = await prescan_merge.get_merged_dictionary(novel_id)
    return {
        "status": "ok",
        "merges": merges,
        "canonical": next((c for c, names in absorbed.items() if source in names), target),
        "total": len(entries),
    }


@router.delete("/novels/{novel_id}/entity-dictionary/merge/{source}")
async def unmerge_dictionary_entries(novel_id: str, source: str):
    """Undo one merge (issue #18). Restores the automatic view for that name."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    removed = await prescan_merge.delete_merge(novel_id, source)
    if not removed:
        raise HTTPException(status_code=404, detail="没有这条合并记录")
    entries, _absorbed, merges = await prescan_merge.get_merged_dictionary(novel_id)
    return {"status": "ok", "merges": merges, "total": len(entries)}
