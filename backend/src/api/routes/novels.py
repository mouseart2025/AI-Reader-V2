"""Novel management endpoints: upload, confirm, list, delete."""

from fastapi import APIRouter, HTTPException, UploadFile

from src.api.schemas.novels import (
    CleanAndReSplitRequest,
    ConfirmImportRequest,
    NovelListItem,
    NovelResponse,
    ReSplitRequest,
    UploadPreviewResponse,
)
from src.db import novel_store
from src.services import novel_service

router = APIRouter(prefix="/api/novels", tags=["novels"])

_ALLOWED_EXTENSIONS = {".txt", ".md"}
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100MB


@router.get("")
async def list_novels():
    """Return list of all imported novels with progress info."""
    rows = await novel_store.list_novels()
    novels = [NovelListItem(**row) for row in rows]
    return {"novels": novels}


@router.post("/upload", response_model=UploadPreviewResponse)
async def upload_novel(file: UploadFile):
    """Upload a .txt/.md file and return chapter-split preview."""
    # Validate extension
    filename = file.filename or "unknown.txt"
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式 '{suffix}'，仅支持 .txt 和 .md",
        )

    # Read content
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件大小超过 100MB 限制")

    preview = await novel_service.parse_upload(filename, content)
    return preview


@router.get("/split-modes")
async def get_split_modes():
    """Return available chapter splitting modes."""
    return {"modes": novel_service.get_available_modes()}


