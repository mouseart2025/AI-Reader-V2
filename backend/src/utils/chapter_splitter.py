"""Chapter splitting engine with pattern modes + heuristic + fixed-size fallback."""

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


@dataclass
class SplitResult:
    """Extended split result with metadata about how the split was performed."""
    chapters: list[ChapterInfo]
    matched_mode: str  # e.g. "chapter_zh", "heuristic_title", "fixed_size", "custom", "none"
    is_fallback: bool = False  # True if heuristic or fixed_size was used as fallback


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
    # Mode 3: 一、/ 二、/ 十二、/ 一百二十三、 (Chinese numeral + 顿号)
    (
        "cn_numbered",
        re.compile(
            r"^\s*([一二三四五六七八九十百千万零〇]+)、[^\S\n]*(.*)$",
            re.MULTILINE,
        ),
    ),
    # Mode 4: 1. / 001 / 1、
    (
        "numbered",
        re.compile(
            r"^(\d{1,4})[\.、\s]\s*(.+)$",
            re.MULTILINE,
        ),
    ),
    # Mode 4: English chapter headers (CHAPTER 1 / Part I / Prologue / Epilogue)
    (
        "chapter_en",
        re.compile(
            r"^\s*(?:CHAPTER|Chapter|PART|Part|PROLOGUE|Prologue|EPILOGUE|Epilogue)\s*[\d\sIVXLCDM]*[\.:\s\u2014-]*(.*)$",
            re.MULTILINE,
        ),
    ),
    # Mode 5: Markdown headers
    (
        "markdown",
        re.compile(
            r"^#{1,3}\s+(.+)$",
            re.MULTILINE,
        ),
    ),
    # Mode 6: Separator lines (--- or ===)
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
    r"^\s*(?:#{1,3}\s+)?第[零〇一二两三四五六七八九十百千万\d]+[卷部集][\s：:]*(.*)$",
    re.MULTILINE,
)

# Expose available mode names for the API
AVAILABLE_MODES = [name for name, _ in _PATTERNS] + ["heuristic_title", "fixed_size"]


_BLANK_LINE_RE = re.compile(r"\n{4,}")  # 3+ consecutive empty lines

_DEFAULT_FIXED_SIZE = 8000  # chars per chunk for fixed_size fallback
_OVERSIZED_THRESHOLD = 50_000  # chars: chapters larger than this get sub-split

# Punctuation that disqualifies a line from being a heuristic title
_BODY_PUNCTUATION = set("。，；：！？…、》）」』】")


def split_chapters(text: str, mode: str | None = None, custom_regex: str | None = None) -> list[ChapterInfo]:
    """Split text into chapters.

    Backward-compatible wrapper that returns just the chapter list.
    """
    result = split_chapters_ex(text, mode=mode, custom_regex=custom_regex)
    return result.chapters


