"""Export / import novel data endpoints."""

import gzip
import json
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response

from src.db import novel_store
from src.services import export_service

router = APIRouter(prefix="/api", tags=["export-import"])


def _decode_import_content(raw: bytes) -> dict:
    """Decode import file content — auto-detects gzip (.air) vs plain JSON."""
    # Detect gzip via magic bytes (1f 8b)
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            raise HTTPException(400, "无法解压 .air 文件")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "无效的 JSON 文件")


@router.get("/novels/{novel_id}/export")
async def export_novel(novel_id: str, format: str = Query("json")):
    """Export a novel with all analysis data.

    format=json (default): plain JSON response
    format=air: gzip-compressed JSON with .air extension
    """
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    try:
        data = await export_service.export_novel(novel_id)

        if format == "air":
            json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
            compressed = gzip.compress(json_bytes, compresslevel=6)
            filename = f'{novel["title"]}.air'
            encoded = quote(filename)
            return Response(
                content=compressed,
                media_type="application/x-air+gzip",
                headers={
                    "Content-Disposition": f"attachment; filename=\"export.air\"; filename*=UTF-8''{encoded}",
                },
            )

        # Default: plain JSON
        filename = f'{novel["title"]}_export.json'
        encoded = quote(filename)
        return JSONResponse(
            content=data,
            headers={
                "Content-Disposition": f"attachment; filename=\"export.json\"; filename*=UTF-8''{encoded}",
            },
        )
    except Exception as e:
        raise HTTPException(500, f"Export failed: {e}")


@router.post("/novels/import/preview")
async def preview_import(file: UploadFile):
    """Upload an export file (.air or .json) and return a preview of what will be imported."""
    content = await file.read()
    data = _decode_import_content(content)

    try:
        preview = export_service.preview_import(data)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Enrich with LLM model info from chapter_facts
    facts = data.get("chapter_facts", [])
    if facts:
        models = set(f.get("llm_model") for f in facts if f.get("llm_model"))
        preview["llm_models"] = sorted(models) if models else []
    else:
        preview["llm_models"] = []

    # Check if a novel with the same title or file_hash exists
    from src.db.sqlite_db import get_connection

    novel_data = data.get("novel", {})
    file_hash = novel_data.get("file_hash")

    conn = await get_connection()
    try:
        if file_hash:
            cur = await conn.execute(
                "SELECT id, title FROM novels WHERE title = ? OR file_hash = ?",
                (novel_data.get("title"), file_hash),
            )
        else:
            cur = await conn.execute(
                "SELECT id, title FROM novels WHERE title = ?",
                (novel_data.get("title"),),
            )
        existing = await cur.fetchone()
        preview["existing_novel_id"] = existing["id"] if existing else None
    finally:
        await conn.close()

    return preview


@router.post("/novels/import/confirm")
async def confirm_import(file: UploadFile, overwrite: bool = False):
    """Import a novel from an exported file (.air or .json)."""
    content = await file.read()
    data = _decode_import_content(content)

    try:
        result = await export_service.import_novel(data, overwrite=overwrite)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Import failed: {e}")

    return result
