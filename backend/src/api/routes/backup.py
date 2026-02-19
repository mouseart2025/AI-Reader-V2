"""Full data backup / restore endpoints."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from src.services import backup_service

router = APIRouter(prefix="/api/backup", tags=["backup"])


@router.get("/export")
async def export_backup():
    """Export all novels and data as a ZIP file."""
    try:
        buf = await backup_service.export_all()
    except Exception as e:
        raise HTTPException(500, f"备份导出失败: {e}")

    date_str = datetime.now().strftime("%Y%m%d")
    filename = f"ai-reader-v2-backup-{date_str}.zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.post("/import/preview")
async def preview_backup(file: UploadFile):
    """Preview backup ZIP contents before importing."""
    data = await file.read()
    try:
        preview = await backup_service.preview_backup_import(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return preview


@router.post("/import/confirm")
async def confirm_backup_import(
    file: UploadFile, conflict_mode: str = "skip"
):
    """Import all novels from a backup ZIP.

    Query params:
        conflict_mode: "skip" (default) or "overwrite"
    """
    if conflict_mode not in ("skip", "overwrite"):
        raise HTTPException(400, "conflict_mode 必须为 skip 或 overwrite")

    data = await file.read()
    try:
        result = await backup_service.import_all(data, conflict_mode=conflict_mode)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"备份导入失败: {e}")
    return result
