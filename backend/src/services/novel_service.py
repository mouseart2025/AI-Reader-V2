"""Business logic for novel upload, preview, and import."""

import asyncio
import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from src.api.schemas.novels import (
    ChapterPreviewItem,
    SplitDiagnosis,
    UploadPreviewResponse,
)
from src.db import novel_store
from src.utils.chapter_classifier import classify_chapters
from src.utils.chapter_splitter import AVAILABLE_MODES, ChapterInfo, SplitResult, split_chapters, split_chapters_ex
from src.utils.text_processor import decode_text

# In-memory cache for upload previews (file_hash -> cached data)
# TTL: 30 minutes
_CACHE_TTL = 30 * 60  # seconds


@dataclass
class _CachedUpload:
    preview: UploadPreviewResponse
    chapters: list[ChapterInfo]
    raw_text: str
    created_at: float


_upload_cache: dict[str, _CachedUpload] = {}

_MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
_LARGE_CHAPTER_WORDS = 50_000


def _evict_expired() -> None:
    """Remove expired entries from the upload cache."""
    now = time.time()
    expired = [k for k, v in _upload_cache.items() if now - v.created_at > _CACHE_TTL]
    for k in expired:
        del _upload_cache[k]


def _compute_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _extract_author(text: str) -> str | None:
    """Try to extract author from text content (e.g. '作者：XXX' or '作者:XXX')."""
    match = re.search(r"作者[：:][\s]*(.+)", text[:5000])  # Only search early in the text
    if match:
        author = match.group(1).strip()
        # Clean up — take first line only, limit length
        author = author.split("\n")[0].strip()
        if len(author) > 50:
            author = author[:50]
        return author if author else None
    return None


def _title_from_filename(filename: str) -> str:
    """Derive novel title from filename by removing extension."""
    return Path(filename).stem


def _compute_diagnosis(split_result: SplitResult, total_words: int) -> SplitDiagnosis:
    """Compute a structured diagnosis from the split result."""
    chapters = split_result.chapters
    chapter_count = len(chapters)
    max_words = max((ch.word_count for ch in chapters), default=0)
    avg_words = total_words // chapter_count if chapter_count > 0 else 0

    # Fallback used (heuristic or fixed_size as auto-fallback)
    if split_result.is_fallback:
        if split_result.matched_mode == "fixed_size":
            return SplitDiagnosis(
                tag="NO_HEADING_MATCH",
                message="未检测到标准章节标题，已按段落/字数自动切分",
                suggestion="您也可以尝试自定义正则表达式",
            )
        if split_result.matched_mode == "heuristic_title":
            return SplitDiagnosis(
                tag="FALLBACK_USED",
                message="未检测到标准章节格式，已通过启发式标题检测自动切分",
                suggestion="如果切分结果不理想，可以尝试其他模式或自定义正则",
            )

    # Single huge chapter
    if chapter_count == 1 and max_words > 30_000:
        return SplitDiagnosis(
            tag="SINGLE_HUGE_CHAPTER",
            message=f"仅检测到 1 个章节（{max_words} 字），可能章节切分未生效",
            suggestion="建议使用「按字数切分」模式",
        )

    # Headings too sparse
    if chapter_count < 5 and total_words > 100_000:
        return SplitDiagnosis(
            tag="HEADING_TOO_SPARSE",
            message=f"检测到的章节较少（{chapter_count} 章 / {total_words} 字），可能遗漏了部分章节标题",
            suggestion="可以尝试切换切分模式或使用自定义正则",
        )

    # Headings too dense
    if avg_words < 500 and chapter_count > 10:
        return SplitDiagnosis(
            tag="HEADING_TOO_DENSE",
            message=f"章节过多过短（{chapter_count} 章，章均 {avg_words} 字），可能误将正文识别为标题",
            suggestion="建议切换到其他切分模式",
        )

    return SplitDiagnosis(tag="OK", message="章节切分正常")


