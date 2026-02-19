"""Analysis task management endpoints."""

import csv
import io

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
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


@router.get("/novels/{novel_id}/analyze/estimate")
async def estimate_analysis_cost(
    novel_id: str,
    chapter_start: int | None = None,
    chapter_end: int | None = None,
):
    """Return cost estimate for cloud LLM analysis."""
    from src.infra.config import LLM_PROVIDER
    from src.services.cost_service import estimate_analysis_cost as calc_estimate

    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    start = chapter_start or 1
    end = chapter_end or novel["total_chapters"]

    if start < 1 or end > novel["total_chapters"] or start > end:
        raise HTTPException(status_code=400, detail="无效的章节范围")

    chapter_count = end - start + 1

    # Estimate total words for the range (proportional to full novel)
    proportion = chapter_count / max(novel["total_chapters"], 1)
    range_words = int(novel["total_words"] * proportion)

    # Check if prescan is already done
    prescan_done = False
    conn = await get_connection()
    try:
        row = await conn.execute(
            "SELECT status FROM entity_dictionary WHERE novel_id = ? LIMIT 1",
            (novel_id,),
        )
        result = await row.fetchone()
        prescan_done = result is not None
    finally:
        await conn.close()

    estimate = calc_estimate(
        chapter_count=chapter_count,
        total_words=range_words,
        include_prescan=not prescan_done,
    )

    # Include budget info for cloud mode
    monthly_budget_cny = 0.0
    monthly_used_cny = 0.0
    if LLM_PROVIDER == "openai":
        from src.services.cost_service import get_monthly_budget, get_monthly_usage
        monthly_budget_cny = await get_monthly_budget()
        usage = await get_monthly_usage()
        monthly_used_cny = usage.get("cny", 0.0)

    return {
        "is_cloud": LLM_PROVIDER == "openai",
        "novel_title": novel["title"],
        "chapter_range": [start, end],
        "chapter_count": chapter_count,
        "total_words": range_words,
        "provider": estimate.provider,
        "model": estimate.model,
        "estimated_input_tokens": estimate.estimated_input_tokens,
        "estimated_output_tokens": estimate.estimated_output_tokens,
        "estimated_total_tokens": estimate.estimated_total_tokens,
        "estimated_cost_usd": estimate.estimated_cost_usd,
        "estimated_cost_cny": estimate.estimated_cost_cny,
        "includes_prescan": estimate.includes_prescan,
        "input_price_per_1m": estimate.input_price_per_1m,
        "output_price_per_1m": estimate.output_price_per_1m,
        "monthly_budget_cny": monthly_budget_cny,
        "monthly_used_cny": monthly_used_cny,
    }


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