def split_chapters_ex(text: str, mode: str | None = None, custom_regex: str | None = None) -> SplitResult:
    """Split text into chapters with metadata about the split.

    If mode is given, uses that specific pattern.
    If custom_regex is given, compiles and uses it.
    Otherwise tries all patterns, picks the one with the most matches (>= 2).
    Falls back to heuristic_title, then fixed_size if nothing works.
    """
    # Normalize line endings: \r\n → \n, standalone \r → \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Compress excessive blank lines (3+ → 2)
    text = _BLANK_LINE_RE.sub("\n\n\n", text)

    # Custom regex mode
    if custom_regex:
        try:
            pattern = re.compile(custom_regex, re.MULTILINE)
        except re.error:
            return SplitResult(
                chapters=[ChapterInfo(
                    chapter_num=1, title="全文",
                    content=text.strip(), word_count=len(text.strip()),
                )],
                matched_mode="custom",
                is_fallback=False,
            )
        matches = list(pattern.finditer(text))
        if len(matches) >= 2:
            chapters = _split_by_matches(text, "custom", matches)
            _assign_volumes(text, chapters)
            _detect_volume_resets(chapters)
            chapters = _subsplit_oversized(chapters)
            return SplitResult(chapters=chapters, matched_mode="custom")
        return SplitResult(
            chapters=[ChapterInfo(
                chapter_num=1, title="全文",
                content=text.strip(), word_count=len(text.strip()),
            )],
            matched_mode="custom",
        )

    # Specific mode
    if mode:
        if mode == "heuristic_title":
            chapters = _heuristic_title_split(text)
            if chapters:
                _assign_volumes(text, chapters)
                _detect_volume_resets(chapters)
                chapters = _subsplit_oversized(chapters)
                return SplitResult(chapters=chapters, matched_mode="heuristic_title")
            return SplitResult(
                chapters=_fixed_size_split(text),
                matched_mode="fixed_size",
                is_fallback=True,
            )
        if mode == "fixed_size":
            return SplitResult(
                chapters=_fixed_size_split(text),
                matched_mode="fixed_size",
            )
        for mode_name, pattern in _PATTERNS:
            if mode_name == mode:
                matches = list(pattern.finditer(text))
                if len(matches) >= 2:
                    chapters = _split_by_matches(text, mode_name, matches)
                    _assign_volumes(text, chapters)
                    _detect_volume_resets(chapters)
                    chapters = _subsplit_oversized(chapters)
                    return SplitResult(chapters=chapters, matched_mode=mode_name)
                return SplitResult(
                    chapters=_fixed_size_split(text),
                    matched_mode="fixed_size",
                    is_fallback=True,
                )

    # Auto-detect: try all patterns, pick the best
    best_mode = None
    best_matches: list[re.Match] = []
    best_count = 0

    for mode_name, pattern in _PATTERNS:
        matches = list(pattern.finditer(text))
        if len(matches) >= 2 and len(matches) > best_count:
            best_mode = mode_name
            best_matches = matches
            best_count = len(matches)

    if not best_matches:
        # No regex pattern matched >= 2 times — try heuristic title detection
        chapters = _heuristic_title_split(text)
        if chapters:
            _assign_volumes(text, chapters)
            _detect_volume_resets(chapters)
            chapters = _subsplit_oversized(chapters)
            return SplitResult(chapters=chapters, matched_mode="heuristic_title", is_fallback=True)
        # Last resort: fixed-size split at paragraph boundaries
        return SplitResult(
            chapters=_fixed_size_split(text),
            matched_mode="fixed_size",
            is_fallback=True,
        )

    chapters = _split_by_matches(text, best_mode, best_matches)

    # If result is a single huge chapter, try fixed_size as secondary fallback
    if len(chapters) == 1 and chapters[0].word_count > 30_000:
        fallback = _fixed_size_split(text)
        if len(fallback) > 1:
            return SplitResult(chapters=fallback, matched_mode="fixed_size", is_fallback=True)

    _assign_volumes(text, chapters)
    _detect_volume_resets(chapters)

    # Sub-split any oversized chapters (>50k chars) to keep all chunks manageable
    chapters = _subsplit_oversized(chapters)

    return SplitResult(chapters=chapters, matched_mode=best_mode or "none")


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

    if mode in ("numbered", "cn_numbered"):
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


# Pattern for extracting chapter numbers from titles (e.g., "第一章", "第3回")
_CH_NUM_PATTERN = re.compile(r"第([零〇一二两三四五六七八九十百千万\d]+)[章回节]")