def _build_preview(
    title: str,
    author: str | None,
    file_hash: str,
    split_result: SplitResult,
    duplicate_novel_id: str | None,
    extra_warnings: list[str] | None = None,
) -> UploadPreviewResponse:
    """Build an UploadPreviewResponse from a split result."""
    chapters = split_result.chapters
    total_chapters = len(chapters)
    total_words = sum(ch.word_count for ch in chapters)

    # Warnings
    warnings: list[str] = list(extra_warnings) if extra_warnings else []
    if total_chapters == 1:
        warnings.append("仅检测到 1 个章节，可能章节切分未生效")
    for ch in chapters:
        if ch.word_count > _LARGE_CHAPTER_WORDS:
            warnings.append(f"章节 '{ch.title}' 字数为 {ch.word_count}，超过 5 万字")

    # Classify chapters for non-content detection
    suspects = classify_chapters(chapters)

    # Build chapter previews (with first ~100 chars of content)
    chapter_previews = [
        ChapterPreviewItem(
            chapter_num=ch.chapter_num,
            title=ch.title,
            word_count=ch.word_count,
            is_suspect=suspects[i],
            content_preview=ch.content[:100].replace("\n", " ").strip(),
        )
        for i, ch in enumerate(chapters)
    ]

    suspect_count = sum(suspects)
    if suspect_count > 0:
        warnings.append(f"检测到 {suspect_count} 个疑似非正文章节（建议排除）")

    # Compute diagnosis
    diagnosis = _compute_diagnosis(split_result, total_words)

    return UploadPreviewResponse(
        title=title,
        author=author,
        file_hash=file_hash,
        total_chapters=total_chapters,
        total_words=total_words,
        chapters=chapter_previews,
        warnings=warnings,
        duplicate_novel_id=duplicate_novel_id,
        diagnosis=diagnosis,
        matched_mode=split_result.matched_mode,
    )


async def parse_upload(filename: str, content: bytes) -> UploadPreviewResponse:
    """Parse an uploaded file and return a chapter-split preview.

    The full chapter data is cached server-side for later confirm_import.
    """
    _evict_expired()

    extra_warnings: list[str] = []

    # File size warning
    if len(content) > _MAX_FILE_SIZE:
        extra_warnings.append(f"文件大小 ({len(content) / 1024 / 1024:.1f}MB) 超过 100MB，处理可能较慢")

    # SHA256 hash
    file_hash = _compute_hash(content)

    # Decode text
    text = decode_text(content)

    # Extract metadata
    title = _title_from_filename(filename)
    author = _extract_author(text)

    # Split chapters (with extended info)
    split_result = split_chapters_ex(text)

    # Detect text hygiene issues
    hygiene_report = None
    try:
        from src.utils.text_sanitizer import detect_noise
        from src.api.schemas.novels import HygieneReport, SuspectLine

        noise = detect_noise(text, split_result.chapters)
        if noise.total_suspect_lines > 0:
            hygiene_report = HygieneReport(
                total_suspect_lines=noise.total_suspect_lines,
                by_category=noise.by_category,
                samples=[
                    SuspectLine(
                        line_num=s.line_num,
                        content=s.content,
                        category=s.category,
                        confidence=s.confidence,
                    )
                    for s in noise.samples
                ],
            )
    except Exception:
        pass  # Hygiene detection is optional

    # Check for duplicate
    existing = await novel_store.find_by_hash(file_hash)
    duplicate_novel_id = existing["id"] if existing else None

    # Build preview
    preview = _build_preview(
        title=title,
        author=author,
        file_hash=file_hash,
        split_result=split_result,
        duplicate_novel_id=duplicate_novel_id,
        extra_warnings=extra_warnings,
    )
    preview.hygiene_report = hygiene_report

    # Cache for confirm step (including raw text for re-split)
    _upload_cache[file_hash] = _CachedUpload(
        preview=preview,
        chapters=split_result.chapters,
        raw_text=text,
        created_at=time.time(),
    )

    return preview


