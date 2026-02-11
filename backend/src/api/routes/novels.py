"""Novel management endpoints: upload, confirm, list, delete."""

from fastapi import APIRouter, HTTPException, UploadFile

from src.api.schemas.novels import (
    ConfirmImportRequest,
    NovelResponse,
    UploadPreviewResponse,
)
from src.db import novel_store
from src.services import novel_service

router = APIRouter(prefix="/api/novels", tags=["novels"])

_ALLOWED_EXTENSIONS = {".txt", ".md"}
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100MB


@router.get("")
async def list_novels():
    """Return list of all imported novels."""
    novels = await novel_store.list_novels()
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


@router.post("/confirm", response_model=NovelResponse)
async def confirm_import(req: ConfirmImportRequest):
    """Confirm import of a previously uploaded file."""
    try:
        novel = await novel_service.confirm_import(
            file_hash=req.file_hash,
            title=req.title,
            author=req.author,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return novel


@router.delete("/{novel_id}")
async def delete_novel(novel_id: str):
    """Delete a novel and all associated data."""
    deleted = await novel_store.delete_novel(novel_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="小说不存在")
    return {"ok": True}
