"""Analysis pass endpoints — 独立二审(source pass)的启动/进度/diff/删除
(multi-pass MVP, issue #70 Epic 3 Story 3.2)。

二审产物全部落影子表(analysis_passes / pass_chapter_facts),主表
chapter_facts 零改动;diff 由 PassDiffService 生成并回填 history 埋点
(air_unlocked_at / diff_counts)。人工裁决(Epic 4)只写 history 埋点
(adjudication 计数 + adjudication_log 明细),不改正式分析结果。
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.db import analysis_pass_store, analysis_task_store, chapter_store, novel_store
from src.services.pass_diff_service import get_pass_diff_service
from src.services.source_pass_service import get_source_pass_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/novels/{novel_id}/passes", tags=["analysis-passes"])

# 人工裁决 verdict → history adjudication 计数键(Epic 4 Story 4.2)
# confirmed=采纳一审(维持现状), rejected=采纳二审, neither=两者皆否
_ADJUDICATION_KEYS = {
    "accept_main": "confirmed",
    "accept_pass": "rejected",
    "neither": "neither",
}


class StartPassRequest(BaseModel):
    kind: str = analysis_pass_store.PASS_KIND_SOURCE
    model_override: str | None = None  # D3: 换模型视角增强错误倾向差异
    chapter_start: int | None = None
    chapter_end: int | None = None
    include_dictionary: bool = True  # D1 开关


async def _require_novel(novel_id: str) -> dict:
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    return novel


async def _require_pass(novel_id: str, pass_id: str) -> dict:
    """pass 必须存在且属于本小说。"""
    pass_row = await analysis_pass_store.get_pass(pass_id)
    if not pass_row or pass_row["novel_id"] != novel_id:
        raise HTTPException(status_code=404, detail="二审任务不存在")
    return pass_row


def _pass_cost_summary(pass_row: dict) -> dict:
    """从 history 埋点聚合本 pass 的成本合计 (Story 5.2 成本分账)。

    逐章 usage 在 source_pass_service 落 history_json.chapters[*].usage;
    token 始终记录,费用仅云端模式非零。与一审 cost-detail(读主表
    chapter_facts)物理隔离,互不混入。
    """
    chapters = (pass_row.get("history_json") or {}).get("chapters") or {}
    total_input = 0
    total_output = 0
    total_usd = 0.0
    total_cny = 0.0
    billed = 0
    for entry in chapters.values():
        u = entry.get("usage")
        if not u:
            continue
        billed += 1
        total_input += u.get("input_tokens", 0)
        total_output += u.get("output_tokens", 0)
        total_usd += u.get("cost_usd", 0.0)
        total_cny += u.get("cost_cny", 0.0)
    return {
        "billed_chapters": billed,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cost_usd": round(total_usd, 4),
        "cost_cny": round(total_cny, 2),
    }


@router.post("")
async def start_pass(novel_id: str, body: StartPassRequest | None = None):
    """启动独立二审(source pass)。运行循环作为后台任务执行,进度走 WS。"""
    await _require_novel(novel_id)
    body = body or StartPassRequest()

    if body.kind != analysis_pass_store.PASS_KIND_SOURCE:
        raise HTTPException(
            status_code=400,
            detail=f"暂不支持的二审类型: {body.kind}(当前仅支持 source_pass)",
        )

    # 前置校验:小说已完成一审分析(未排除章节全部跑过一审,且至少一章成功)
    chapters = await chapter_store.list_chapters(novel_id)
    active = [c for c in chapters if not c.get("is_excluded")]
    if not active:
        raise HTTPException(status_code=409, detail="小说没有可分析的章节")
    statuses = {c.get("analysis_status") for c in active}
    if "completed" not in statuses or statuses - {"completed", "failed"}:
        raise HTTPException(
            status_code=409,
            detail="小说尚未完成一审分析,请先完成一审分析后再启动二审",
        )

    if body.chapter_start is not None or body.chapter_end is not None:
        start = body.chapter_start or 1
        end = body.chapter_end or active[-1]["chapter_num"]
        if start < 1 or start > end:
            raise HTTPException(status_code=400, detail="无效的章节范围")

    # 单活互斥(中文错误信息;SourcePassService.start 内有对称检查兜底)
    if await analysis_task_store.get_running_task(novel_id):
        raise HTTPException(
            status_code=409,
            detail="小说有正在进行的一审分析任务,无法启动二审",
        )
    if await analysis_pass_store.get_active_pass(novel_id):
        raise HTTPException(
            status_code=409,
            detail="小说已有进行中的二审,请先完成或取消后再启动",
        )

    service = get_source_pass_service()
    try:
        pass_id = await service.start(
            novel_id,
            chapter_start=body.chapter_start,
            chapter_end=body.chapter_end,
            model_override=body.model_override,
            include_dictionary=body.include_dictionary,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {"pass_id": pass_id, "status": "running"}


@router.get("")
async def list_passes(novel_id: str):
    """本小说的 pass 列表(含 history 埋点 + 成本合计),最新在前。"""
    await _require_novel(novel_id)
    passes = await analysis_pass_store.list_passes(novel_id)
    for p in passes:
        p["cost_summary"] = _pass_cost_summary(p)
    return {"passes": passes}


@router.get("/{pass_id}/diff")
async def get_chapter_diff(novel_id: str, pass_id: str, chapter: int = Query(...)):
    """某章的一审/二审 diff;首次生成时回填 air_unlocked_at + diff_counts。"""
    await _require_novel(novel_id)
    await _require_pass(novel_id, pass_id)
    service = get_pass_diff_service()
    try:
        return await service.get_chapter_diff(pass_id, chapter)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{pass_id}/pause")
async def pause_pass(novel_id: str, pass_id: str):
    """暂停进行中的二审(跑完当前章后停)。"""
    await _require_novel(novel_id)
    pass_row = await _require_pass(novel_id, pass_id)
    if pass_row["status"] != "running":
        raise HTTPException(status_code=409, detail="只有进行中的二审才能暂停")
    service = get_source_pass_service()
    try:
        await service.pause(pass_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"pass_id": pass_id, "status": "paused"}


@router.post("/{pass_id}/resume")
async def resume_pass(novel_id: str, pass_id: str):
    """从 current_chapter + 1 续跑已暂停的二审。"""
    await _require_novel(novel_id)
    pass_row = await _require_pass(novel_id, pass_id)
    if pass_row["status"] != "paused":
        raise HTTPException(status_code=409, detail="只有已暂停的二审才能继续")
    service = get_source_pass_service()
    try:
        await service.resume(pass_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"pass_id": pass_id, "status": "running"}


@router.post("/{pass_id}/cancel")
async def cancel_pass(novel_id: str, pass_id: str):
    """取消进行中/已暂停的二审(影子数据保留,可再删除)。"""
    await _require_novel(novel_id)
    pass_row = await _require_pass(novel_id, pass_id)
    if pass_row["status"] not in ("running", "paused"):
        raise HTTPException(status_code=409, detail="二审已结束,无法取消")
    service = get_source_pass_service()
    try:
        await service.cancel(pass_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"pass_id": pass_id, "status": "cancelled"}


@router.delete("/{pass_id}")
async def delete_pass(novel_id: str, pass_id: str):
    """删除 pass 并级联清影子数据(pass_chapter_facts);主表不受影响。"""
    await _require_novel(novel_id)
    pass_row = await _require_pass(novel_id, pass_id)
    if pass_row["status"] in ("running", "paused"):
        raise HTTPException(
            status_code=409, detail="二审正在进行中,请先取消后再删除",
        )
    await analysis_pass_store.delete_pass(pass_id)
    return {"status": "ok"}


class AdjudicationRequest(BaseModel):
    """人工裁决一条章节 diff(Epic 4 Story 4.2)。

    只写 history 埋点(计数 + 明细),不改正式分析结果;改正式结果走
    实体卡的人工修正工具链。
    """

    chapter: int          # 章节号(chapter_num,与 diff 接口同口径)
    entry_id: str         # 差异条目 id(前端生成: category:collection:key)
    verdict: str          # accept_main / accept_pass / neither


@router.post("/{pass_id}/adjudications")
async def record_adjudication(
    novel_id: str, pass_id: str, body: AdjudicationRequest,
):
    """记录一条人工裁决,落 history 埋点(不改正式分析结果)。"""
    await _require_novel(novel_id)
    pass_row = await _require_pass(novel_id, pass_id)

    count_key = _ADJUDICATION_KEYS.get(body.verdict)
    if count_key is None:
        raise HTTPException(
            status_code=400,
            detail=f"无效的裁决: {body.verdict}"
                   "(仅支持 accept_main / accept_pass / neither)",
        )
    if not (pass_row["chapter_start"] <= body.chapter <= pass_row["chapter_end"]):
        raise HTTPException(
            status_code=400,
            detail=f"第{body.chapter}章不在本次二审范围"
                   f"({pass_row['chapter_start']}-{pass_row['chapter_end']})内",
        )

    chapters_hist = (pass_row.get("history_json") or {}).get("chapters") or {}
    existing = chapters_hist.get(str(body.chapter)) or {}
    counts = {
        "confirmed": 0, "rejected": 0, "neither": 0,
        **(existing.get("adjudication") or {}),
    }
    counts[count_key] += 1
    log = list(existing.get("adjudication_log") or [])
    log.append({
        "entry_id": body.entry_id,
        "verdict": body.verdict,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    await analysis_pass_store.update_chapter_history(pass_id, body.chapter, {
        "adjudication": counts,
        "adjudication_log": log,
    })
    return {"status": "ok", "chapter": body.chapter, "adjudication": counts}
