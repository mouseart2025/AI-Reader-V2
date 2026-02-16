"""Pydantic request/response schemas for novel endpoints."""

from pydantic import BaseModel


class ChapterPreviewItem(BaseModel):
    chapter_num: int
    title: str
    word_count: int
    is_suspect: bool = False
    content_preview: str = ""


class SplitDiagnosis(BaseModel):
    """Structured diagnosis of how the chapter split went."""
    tag: str  # OK, NO_HEADING_MATCH, SINGLE_HUGE_CHAPTER, etc.
    message: str  # User-facing Chinese description
    suggestion: str = ""  # Recommended action


class SuspectLine(BaseModel):
    """A single line flagged as noise."""
    line_num: int
    content: str
    category: str  # url, promo, template, decoration, repeated
    confidence: float


class HygieneReport(BaseModel):
    """Report on text noise/hygiene issues."""
    total_suspect_lines: int
    by_category: dict[str, int]
    samples: list[SuspectLine]


class UploadPreviewResponse(BaseModel):
    title: str
    author: str | None
    file_hash: str
    total_chapters: int
    total_words: int
    chapters: list[ChapterPreviewItem]
    warnings: list[str]
    duplicate_novel_id: str | None
    diagnosis: SplitDiagnosis | None = None
    hygiene_report: HygieneReport | None = None
    matched_mode: str | None = None


class ConfirmImportRequest(BaseModel):
    file_hash: str
    title: str
    author: str | None = None
    excluded_chapters: list[int] = []


class ReSplitRequest(BaseModel):
    file_hash: str
    mode: str | None = None
    custom_regex: str | None = None


class CleanAndReSplitRequest(BaseModel):
    file_hash: str
    clean_mode: str = "conservative"  # "conservative" or "aggressive"


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