@router.post("/re-split", response_model=UploadPreviewResponse)
async def re_split_chapters(req: ReSplitRequest):
    """Re-split a previously uploaded file using a different mode."""
    try:
        preview = await novel_service.re_split(
            file_hash=req.file_hash,
            mode=req.mode,
            custom_regex=req.custom_regex,
            split_points=req.split_points,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return preview


@router.get("/raw-text/{file_hash}")
async def get_raw_text(file_hash: str):
    """Return cached raw text for preview panel (available within 30-min upload window)."""
    text = novel_service.get_cached_raw_text(file_hash)
    if text is None:
        raise HTTPException(status_code=404, detail="上传数据已过期，请重新上传文件")
    return {"text": text}


@router.post("/infer-pattern")
async def infer_pattern(req: ReSplitRequest):
    """Infer a regex pattern from user-marked split points, then re-split using it."""
    if not req.split_points or len(req.split_points) < 2:
        raise HTTPException(status_code=400, detail="至少需要标记 2 个分割点才能推断模式")
    try:
        result = await novel_service.infer_and_resplit(
            file_hash=req.file_hash,
            split_points=req.split_points,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/clean-and-resplit", response_model=UploadPreviewResponse)
async def clean_and_resplit(req: CleanAndReSplitRequest):
    """Clean text noise and re-split chapters."""
    try:
        preview = await novel_service.clean_and_resplit(
            file_hash=req.file_hash,
            clean_mode=req.clean_mode,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return preview


@router.post("/confirm", response_model=NovelResponse)
async def confirm_import(req: ConfirmImportRequest):
    """Confirm import of a previously uploaded file."""
    try:
        novel = await novel_service.confirm_import(
            file_hash=req.file_hash,
            title=req.title,
            author=req.author,
            excluded_chapters=req.excluded_chapters or None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return novel


@router.get("/{novel_id}", response_model=NovelResponse)
async def get_novel(novel_id: str):
    """Return a single novel by ID."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    return novel


@router.get("/{novel_id}/synopsis")
async def get_synopsis(novel_id: str):
    """Get the novel's synopsis."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    return {"synopsis": novel.get("synopsis")}


@router.put("/{novel_id}/synopsis")
async def update_synopsis(novel_id: str, body: dict):
    """Update or set the novel's synopsis (user edit or LLM generation)."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    synopsis = body.get("synopsis", "").strip() or None
    from src.db.sqlite_db import get_connection

    conn = await get_connection()
    try:
        await conn.execute(
            "UPDATE novels SET synopsis = ?, updated_at = datetime('now') WHERE id = ?",
            (synopsis, novel_id),
        )
        await conn.commit()
    finally:
        await conn.close()
    return {"synopsis": synopsis}


@router.post("/{novel_id}/synopsis/generate")
async def generate_synopsis(novel_id: str):
    """Trigger LLM synopsis generation for a novel."""
    import json as _json
    import logging

    logger = logging.getLogger(__name__)

    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    from src.db.sqlite_db import get_connection
    from src.extraction.synopsis_generator import SynopsisGenerator

    conn = await get_connection()
    try:
        # Gather high-importance events
        cur = await conn.execute(
            """SELECT cf.fact_json FROM chapter_facts cf
               WHERE cf.novel_id = ? ORDER BY cf.chapter_id""",
            (novel_id,),
        )
        rows = await cur.fetchall()

        events = []
        characters = set()
        locations = set()
        for row in rows:
            try:
                fact = (
                    row["fact_json"]
                    if isinstance(row["fact_json"], dict)
                    else _json.loads(row["fact_json"])
                )
            except Exception:
                continue
            for evt in fact.get("events", []):
                if evt.get("importance") in ("high", "medium"):
                    events.append(evt.get("summary", ""))
            for ch in fact.get("characters", []):
                name = ch.get("name", "")
                if name:
                    characters.add(name)
            for loc in fact.get("locations", []):
                name = loc.get("name", "")
                if name:
                    locations.add(name)

        logger.info(
            "Synopsis generate: %d facts, %d events, %d characters, %d locations",
            len(rows), len(events), len(characters), len(locations),
        )

        if not events and not characters:
            raise HTTPException(status_code=400, detail="没有可用的分析数据，请先完成章节分析")

        generator = SynopsisGenerator()
        synopsis = await generator.generate(
            title=novel["title"],
            author=novel.get("author"),
            high_importance_events=events,
            main_characters=sorted(characters)[:20],
            main_locations=sorted(locations)[:15],
        )

        if synopsis:
            await conn.execute(
                "UPDATE novels SET synopsis = ?, updated_at = datetime('now') WHERE id = ?",
                (synopsis, novel_id),
            )
            await conn.commit()
        else:
            raise HTTPException(status_code=502, detail="LLM 生成失败，请检查模型配置")

        return {"synopsis": synopsis}
    finally:
        await conn.close()


@router.get("/{novel_id}/stats")
async def get_novel_stats(novel_id: str):
    """Return aggregated analysis statistics for a novel (for overview card)."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    from src.db.sqlite_db import get_connection
    from src.services import entity_aggregator

    conn = await get_connection()
    try:
        # Chapter stats
        cur = await conn.execute(
            """SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN analysis_status = 'completed' THEN 1 ELSE 0 END) AS analyzed,
                SUM(CASE WHEN is_excluded = 1 THEN 1 ELSE 0 END) AS excluded
            FROM chapters WHERE novel_id = ?""",
            (novel_id,),
        )
        ch_row = await cur.fetchone()

        # Entity counts from chapter_facts aggregation (not entity_dictionary)
        entities = await entity_aggregator.get_all_entities(novel_id)
        counts = {"person": 0, "location": 0, "item": 0, "org": 0, "concept": 0}
        for e in entities:
            if e.type in counts:
                counts[e.type] += 1

        # LLM model and extraction time
        cur = await conn.execute(
            """SELECT
                GROUP_CONCAT(DISTINCT cf.llm_model) AS llm_models,
                SUM(cf.extraction_ms) AS total_extraction_ms
            FROM chapter_facts cf WHERE cf.novel_id = ?""",
            (novel_id,),
        )
        fact_row = await cur.fetchone()

        return {
            "novel_id": novel_id,
            "chapters": {
                "total": ch_row["total"] if ch_row else 0,
                "analyzed": ch_row["analyzed"] if ch_row else 0,
                "excluded": ch_row["excluded"] if ch_row else 0,
            },
            "entities": {
                **counts,
                "total": sum(counts.values()),
            },
            "llm_models": (fact_row["llm_models"] or "").split(",") if fact_row and fact_row["llm_models"] else [],
            "total_extraction_ms": fact_row["total_extraction_ms"] if fact_row else 0,
            "synopsis": novel.get("synopsis"),
        }
    finally:
        await conn.close()


@router.delete("/{novel_id}")
async def delete_novel(novel_id: str):
    """Delete a novel and all associated data."""
    deleted = await novel_store.delete_novel(novel_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="小说不存在")
    return {"ok": True}