def _detect_volume_resets(chapters: list[ChapterInfo]) -> None:
    """Infer volume boundaries when chapter numbers reset.

    Only runs when _assign_volumes() found no volume markers.
    Detects repeated chapter labels (e.g., two "第一章") as volume boundaries.
    """
    if not chapters:
        return
    # Skip if any chapter already has volume info
    if any(ch.volume_num is not None for ch in chapters):
        return

    seen_labels: set[str] = set()
    vol_num = 1

    for ch in chapters:
        m = _CH_NUM_PATTERN.search(ch.title)
        if not m:
            continue
        ch_label = m.group(0)  # e.g., "第一章"
        if ch_label in seen_labels:
            # Chapter number repeated → new volume starts
            vol_num += 1
            seen_labels.clear()
        seen_labels.add(ch_label)
        ch.volume_num = vol_num

    # Only keep volume info if we actually found multiple volumes
    if vol_num <= 1:
        for ch in chapters:
            ch.volume_num = None


def _heuristic_title_split(text: str) -> list[ChapterInfo] | None:
    """Detect chapter boundaries from short title-like lines.

    A line is a title candidate if ALL conditions are met:
    - Length ≤ 30 characters (stripped)
    - Contains no body punctuation (。，；：！？…)
    - Not pure digits, pure punctuation, or blank
    - Preceded by a blank line (or is the start of the file)

    Returns a list of ChapterInfo, or None if too few candidates found.
    """
    lines = text.split("\n")
    candidates: list[int] = []  # line indices of candidate titles

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) > 30:
            continue
        if any(ch in _BODY_PUNCTUATION for ch in stripped):
            continue
        # Must not be pure digits or pure symbols
        if stripped.isdigit():
            continue
        if all(not c.isalnum() for c in stripped):
            continue
        # Preceded by blank line or at file start
        if i > 0:
            prev = lines[i - 1].strip()
            if prev:
                continue
        candidates.append(i)

    if len(candidates) < 2:
        return None

    # Filter: remove candidates that are too close or too far apart
    filtered: list[int] = [candidates[0]]
    for idx in candidates[1:]:
        # Compute char distance from previous candidate
        prev_idx = filtered[-1]
        gap_chars = sum(len(lines[j]) + 1 for j in range(prev_idx + 1, idx))
        if gap_chars < 500:
            # Too close — skip (keep the earlier one)
            continue
        filtered.append(idx)

    # Second pass: filter candidates where gap is too large (> 50,000 chars)
    if len(filtered) >= 3:
        final: list[int] = [filtered[0]]
        for idx in filtered[1:]:
            prev_idx = final[-1]
            gap_chars = sum(len(lines[j]) + 1 for j in range(prev_idx + 1, idx))
            if gap_chars > 50_000:
                # Gap too large — still include but note it
                pass
            final.append(idx)
        filtered = final

    if len(filtered) < 2:
        return None

    # Build chapters from filtered candidates
    chapters: list[ChapterInfo] = []
    chapter_num = 0

    # Prologue (text before first candidate)
    prologue_lines = lines[: filtered[0]]
    prologue_text = "\n".join(prologue_lines).strip()
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

    for i, line_idx in enumerate(filtered):
        title = lines[line_idx].strip()
        # Content: from next line after title to next candidate (or end)
        content_start = line_idx + 1
        content_end = filtered[i + 1] if i + 1 < len(filtered) else len(lines)
        # Walk back to exclude trailing blank lines before next title
        content_text = "\n".join(lines[content_start:content_end]).strip()
        if not content_text:
            continue
        chapter_num += 1
        chapters.append(
            ChapterInfo(
                chapter_num=chapter_num,
                title=title,
                content=content_text,
                word_count=len(content_text),
            )
        )

    return chapters if len(chapters) >= 2 else None