async def confirm_import(
    file_hash: str,
    title: str,
    author: str | None,
    excluded_chapters: list[int] | None = None,
) -> dict:
    """Confirm import of a previously uploaded file.

    Retrieves cached chapter data by file_hash and writes to DB.
    Chapters whose chapter_num is in *excluded_chapters* are marked
    as excluded (is_excluded=1) in the DB.
    Returns the created novel record.
    """
    _evict_expired()

    cached = _upload_cache.get(file_hash)
    if not cached:
        raise ValueError("上传数据已过期或不存在，请重新上传文件")

    novel_id = str(uuid.uuid4())

    chapters = cached.chapters
    total_chapters = len(chapters)
    total_words = sum(ch.word_count for ch in chapters)

    excluded_set = set(excluded_chapters) if excluded_chapters else None

    # Persist to DB
    await novel_store.insert_novel(
        novel_id=novel_id,
        title=title,
        author=author,
        file_hash=file_hash,
        total_chapters=total_chapters,
        total_words=total_words,
    )
    await novel_store.insert_chapters(novel_id, chapters, excluded_nums=excluded_set)

    # Remove from cache after successful import
    del _upload_cache[file_hash]

    # Trigger pre-scan in background (non-blocking)
    async def _run_prescan() -> None:
        try:
            from src.extraction.entity_pre_scanner import EntityPreScanner
            scanner = EntityPreScanner()
            await scanner.scan(novel_id)
        except Exception as e:
            logging.getLogger(__name__).warning("预扫描后台任务失败: %s", e)

    asyncio.create_task(_run_prescan())

    # Return the created novel
    novel = await novel_store.get_novel(novel_id)
    return novel


def get_available_modes() -> list[str]:
    """Return available split mode names."""
    return AVAILABLE_MODES


async def re_split(
    file_hash: str,
    mode: str | None = None,
    custom_regex: str | None = None,
) -> UploadPreviewResponse:
    """Re-split a previously uploaded file using a different mode.

    Retrieves cached raw text by file_hash, re-runs chapter splitting,
    and updates the cached preview and chapters.
    """
    _evict_expired()

    cached = _upload_cache.get(file_hash)
    if not cached:
        raise ValueError("上传数据已过期或不存在，请重新上传文件")

    text = cached.raw_text

    # Re-split with specified mode (extended)
    split_result = split_chapters_ex(text, mode=mode, custom_regex=custom_regex)

    # Build updated preview
    preview = _build_preview(
        title=cached.preview.title,
        author=cached.preview.author,
        file_hash=file_hash,
        split_result=split_result,
        duplicate_novel_id=cached.preview.duplicate_novel_id,
    )

    # Carry over hygiene_report from previous preview if it exists
    if cached.preview.hygiene_report:
        preview.hygiene_report = cached.preview.hygiene_report

    # Update cache
    cached.preview = preview
    cached.chapters = split_result.chapters

    return preview


async def clean_and_resplit(
    file_hash: str,
    clean_mode: str = "conservative",
) -> UploadPreviewResponse:
    """Clean text and re-split.

    Uses the text_sanitizer to clean noise, then re-splits.
    """
    _evict_expired()

    cached = _upload_cache.get(file_hash)
    if not cached:
        raise ValueError("上传数据已过期或不存在，请重新上传文件")

    from src.utils.text_sanitizer import clean_text, detect_noise

    text = cached.raw_text

    # Detect noise
    report = detect_noise(text)

    # Clean
    cleaned_text = clean_text(text, report, mode=clean_mode)

    # Re-split cleaned text
    split_result = split_chapters_ex(cleaned_text)

    # Build preview with updated hygiene report
    from src.api.schemas.novels import HygieneReport, SuspectLine

    hygiene_report = HygieneReport(
        total_suspect_lines=report.total_suspect_lines,
        by_category=report.by_category,
        samples=[
            SuspectLine(
                line_num=s.line_num,
                content=s.content,
                category=s.category,
                confidence=s.confidence,
            )
            for s in report.samples
        ],
    )

    preview = _build_preview(
        title=cached.preview.title,
        author=cached.preview.author,
        file_hash=file_hash,
        split_result=split_result,
        duplicate_novel_id=cached.preview.duplicate_novel_id,
    )
    preview.hygiene_report = hygiene_report

    # Update cache with cleaned text
    cached.preview = preview
    cached.chapters = split_result.chapters
    cached.raw_text = cleaned_text

    return preview