@router.get("/novels/{novel_id}/analysis/cost-detail")
async def get_cost_detail(novel_id: str):
    """Return per-chapter cost breakdown with summary for a novel."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    all_facts = await chapter_fact_store.get_all_chapter_facts(novel_id)

    # Get latest task for time range and model info
    task = await analysis_task_store.get_latest_task(novel_id)

    chapters = []
    total_input = 0
    total_output = 0
    total_cost_usd = 0.0
    total_cost_cny = 0.0
    total_entities = 0
    model_used = ""

    for ef in all_facts:
        fact = ef.get("fact", {})
        entity_count = (
            len(fact.get("characters", []))
            + len(fact.get("locations", []))
            + len(fact.get("item_events", []))
            + len(fact.get("org_events", []))
        )
        inp = ef.get("input_tokens", 0)
        out = ef.get("output_tokens", 0)
        c_usd = ef.get("cost_usd", 0.0)
        c_cny = ef.get("cost_cny", 0.0)

        total_input += inp
        total_output += out
        total_cost_usd += c_usd
        total_cost_cny += c_cny
        total_entities += entity_count

        if not model_used and ef.get("llm_model"):
            model_used = ef["llm_model"]

        chapters.append({
            "chapter_id": ef["chapter_id"],
            "input_tokens": inp,
            "output_tokens": out,
            "cost_usd": round(c_usd, 6),
            "cost_cny": round(c_cny, 4),
            "entity_count": entity_count,
            "extraction_ms": ef.get("extraction_ms", 0),
            "extracted_at": ef.get("extracted_at"),
            "llm_model": ef.get("llm_model", ""),
        })

    return {
        "novel_id": novel_id,
        "novel_title": novel["title"],
        "chapters": chapters,
        "summary": {
            "total_chapters": len(chapters),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cost_usd": round(total_cost_usd, 4),
            "total_cost_cny": round(total_cost_cny, 2),
            "total_entities": total_entities,
        },
        "model": model_used,
        "started_at": task["created_at"] if task else None,
        "completed_at": task["updated_at"] if task else None,
    }


@router.get("/novels/{novel_id}/analysis/cost-detail/csv")
async def export_cost_csv(novel_id: str):
    """Export per-chapter cost breakdown as CSV."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    all_facts = await chapter_fact_store.get_all_chapter_facts(novel_id)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "章节", "输入Token", "输出Token",
        "费用(USD)", "费用(CNY)", "实体数", "耗时(ms)", "模型", "分析时间",
    ])

    total_inp = 0
    total_out = 0
    total_usd = 0.0
    total_cny = 0.0
    total_ent = 0

    for ef in all_facts:
        fact = ef.get("fact", {})
        ent = (
            len(fact.get("characters", []))
            + len(fact.get("locations", []))
            + len(fact.get("item_events", []))
            + len(fact.get("org_events", []))
        )
        inp = ef.get("input_tokens", 0)
        out = ef.get("output_tokens", 0)
        c_usd = ef.get("cost_usd", 0.0)
        c_cny = ef.get("cost_cny", 0.0)

        total_inp += inp
        total_out += out
        total_usd += c_usd
        total_cny += c_cny
        total_ent += ent

        writer.writerow([
            ef["chapter_id"],
            inp,
            out,
            round(c_usd, 6),
            round(c_cny, 4),
            ent,
            ef.get("extraction_ms", 0),
            ef.get("llm_model", ""),
            ef.get("extracted_at", ""),
        ])

    # Summary row
    writer.writerow([
        "合计",
        total_inp,
        total_out,
        round(total_usd, 4),
        round(total_cny, 2),
        total_ent,
        "",
        "",
        "",
    ])

    buf.seek(0)
    filename = f"{novel['title']}_成本明细.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.get("/settings/analysis-records")
async def get_analysis_records():
    """List all completed analysis tasks with summary cost info."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT t.id, t.novel_id, t.status, t.chapter_start, t.chapter_end,
                   t.created_at, t.updated_at, n.title as novel_title
            FROM analysis_tasks t
            LEFT JOIN novels n ON t.novel_id = n.id
            WHERE t.status IN ('completed', 'cancelled')
            ORDER BY t.created_at DESC
            LIMIT 50
            """
        )
        rows = await cursor.fetchall()

        records = []
        for row in rows:
            # Get cost summary from chapter_facts for this task's range
            fact_cursor = await conn.execute(
                """
                SELECT COALESCE(SUM(input_tokens), 0) as total_input,
                       COALESCE(SUM(output_tokens), 0) as total_output,
                       COALESCE(SUM(cost_usd), 0) as total_usd,
                       COALESCE(SUM(cost_cny), 0) as total_cny,
                       COUNT(*) as chapter_count
                FROM chapter_facts
                WHERE novel_id = ?
                  AND chapter_id >= ? AND chapter_id <= ?
                """,
                (row["novel_id"], row["chapter_start"], row["chapter_end"]),
            )
            cost_row = await fact_cursor.fetchone()

            records.append({
                "task_id": row["id"],
                "novel_id": row["novel_id"],
                "novel_title": row["novel_title"] or "",
                "status": row["status"],
                "chapter_range": [row["chapter_start"], row["chapter_end"]],
                "chapter_count": cost_row["chapter_count"] if cost_row else 0,
                "total_input_tokens": cost_row["total_input"] if cost_row else 0,
                "total_output_tokens": cost_row["total_output"] if cost_row else 0,
                "total_cost_usd": round(cost_row["total_usd"], 4) if cost_row else 0,
                "total_cost_cny": round(cost_row["total_cny"], 2) if cost_row else 0,
                "started_at": row["created_at"],
                "completed_at": row["updated_at"],
            })

        return {"records": records}
    finally:
        await conn.close()