def _subsplit_oversized(
    chapters: list[ChapterInfo],
    threshold: int = _OVERSIZED_THRESHOLD,
    target_size: int = _DEFAULT_FIXED_SIZE,
) -> list[ChapterInfo]:
    """Sub-split chapters exceeding *threshold* chars using fixed-size splitting.

    Preserves normal-sized chapters unchanged. Oversized chapters are split into
    sub-chunks titled "{original_title} (1)", "(2)", etc.  Chapter numbers are
    reassigned sequentially after expansion.

    Returns the original list unchanged if no chapter exceeds the threshold.
    """
    any_oversized = any(ch.word_count > threshold for ch in chapters)
    if not any_oversized:
        return chapters

    result: list[ChapterInfo] = []
    for ch in chapters:
        if ch.word_count <= threshold:
            result.append(ch)
            continue
        # Sub-split this chapter's content
        sub_chapters = _fixed_size_split(ch.content, target_size=target_size)
        if len(sub_chapters) <= 1:
            result.append(ch)
            continue
        # Re-title sub-chapters
        for j, sub in enumerate(sub_chapters, 1):
            sub.title = f"{ch.title} ({j})"
            sub.volume_num = ch.volume_num
            sub.volume_title = ch.volume_title
            result.append(sub)

    # Reassign chapter numbers sequentially
    for i, ch in enumerate(result, 1):
        ch.chapter_num = i

    return result


def _fixed_size_split(text: str, target_size: int = _DEFAULT_FIXED_SIZE) -> list[ChapterInfo]:
    """Split text into roughly equal-sized chunks at paragraph boundaries.

    Finds the nearest blank line to each target_size boundary.
    Never splits mid-sentence.
    """
    text = text.strip()
    if not text:
        return [ChapterInfo(chapter_num=1, title="全文", content="", word_count=0)]

    total = len(text)
    if total <= target_size * 1.5:
        # Too short to split meaningfully
        return [ChapterInfo(chapter_num=1, title="第 1 段", content=text, word_count=total)]

    # Find all line break positions.  We use single \n rather than \n\n (paragraph
    # breaks) because many Chinese novels use only single newlines between paragraphs.
    # Using \n gives maximum flexibility for finding a break near each target boundary.
    breaks: list[int] = []
    i = 0
    while i < total:
        nl = text.find("\n", i)
        if nl == -1:
            break
        breaks.append(nl)
        i = nl + 1

    if not breaks:
        # No breaks at all — return as single chunk
        return [ChapterInfo(chapter_num=1, title="第 1 段", content=text, word_count=total)]

    # Greedy: pick breaks nearest to multiples of target_size
    chapters: list[ChapterInfo] = []
    chunk_start = 0
    chapter_num = 0

    while chunk_start < total:
        target_end = chunk_start + target_size

        if target_end >= total:
            # Last chunk
            chunk_text = text[chunk_start:].strip()
            if chunk_text:
                chapter_num += 1
                chapters.append(
                    ChapterInfo(
                        chapter_num=chapter_num,
                        title=f"第 {chapter_num} 段",
                        content=chunk_text,
                        word_count=len(chunk_text),
                    )
                )
            break

        # Find the nearest paragraph break to target_end
        best_break = None
        best_dist = float("inf")
        for bp in breaks:
            if bp <= chunk_start:
                continue
            dist = abs(bp - target_end)
            if dist < best_dist:
                best_dist = dist
                best_break = bp
            elif bp > target_end + target_size:
                break  # Past the window

        if best_break is None or best_break <= chunk_start:
            # No break found — take everything remaining
            chunk_text = text[chunk_start:].strip()
            if chunk_text:
                chapter_num += 1
                chapters.append(
                    ChapterInfo(
                        chapter_num=chapter_num,
                        title=f"第 {chapter_num} 段",
                        content=chunk_text,
                        word_count=len(chunk_text),
                    )
                )
            break

        chunk_text = text[chunk_start:best_break].strip()
        if chunk_text:
            chapter_num += 1
            chapters.append(
                ChapterInfo(
                    chapter_num=chapter_num,
                    title=f"第 {chapter_num} 段",
                    content=chunk_text,
                    word_count=len(chunk_text),
                )
            )
        chunk_start = best_break + 2  # Skip past the \n\n

    return chapters if chapters else [
        ChapterInfo(chapter_num=1, title="第 1 段", content=text.strip(), word_count=len(text.strip()))
    ]
