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
    _text_pos: int = field(default=0, repr=False)  # internal: position in source text


# Chinese number mapping for conversion
_CN_NUMS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "百": 100, "千": 1000, "万": 10000,
}

# 5 splitting modes ordered by priority
# \s* after ^ to tolerate leading whitespace (fullwidth spaces, tabs, etc.)
_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Mode 1: 第X章 / 番外X / 后记 / 尾声 / 完本感言
    # Note: 两 is needed for 第两千章 etc.
    (
        "chapter_zh",
        re.compile(
            r"^\s*(?:第[零〇一二两三四五六七八九十百千万\d]+[章]|番外[零〇一二两三四五六七八九十百千万\d篇]*|后记|尾声|完本感言)[\s：:]*(.*)$",
            re.MULTILINE,
        ),
    ),
    # Mode 2: 第X回/节/卷/幕/场
    (
        "section_zh",
        re.compile(
            r"^\s*第[零〇一二两三四五六七八九十百千万\d]+[幕场回节卷][\s：:]*(.*)$",
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

# Volume/part markers — detected as secondary markers within chapter content
_VOLUME_PATTERN = re.compile(
    r"^\s*第[零〇一二两三四五六七八九十百千万\d]+[卷部集][\s：:]*(.*)$",
    re.MULTILINE,
)

# Expose available mode names for the API
AVAILABLE_MODES = [name for name, _ in _PATTERNS]


def split_chapters(text: str, mode: str | None = None, custom_regex: str | None = None) -> list[ChapterInfo]:
    """Split text into chapters.

    If mode is given, uses that specific pattern.
    If custom_regex is given, compiles and uses it.
    Otherwise tries all 5 patterns, picks the one with the most matches (>= 2).
    If no pattern matches >= 2 times, returns the entire text as one chapter.
    """
    # Normalize line endings: \r\n → \n, standalone \r → \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Custom regex mode
    if custom_regex:
        try:
            pattern = re.compile(custom_regex, re.MULTILINE)
        except re.error:
            return [
                ChapterInfo(
                    chapter_num=1, title="全文",
                    content=text.strip(), word_count=len(text.strip()),
                )
            ]
        matches = list(pattern.finditer(text))
        if len(matches) >= 2:
            return _split_by_matches(text, "custom", matches)
        return [
            ChapterInfo(
                chapter_num=1, title="全文",
                content=text.strip(), word_count=len(text.strip()),
            )
        ]

    # Specific mode
    if mode:
        for mode_name, pattern in _PATTERNS:
            if mode_name == mode:
                matches = list(pattern.finditer(text))
                if len(matches) >= 2:
                    return _split_by_matches(text, mode_name, matches)
                return [
                    ChapterInfo(
                        chapter_num=1, title="全文",
                        content=text.strip(), word_count=len(text.strip()),
                    )
                ]

    # Auto-detect: try all patterns, pick the best
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

    chapters = _split_by_matches(text, best_mode, best_matches)
    _assign_volumes(text, chapters)
    return chapters


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
                _text_pos=0,
            )
        )

    # Split at each match position
    for i, match in enumerate(matches):
        title = _extract_title(mode, match)

        # Content runs from after the title line to the next match (or end)
        content_start = match.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[content_start:content_end].strip()

        # Skip empty chapters (duplicate markers or formatting artifacts)
        if not content:
            continue

        chapter_num += 1
        chapters.append(
            ChapterInfo(
                chapter_num=chapter_num,
                title=title,
                content=content,
                word_count=len(content),
                _text_pos=match.start(),
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

    if mode == "custom":
        # Try group 1, fall back to full match
        try:
            title = match.group(1).strip() if match.group(1) else ""
            if title:
                return title
        except IndexError:
            pass
        return match.group(0).strip()

    # For chapter_zh, section_zh, markdown: group 1 is the title
    title = match.group(1).strip() if match.group(1) else ""
    if title:
        return title

    # Fallback: use the entire matched line
    return match.group(0).strip()


def _assign_volumes(text: str, chapters: list[ChapterInfo]) -> None:
    """Detect volume markers in text and assign volume info to chapters.

    Finds all volume markers (第X卷/部/集) in the original text,
    then assigns each chapter to the appropriate volume based on text position.
    Also strips volume marker lines from chapter content.
    """
    vol_matches = list(_VOLUME_PATTERN.finditer(text))
    if not vol_matches:
        return

    # Build volume list sorted by position: (start_pos, vol_num, vol_title)
    volumes = [
        (m.start(), i + 1, m.group(1).strip() if m.group(1) else "")
        for i, m in enumerate(vol_matches)
    ]

    # Assign each chapter to the most recent volume before its position
    for ch in chapters:
        for vol_start, vol_num, vol_title in reversed(volumes):
            if ch._text_pos >= vol_start:
                ch.volume_num = vol_num
                ch.volume_title = vol_title
                break

        # Strip volume marker lines from content
        cleaned = _VOLUME_PATTERN.sub("", ch.content).strip()
        if cleaned != ch.content:
            ch.content = cleaned
            ch.word_count = len(cleaned)
