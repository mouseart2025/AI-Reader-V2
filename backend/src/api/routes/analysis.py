"""Analysis task management endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.db import (
    analysis_task_store,
    chapter_fact_store,
    novel_store,
    world_structure_override_store,
    world_structure_store,
)
from src.db.sqlite_db import get_connection
from src.services.analysis_service import get_analysis_service

router = APIRouter(prefix="/api", tags=["analysis"])


class AnalyzeRequest(BaseModel):
    chapter_start: int | None = None
    chapter_end: int | None = None
    force: bool = False  # True to re-analyze already-completed chapters


class PatchTaskRequest(BaseModel):
    status: str  # "paused" | "running" | "cancelled"


@router.get("/analysis/active")
async def get_active_analyses():
    """Return novel IDs with their active analysis status (running/paused)."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT novel_id, status FROM analysis_tasks WHERE status IN ('running', 'paused')"
        )
        rows = await cursor.fetchall()
        # If multiple tasks per novel, prefer 'running' over 'paused'
        result: dict[str, str] = {}
        for novel_id, status in rows:
            if novel_id not in result or status == "running":
                result[novel_id] = status
        return {"items": [{"novel_id": k, "status": v} for k, v in result.items()]}
    finally:
        await conn.close()


@router.post("/novels/{novel_id}/analyze")
async def start_analysis(novel_id: str, req: AnalyzeRequest | None = None):
    """Trigger full or range analysis for a novel."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    chapter_start = 1
    chapter_end = novel["total_chapters"]
    if req:
        if req.chapter_start is not None:
            chapter_start = req.chapter_start
        if req.chapter_end is not None:
            chapter_end = req.chapter_end

    if chapter_start < 1 or chapter_end > novel["total_chapters"] or chapter_start > chapter_end:
        raise HTTPException(status_code=400, detail="无效的章节范围")

    force = req.force if req else False

    service = get_analysis_service()
    try:
        task_id = await service.start(novel_id, chapter_start, chapter_end, force=force)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {"task_id": task_id, "status": "running"}


@router.patch("/analysis/{task_id}")
async def patch_task(task_id: str, req: PatchTaskRequest):
    """Pause, resume, or cancel an analysis task."""
    task = await analysis_task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    service = get_analysis_service()
    try:
        if req.status == "paused":
            await service.pause(task_id)
        elif req.status == "running":
            await service.resume(task_id)
        elif req.status == "cancelled":
            await service.cancel(task_id)
        else:
            raise HTTPException(status_code=400, detail=f"无效的状态: {req.status}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"task_id": task_id, "status": req.status}


@router.get("/analysis/{task_id}")
async def get_task(task_id: str):
    """Query task status and progress."""
    task = await analysis_task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/novels/{novel_id}/analysis/latest")
async def get_latest_task(novel_id: str):
    """Get the most recent analysis task for a novel, with cumulative stats."""
    task = await analysis_task_store.get_latest_task(novel_id)
    if not task:
        return {"task": None, "stats": None}

    # Compute cumulative stats from existing chapter facts
    stats = {"entities": 0, "relations": 0, "events": 0}
    if task["status"] in ("running", "paused", "completed"):
        all_facts = await chapter_fact_store.get_all_chapter_facts(novel_id)
        ch_start = task.get("chapter_start", 1)
        ch_end = task.get("chapter_end", 999999)
        for ef in all_facts:
            ch_id = ef.get("chapter_id", 0)
            if ch_start <= ch_id <= ch_end:
                fact = ef.get("fact", {})
                stats["entities"] += len(fact.get("characters", [])) + len(fact.get("locations", []))
                stats["relations"] += len(fact.get("relationships", []))
                stats["events"] += len(fact.get("events", []))

    return {"task": task, "stats": stats}


@router.delete("/novels/{novel_id}/analysis")
async def clear_analysis_data(novel_id: str):
    """Clear all analysis data for a novel, resetting it to a fresh state."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    # Check no running task
    latest = await analysis_task_store.get_latest_task(novel_id)
    if latest and latest["status"] in ("running", "paused"):
        raise HTTPException(status_code=409, detail="请先取消正在进行的分析任务")

    # Delete chapter_facts
    await chapter_fact_store.delete_chapter_facts(novel_id)

    # Reset chapter analysis_status
    conn = await get_connection()
    try:
        await conn.execute(
            "UPDATE chapters SET analysis_status = 'pending', analyzed_at = NULL "
            "WHERE novel_id = ?",
            (novel_id,),
        )
        # Delete analysis tasks
        await conn.execute(
            "DELETE FROM analysis_tasks WHERE novel_id = ?",
            (novel_id,),
        )
        # Delete world structure + overrides
        await conn.execute(
            "DELETE FROM world_structures WHERE novel_id = ?",
            (novel_id,),
        )
        await conn.execute(
            "DELETE FROM world_structure_overrides WHERE novel_id = ?",
            (novel_id,),
        )
        # Delete layout caches
        await conn.execute(
            "DELETE FROM map_layouts WHERE novel_id = ?",
            (novel_id,),
        )
        await conn.execute(
            "DELETE FROM layer_layouts WHERE novel_id = ?",
            (novel_id,),
        )
        await conn.execute(
            "DELETE FROM map_user_overrides WHERE novel_id = ?",
            (novel_id,),
        )
        await conn.commit()
    finally:
        await conn.close()

    return {"ok": True, "message": "分析数据已清除"}
