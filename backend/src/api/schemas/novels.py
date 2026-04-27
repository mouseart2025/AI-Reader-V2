"""Pydantic request/response schemas for novel endpoints."""

from pydantic import BaseModel

from src.utils.source_language import DEFAULT_SOURCE_LANGUAGE, SourceLanguage


class ChapterPreviewItem(BaseModel):
    chapter_num: int
    title: str
    word_count: int
    start_offset: int = 0  # character offset in raw text where this chapter starts
    is_suspect: bool = False
    content_preview: str = ""


class SplitDiagnosis(BaseModel):
    """Structured diagnosis of how the chapter split went."""
    tag: str  # OK, NO_HEADING_MATCH, SINGLE_HUGE_CHAPTER, HEADING_TOO_SPARSE, HEADING_TOO_DENSE, MODE_MISMATCH
    message: str  # Technical description (Chinese)
    suggestion: str = ""  # Recommended action
    # Sprint C new fields
    auto_optimized: bool = False  # Whether auto-optimization was applied
    original_mode: str | None = None  # Original mode before optimization
    alternatives_tried: list[str] = []  # Modes tried during auto-optimization
    user_message: str = ""  # User-friendly description (preferred over message in frontend)
    technical_detail: str = ""  # Technical details (expandable in frontend)
    detected_genre: str = "unknown"  # Text genre: novel, essay, poetry, short_collection, unknown
    source_language: SourceLanguage = DEFAULT_SOURCE_LANGUAGE


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
    source_language: SourceLanguage = DEFAULT_SOURCE_LANGUAGE


class ConfirmImportRequest(BaseModel):
    file_hash: str
    title: str
    author: str | None = None
    excluded_chapters: list[int] = []
    source_language: SourceLanguage | None = None


class ReSplitRequest(BaseModel):
    file_hash: str
    mode: str | None = None
    custom_regex: str | None = None
    split_points: list[int] | None = None
    source_language: SourceLanguage | None = None


class CleanAndReSplitRequest(BaseModel):
    file_hash: str
    clean_mode: str = "conservative"  # "conservative" or "aggressive"


class NovelResponse(BaseModel):
    id: str
    title: str
    author: str | None
    total_chapters: int
    total_words: int
    is_sample: bool = False
    source_language: SourceLanguage = DEFAULT_SOURCE_LANGUAGE
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
    is_sample: bool = False
    source_language: SourceLanguage = DEFAULT_SOURCE_LANGUAGE
    analysis_progress: float = 0.0
    reading_progress: float = 0.0
    last_opened: str | None = None
