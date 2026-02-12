"""Export / import novel data endpoints."""

import json
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from src.db import novel_store
from src.services import export_service

router = APIRouter(prefix="/api", tags=["export-import"])


@router.get("/novels/{novel_id}/export")
async def export_novel(novel_id: str):
    """Export a novel with all analysis data as JSON."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    try:
        data = await export_service.export_novel(novel_id)
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
    """Upload an export file and return a preview of what will be imported."""
    content = await file.read()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON file")

    try:
        preview = export_service.preview_import(data)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Check if a novel with the same title exists
    from src.db.sqlite_db import get_connection

    conn = await get_connection()
    try:
        cur = await conn.execute(
            "SELECT id, title FROM novels WHERE title = ?",
            (data.get("novel", {}).get("title"),),
        )
        existing = await cur.fetchone()
        preview["existing_novel_id"] = existing["id"] if existing else None
    finally:
        await conn.close()

    return preview


@router.post("/novels/import/confirm")
async def confirm_import(file: UploadFile, overwrite: bool = False):
    """Import a novel from an exported JSON file."""
    content = await file.read()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON file")

    try:
        result = await export_service.import_novel(data, overwrite=overwrite)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Import failed: {e}")

    return result
