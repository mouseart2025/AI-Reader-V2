"""Chapter splitting engine with 5 pattern modes."""

import re
from dataclasses import dataclass, field


@dataclass
class ChapterInfo:
    chapter_num: int
    title: str
    content: str
    word_count: int
    volume_num: int | None = None
    volume_title: str | None = None


# Chinese number mapping for conversion
_CN_NUMS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "百": 100, "千": 1000, "万": 10000,
}

# 5 splitting modes ordered by priority
_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Mode 1: 第X章
    (
        "chapter_zh",
        re.compile(
            r"^第[零〇一二三四五六七八九十百千万\d]+[章][\s：:]*(.*)$",
            re.MULTILINE,
        ),
    ),
    # Mode 2: 第X回/节/卷
    (
        "section_zh",
        re.compile(
            r"^第[零〇一二三四五六七八九十百千万\d]+[回节卷][\s：:]*(.*)$",
            re.MULTILINE,
        ),
    ),
    # Mode 3: 1. / 001 / 1、
    (
        "numbered",
        re.compile(
            r"^(\d{1,4})[\.、\s]\s*(.+)$",
            re.MULTILINE,
        ),
    ),
    # Mode 4: Markdown headers
    (
        "markdown",
        re.compile(
            r"^#{1,3}\s+(.+)$",
            re.MULTILINE,
        ),
    ),
    # Mode 5: Separator lines (--- or ===)
    (
        "separator",
        re.compile(
            r"^[-=]{3,}\s*$",
            re.MULTILINE,
        ),
    ),
]

_MIN_PROLOGUE_CHARS = 100  # Minimum chars to keep a prologue


def split_chapters(text: str) -> list[ChapterInfo]:
    """Split text into chapters using the best matching pattern.

    Tries all 5 patterns, picks the one with the most matches (>= 2).
    If no pattern matches >= 2 times, returns the entire text as one chapter.
    """
    best_mode = None
    best_matches = []
    best_count = 0

    for mode_name, pattern in _PATTERNS:
        matches = list(pattern.finditer(text))
        if len(matches) >= 2 and len(matches) > best_count:
            best_mode = mode_name
            best_matches = matches
            best_count = len(matches)

    if not best_matches:
        # No pattern matched >= 2 times — return entire text as single chapter
        return [
            ChapterInfo(
                chapter_num=1,
                title="全文",
                content=text.strip(),
                word_count=len(text.strip()),
            )
        ]

    return _split_by_matches(text, best_mode, best_matches)


def _split_by_matches(
    text: str, mode: str, matches: list[re.Match]
) -> list[ChapterInfo]:
    """Split text at match positions and build ChapterInfo list."""
    chapters: list[ChapterInfo] = []
    chapter_num = 0

    # Handle prologue (text before first match)
    prologue_text = text[: matches[0].start()].strip()
    if len(prologue_text) >= _MIN_PROLOGUE_CHARS:
        chapter_num += 1
        chapters.append(
            ChapterInfo(
                chapter_num=chapter_num,
                title="序章",
                content=prologue_text,
                word_count=len(prologue_text),
            )
        )

    # Split at each match position
    for i, match in enumerate(matches):
        chapter_num += 1
        title = _extract_title(mode, match)

        # Content runs from after the title line to the next match (or end)
        content_start = match.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[content_start:content_end].strip()

        chapters.append(
            ChapterInfo(
                chapter_num=chapter_num,
                title=title,
                content=content,
                word_count=len(content),
            )
        )

    return chapters


def _extract_title(mode: str, match: re.Match) -> str:
    """Extract a clean chapter title from a regex match."""
    if mode == "separator":
        return f"第{match.start()}段"  # Separators have no title

    if mode == "numbered":
        # Group 2 is the title text after the number
        return match.group(2).strip() if match.group(2) else match.group(0).strip()

    # For chapter_zh, section_zh, markdown: group 1 is the title
    title = match.group(1).strip() if match.group(1) else ""
    if title:
        return title

    # Fallback: use the entire matched line
    return match.group(0).strip()
