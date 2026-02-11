"""Business logic for novel upload, preview, and import."""

import hashlib
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from src.api.schemas.novels import ChapterPreviewItem, UploadPreviewResponse
from src.db import novel_store
from src.utils.chapter_splitter import AVAILABLE_MODES, ChapterInfo, split_chapters
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


async def parse_upload(filename: str, content: bytes) -> UploadPreviewResponse:
    """Parse an uploaded file and return a chapter-split preview.

    The full chapter data is cached server-side for later confirm_import.
    """
    _evict_expired()

    warnings: list[str] = []

    # File size warning
    if len(content) > _MAX_FILE_SIZE:
        warnings.append(f"文件大小 ({len(content) / 1024 / 1024:.1f}MB) 超过 100MB，处理可能较慢")

    # SHA256 hash
    file_hash = _compute_hash(content)

    # Decode text
    text = decode_text(content)

    # Extract metadata
    title = _title_from_filename(filename)
    author = _extract_author(text)

    # Split chapters
    chapters = split_chapters(text)
    total_chapters = len(chapters)
    total_words = sum(ch.word_count for ch in chapters)

    # Warnings
    if total_chapters == 1:
        warnings.append("仅检测到 1 个章节，可能章节切分未生效")
    for ch in chapters:
        if ch.word_count > _LARGE_CHAPTER_WORDS:
            warnings.append(f"章节 '{ch.title}' 字数为 {ch.word_count}，超过 5 万字")

    # Check for duplicate
    existing = await novel_store.find_by_hash(file_hash)
    duplicate_novel_id = existing["id"] if existing else None

    # Build preview
    chapter_previews = [
        ChapterPreviewItem(
            chapter_num=ch.chapter_num,
            title=ch.title,
            word_count=ch.word_count,
        )
        for ch in chapters
    ]

    preview = UploadPreviewResponse(
        title=title,
        author=author,
        file_hash=file_hash,
        total_chapters=total_chapters,
        total_words=total_words,
        chapters=chapter_previews,
        warnings=warnings,
        duplicate_novel_id=duplicate_novel_id,
    )

    # Cache for confirm step (including raw text for re-split)
    _upload_cache[file_hash] = _CachedUpload(
        preview=preview,
        chapters=chapters,
        raw_text=text,
        created_at=time.time(),
    )

    return preview


async def confirm_import(
    file_hash: str, title: str, author: str | None
) -> dict:
    """Confirm import of a previously uploaded file.

    Retrieves cached chapter data by file_hash and writes to DB.
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

    # Persist to DB
    await novel_store.insert_novel(
        novel_id=novel_id,
        title=title,
        author=author,
        file_hash=file_hash,
        total_chapters=total_chapters,
        total_words=total_words,
    )
    await novel_store.insert_chapters(novel_id, chapters)

    # Remove from cache after successful import
    del _upload_cache[file_hash]

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

    # Re-split with specified mode
    chapters = split_chapters(text, mode=mode, custom_regex=custom_regex)
    total_chapters = len(chapters)
    total_words = sum(ch.word_count for ch in chapters)

    # Rebuild warnings
    warnings: list[str] = []
    if total_chapters == 1:
        warnings.append("仅检测到 1 个章节，可能章节切分未生效")
    for ch in chapters:
        if ch.word_count > _LARGE_CHAPTER_WORDS:
            warnings.append(f"章节 '{ch.title}' 字数为 {ch.word_count}，超过 5 万字")

    # Build updated preview
    chapter_previews = [
        ChapterPreviewItem(
            chapter_num=ch.chapter_num,
            title=ch.title,
            word_count=ch.word_count,
        )
        for ch in chapters
    ]

    preview = UploadPreviewResponse(
        title=cached.preview.title,
        author=cached.preview.author,
        file_hash=file_hash,
        total_chapters=total_chapters,
        total_words=total_words,
        chapters=chapter_previews,
        warnings=warnings,
        duplicate_novel_id=cached.preview.duplicate_novel_id,
    )

    # Update cache
    cached.preview = preview
    cached.chapters = chapters

    return preview
