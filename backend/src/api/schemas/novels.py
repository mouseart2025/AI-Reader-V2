"""Pydantic request/response schemas for novel endpoints."""

from pydantic import BaseModel


class ChapterPreviewItem(BaseModel):
    chapter_num: int
    title: str
    word_count: int
    is_suspect: bool = False


class UploadPreviewResponse(BaseModel):
    title: str
    author: str | None
    file_hash: str
    total_chapters: int
    total_words: int
    chapters: list[ChapterPreviewItem]
    warnings: list[str]
    duplicate_novel_id: str | None


class ConfirmImportRequest(BaseModel):
    file_hash: str
    title: str
    author: str | None = None
    excluded_chapters: list[int] = []


class ReSplitRequest(BaseModel):
    file_hash: str
    mode: str | None = None
    custom_regex: str | None = None


class NovelResponse(BaseModel):
    id: str
    title: str
    author: str | None
    total_chapters: int
    total_words: int
    created_at: str
    updated_at: str


class NovelListItem(BaseModel):
    id: str
    title: str
    author: str | None
    total_chapters: int
    total_words: int
    created_at: str
    updated_at: str
    analysis_progress: float = 0.0
    reading_progress: float = 0.0
    last_opened: str | None = None
