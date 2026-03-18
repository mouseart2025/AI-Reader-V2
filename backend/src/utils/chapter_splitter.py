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
    detected_genre: str = "unknown"  # essay, poetry, novel, short_collection, unknown


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
    # [^\S\n] = whitespace except newline — prevents cross-line matching
    # Optional prefix: "第X部 副标题 " before 第X章 (e.g. "第二部 不夜之候 第一章")
    (
        "chapter_zh",
        re.compile(
            r"^\s*(?:第[零〇一二两三四五六七八九十百千万\d]+[部].+?)?(?:第[零〇一二两三四五六七八九十百千万\d]+[章]|番外[零〇一二两三四五六七八九十百千万\d篇]*|后记|尾声|完本感言)[^\S\n：:]*(.*)$",
            re.MULTILINE,
        ),
    ),
    # Mode 2: 第X回/节/卷/幕/场/部 OR 卷X (reversed order) OR (第X部)/(第X卷) (parenthesized)
    # Lookahead prevents false matches like 第二回你... (meaning "second time")
    # or 第三部分 (meaning "part 3") where the suffix is part of a word
    (
        "section_zh",
        re.compile(
            r"^\s*(?:"
            r"[(（]第[零〇一二两三四五六七八九十百千万\d]+[卷部][)）]"  # (第X卷) / (第X部)
            r"|第[零〇一二两三四五六七八九十百千万\d]+[幕场回节卷部](?=$|[\s：:(（·・—–\-])"  # 第X回/节/卷/部
            r"|卷[零〇一二两三四五六七八九十百千万\d]+(?=$|[\s：:·・—–\-\d])"  # 卷X
            r")[^\S\n：:]*(.*)$",
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
    # Allow leading whitespace (fullwidth \u3000 or regular spaces) before dashes
    (
        "separator",
        re.compile(
            r"^\s*[-=]{3,}\s*$",
            re.MULTILINE,
        ),
    ),
]

_MIN_PROLOGUE_CHARS = 100  # Minimum chars to keep a prologue

# Volume/part markers — detected as secondary markers within chapter content
# Group 'vol_name' captures the volume marker itself (e.g. "第一部")
# Group 1 captures any trailing subtitle text
_VOLUME_PATTERN = re.compile(
    r"^\s*(?:#{1,3}\s+)?(?P<vol_name>(?:第[零〇一二两三四五六七八九十百千万\d]+[卷部集]|卷[零〇一二两三四五六七八九十百千万\d]+))(?:[\s：:·・]+(.*))?$",
    re.MULTILINE,
)

# Expose available mode names for the API
AVAILABLE_MODES = [name for name, _ in _PATTERNS] + ["heuristic_title", "fixed_size"]


_BLANK_LINE_RE = re.compile(r"\n{4,}")  # 3+ consecutive empty lines

_DEFAULT_FIXED_SIZE = 8000  # chars per chunk for fixed_size fallback
_OVERSIZED_THRESHOLD = 50_000  # chars: chapters larger than this get sub-split

# Punctuation that disqualifies a line from being a heuristic title
_BODY_PUNCTUATION = set("。，；：！？…、》）」』】")

# URL detection for filtering obfuscated URLs (spaces + fullwidth chars)
_URL_LIKE_RE = re.compile(
    r'(?:https?://|www\.|\.com|\.cn|\.net|\.org|\.txt)',
    re.IGNORECASE,
)

_FW_TO_HW = str.maketrans(
    'ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ．',
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz.',
)


def _is_url_like(text: str) -> bool:
    """Check if text looks like an obfuscated URL after normalizing spaces/fullwidth."""
    normalized = text.replace(' ', '').translate(_FW_TO_HW)
    return bool(_URL_LIKE_RE.search(normalized))


# Paragraph restoration for single-line content (some TXT sources collapse
# paragraphs into one line, using a space after sentence-ending punctuation)
_PARA_BREAK_RE = re.compile(r'([。！？…）】」』\u201d]) ')


def _restore_paragraphs(content: str) -> str:
    """Restore paragraph breaks in content that lacks newlines.

    Some TXT file sources (e.g., from novel aggregator sites) encode each chapter
    as a single long line, with spaces after sentence-ending punctuation as the
    only paragraph separator.  This function detects such content and restores
    newlines at those points.
    """
    if len(content) < 1000:
        return content
    # Only apply when content has very low newline density
    newline_count = content.count("\n")
    expected_min = len(content) / 2000  # at least 1 newline per 2000 chars
    if newline_count >= expected_min:
        return content
    restored = _PARA_BREAK_RE.sub(r"\1\n", content)
    return restored


def _augment_with_volume_markers(text: str, matches: list[re.Match]) -> list[re.Match]:
    """Add 卷X lines as additional split points alongside section_zh matches.

    When section_zh matches 第X部/回/节 but the text also contains 卷X lines
    (e.g., 卷六 安多纳德, 卷八·女朋友们), those lines are structurally identical
    chapter boundaries that section_zh misses.  Merge them to avoid losing content.
    """
    vol_matches = [m for m in _VOLUME_PATTERN.finditer(text)
                   if m.group("vol_name").startswith("卷")]
    if not vol_matches:
        return matches
    existing_starts = {m.start() for m in matches}
    extra = [m for m in vol_matches if m.start() not in existing_starts]
    if not extra:
        return matches
    return sorted(matches + extra, key=lambda m: m.start())


def detect_text_genre(text: str) -> tuple[str, float]:
    """Detect text genre using lightweight heuristics.

    Returns (genre, confidence) where genre is one of:
    "novel", "essay", "poetry", "short_collection", "unknown".

    Safety valve: text > 50K chars auto-downgrades essay/poetry to "unknown".
    """
    from src.utils.text_features import compute_dialogue_ratio
    from statistics import mean

    text_len = len(text)

    # Compute features on first 50K chars for performance
    sample = text[:50_000]
    dialogue_ratio = compute_dialogue_ratio(sample)

    # Paragraph stats
    paras = [l.strip() for l in sample.split("\n") if l.strip()]
    para_count = len(paras)
    avg_para_len = mean(len(p) for p in paras) if paras else 0

    # Count how many regex modes match >= 2 times (early exit for performance)
    from itertools import islice
    modes_with_matches = 0
    for _, pattern in _PATTERNS:
        if len(list(islice(pattern.finditer(text), 2))) >= 2:
            modes_with_matches += 1

    # ── Novel detection (high dialogue) — check FIRST to prevent false poetry ──
    if dialogue_ratio > 0.05:
        return ("novel", 0.9)

    # ── Poetry detection ──
    # avg_para_len < 50 AND 100+ paragraphs AND < 50K chars AND no pattern matches AND low dialogue
    if (avg_para_len < 50 and para_count > 100 and text_len < 50_000
            and modes_with_matches == 0 and dialogue_ratio < 0.01):
        return ("poetry", 0.85)

    # ── Essay detection ──
    # < 30K chars AND dialogue_ratio < 1% AND no pattern matches >= 2
    if text_len < 30_000 and dialogue_ratio < 0.01 and modes_with_matches == 0:
        return ("essay", 0.80)

    # ── Short collection ──
    # Separator or heuristic_title patterns dominant, moderate length
    sep_pattern = next(p for name, p in _PATTERNS if name == "separator")
    sep_matches = list(islice(sep_pattern.finditer(text), 4))
    if len(sep_matches) >= 3 and text_len < 200_000:
        return ("short_collection", 0.7)

    # Default
    return ("unknown", 0.5)


def _score_mode(mode: str, matches: list[re.Match], text: str, genre: str) -> float:
    """Score a candidate split mode based on match quality.

    Higher score = better candidate. Considers:
    - Match count (baseline)
    - Chapter size uniformity (CV penalty)
    - Tiny chapter ratio penalty (miscut detection)
    - Genre-aware mode suppression
    """
    from statistics import mean, stdev

    count = len(matches)
    if count < 2:
        return 0

    score = float(count)

    # Estimate chapter sizes from match positions
    text_len = len(text)
    sizes = []
    for i in range(len(matches)):
        start = matches[i].end()
        end = matches[i + 1].start() if i + 1 < len(matches) else text_len
        sizes.append(max(0, end - start))

    if sizes:
        avg = mean(sizes)
        if avg > 0 and len(sizes) > 1:
            cv = stdev(sizes) / avg
            score *= max(0.3, 1 - cv)  # Uniformity reward

        # Tiny chapter penalty (< 200 chars likely miscut)
        tiny_ratio = sum(1 for s in sizes if s < 200) / len(sizes)
        score *= max(0.2, 1 - tiny_ratio)

    # Genre-aware suppression: separator and numbered are usually wrong for novels
    if genre in ("novel", "unknown"):
        if mode == "separator":
            score *= 0.1  # Heavy penalty — separators in novels are scene breaks, not chapters
        elif mode == "numbered":
            score *= 0.5  # Moderate penalty — numbered lists in novels are often content enumerations

    # Short collection boost
    if genre == "short_collection" and mode in ("heuristic_title", "separator"):
        score *= 1.5

    return score


_CJK_SEQUENCES = [
    "上中下",
    "甲乙丙丁戊己庚辛壬癸",
    "子丑寅卯辰巳午未申酉戌亥",
    "春夏秋冬",
    "东南西北",
    "前后",
    "左右",
    "内外",
]


def _expand_cjk_class(chars: set[str]) -> str:
    """Expand a set of CJK characters using known sequences.

    E.g., {"上", "中"} → "上中下" (adds "下" from the 上中下 sequence).
    """
    result = set(chars)
    for seq in _CJK_SEQUENCES:
        if chars <= set(seq) and len(chars & set(seq)) >= 2:
            result |= set(seq)
        elif len(chars & set(seq)) >= 1 and len(chars) <= 3:
            # At least one char matches a sequence — add the full sequence
            if all(c in seq for c in chars if c in set(seq)):
                result |= set(seq)
    return "".join(sorted(result))


def infer_pattern_from_points(text: str, split_points: list[int]) -> str | None:
    """Infer a regex pattern from user-marked split points.

    Extracts the heading line at each split point, tokenizes each heading into
    segments (CJK chars, digits, punctuation, spaces), aligns them column-wise,
    and generates a regex where varying segments become character classes.

    Examples:
      ["上卷 第01节", "上卷 第02节", "中卷 第01节"]
      → "^[上中下]卷 第\\d+节"

      ["卷一 01标题.1", "卷一 02标题.2"]
      → "^卷[一二三...] \\d+"
    """
    if len(split_points) < 2:
        return None

    # Extract the heading line at each split point
    headings: list[str] = []
    for pos in sorted(split_points):
        line_start = text.rfind("\n", 0, pos) + 1
        line_end = text.find("\n", pos)
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end].strip()
        if line:
            headings.append(line)

    if len(headings) < 2:
        return None

    # Pre-clean: strip leading parentheses/brackets for consistency
    # e.g. "(第一部)芳汀" → "第一部)芳汀" — helps find common structure
    def _strip_brackets(s: str) -> str:
        return s.lstrip("(（[【〖")

    # Check against existing patterns (on both raw and cleaned headings)
    for _, pattern in _PATTERNS:
        if all(pattern.match(h) for h in headings):
            return None  # Already covered by built-in mode
        cleaned = [_strip_brackets(h) for h in headings]
        if all(pattern.match(h) for h in cleaned):
            return None  # Covered after bracket stripping

    # Tokenize headings into typed segments
    # Order matters: Chinese numbers first, then digits, then CJK single chars, then ASCII words
    _TOKEN_RE = re.compile(
        r"([零〇一二两三四五六七八九十百千万]+)"    # Chinese numbers
        r"|(\d+)"                                    # Arabic digits
        r"|(\s+)"                                    # Whitespace
        r"|([^\w\s零〇一二两三四五六七八九十百千万]+)"  # Punctuation/symbols
        r"|([a-zA-Z]+)"                              # ASCII words
        r"|([\u4e00-\u9fff\u3400-\u4dbf])"           # Single CJK character
    )

    def _tokenize(s: str) -> list[tuple[str, str]]:
        """Returns list of (type, value) tokens."""
        tokens = []
        for m in _TOKEN_RE.finditer(s):
            if m.group(1):
                tokens.append(("cn_num", m.group(1)))
            elif m.group(2):
                tokens.append(("digit", m.group(2)))
            elif m.group(3):
                tokens.append(("space", m.group(3)))
            elif m.group(4):
                tokens.append(("punct", m.group(4)))
            elif m.group(5):
                tokens.append(("ascii", m.group(5)))
            elif m.group(6):
                tokens.append(("cjk", m.group(6)))
        return tokens

    tokenized = [_tokenize(h) for h in headings]

    # If first tokens diverge (e.g. "(" vs "第"), try bracket-stripped versions
    if len(set(t[0][1] for t in tokenized if t)) > 1:
        cleaned = [_strip_brackets(h) for h in headings]
        cleaned_tokens = [_tokenize(c) for c in cleaned]
        if len(set(t[0][1] for t in cleaned_tokens if t)) < len(set(t[0][1] for t in tokenized if t)):
            headings = cleaned
            tokenized = cleaned_tokens

    # Find the minimum token count — align up to that length
    min_len = min(len(t) for t in tokenized)
    if min_len == 0:
        return None

    # Build regex by aligning token columns
    regex_parts: list[str] = []
    cn_num_class = "[零〇一二两三四五六七八九十百千万\\d]+"

    for col in range(min_len):
        col_tokens = [(tokenized[i][col] if col < len(tokenized[i]) else ("", ""))
                      for i in range(len(tokenized))]
        types = set(t[0] for t in col_tokens)
        values = set(t[1] for t in col_tokens)

        if len(values) == 1:
            # All same — literal
            val = col_tokens[0][1]
            typ = col_tokens[0][0]
            if typ == "space":
                regex_parts.append(r"\s+")
            elif typ in ("digit", "cn_num"):
                regex_parts.append(cn_num_class)  # Even if same value, generalize numbers
            else:
                regex_parts.append(re.escape(val))
        elif types <= {"cn_num", "digit"}:
            # Varying numbers — use number class
            regex_parts.append(cn_num_class)
        elif types == {"cjk"}:
            # Varying single CJK characters — build character class
            # Auto-expand common sequences (上中→上中下, 甲乙→甲乙丙丁, etc.)
            chars = _expand_cjk_class(values)
            regex_parts.append(f"[{re.escape(chars)}]")
        elif types == {"ascii"}:
            # Varying ASCII words
            regex_parts.append(r"[a-zA-Z]+")
        else:
            # Mixed types — stop here, don't extend regex further
            break

    regex = "^" + "".join(regex_parts)

    # Validate
    try:
        pat = re.compile(regex, re.MULTILINE)
        if not all(pat.match(h) for h in headings):
            return None
        full_matches = len(list(pat.finditer(text)))
        if full_matches < 2:
            return None
        return regex
    except re.error:
        return None


def split_chapters(
    text: str,
    mode: str | None = None,
    custom_regex: str | None = None,
    split_points: list[int] | None = None,
) -> list[ChapterInfo]:
    """Split text into chapters.

    Backward-compatible wrapper that returns just the chapter list.
    """
    result = split_chapters_ex(text, mode=mode, custom_regex=custom_regex, split_points=split_points)
    return result.chapters


def split_chapters_ex(
    text: str,
    mode: str | None = None,
    custom_regex: str | None = None,
    split_points: list[int] | None = None,
) -> SplitResult:
    """Split text into chapters with metadata about the split.

    If split_points is given, splits at those character offsets (manual mode).
    If custom_regex is given, compiles and uses it.
    If mode is given, uses that specific pattern.
    Otherwise tries all patterns, picks the one with the most matches (>= 2).
    Falls back to heuristic_title, then fixed_size if nothing works.
    """
    # Normalize line endings: \r\n → \n, standalone \r → \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Compress excessive blank lines (3+ → 2)
    text = _BLANK_LINE_RE.sub("\n\n\n", text)

    # Detect text genre (for auto-detect mode only — skip if user specified mode/regex/points)
    genre = "unknown"
    if not mode and not custom_regex and not split_points:
        genre, confidence = detect_text_genre(text)
        # Essay/poetry: return as single chapter without splitting
        if genre in ("essay", "poetry") and confidence >= 0.7:
            return SplitResult(
                chapters=[ChapterInfo(
                    chapter_num=1, title="全文",
                    content=text.strip(), word_count=len(text.strip()),
                )],
                matched_mode=f"genre_{genre}",
                is_fallback=False,
                detected_genre=genre,
            )

    # Manual split points mode (from user-marked boundaries)
    if split_points:
        return _split_by_points(text, sorted(set(split_points)))

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
            chapters = _dedup_adjacent_chapters(chapters)
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
                chapters = _dedup_adjacent_chapters(chapters)
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
                    if mode_name == "section_zh":
                        matches = _augment_with_volume_markers(text, matches)
                    chapters = _split_by_matches(text, mode_name, matches)
                    _assign_volumes(text, chapters)
                    chapters = _dedup_adjacent_chapters(chapters)
                    _detect_volume_resets(chapters)
                    chapters = _subsplit_oversized(chapters)
                    return SplitResult(chapters=chapters, matched_mode=mode_name)
                return SplitResult(
                    chapters=_fixed_size_split(text),
                    matched_mode="fixed_size",
                    is_fallback=True,
                )

    # Auto-detect: try all patterns, pick the best using weighted scoring
    best_mode = None
    best_matches: list[re.Match] = []
    best_score = 0

    for mode_name, pattern in _PATTERNS:
        matches = list(pattern.finditer(text))
        if len(matches) < 2:
            continue
        score = _score_mode(mode_name, matches, text, genre)
        if score > best_score:
            best_mode = mode_name
            best_matches = matches
            best_score = score

    if not best_matches:
        # No regex pattern matched >= 2 times — try heuristic title detection
        chapters = _heuristic_title_split(text)
        if chapters:
            _assign_volumes(text, chapters)
            chapters = _dedup_adjacent_chapters(chapters)
            _detect_volume_resets(chapters)
            chapters = _subsplit_oversized(chapters)
            return SplitResult(chapters=chapters, matched_mode="heuristic_title", is_fallback=True,
                               detected_genre=genre)
        # Last resort: fixed-size split at paragraph boundaries
        return SplitResult(
            chapters=_fixed_size_split(text),
            matched_mode="fixed_size",
            is_fallback=True,
            detected_genre=genre,
        )

    if best_mode == "section_zh":
        best_matches = _augment_with_volume_markers(text, best_matches)

    chapters = _split_by_matches(text, best_mode, best_matches)

    # If result is a single huge chapter, try fixed_size as secondary fallback
    if len(chapters) == 1 and chapters[0].word_count > 30_000:
        fallback = _fixed_size_split(text)
        if len(fallback) > 1:
            return SplitResult(chapters=fallback, matched_mode="fixed_size", is_fallback=True,
                               detected_genre=genre)

    _assign_volumes(text, chapters)
    chapters = _dedup_adjacent_chapters(chapters)
    _detect_volume_resets(chapters)

    # Sub-split any oversized chapters (>50k chars) to keep all chunks manageable
    chapters = _subsplit_oversized(chapters)

    return SplitResult(chapters=chapters, matched_mode=best_mode or "none",
                       detected_genre=genre)


def _split_by_points(text: str, points: list[int]) -> SplitResult:
    """Split text at explicit character offsets (manual boundary marking)."""
    # Filter points to valid range and add boundaries
    total = len(text)
    valid = [p for p in points if 0 < p < total]
    boundaries = [0] + valid + [total]

    chapters: list[ChapterInfo] = []
    for i in range(len(boundaries) - 1):
        chunk = text[boundaries[i]:boundaries[i + 1]].strip()
        if not chunk:
            continue
        chapter_num = len(chapters) + 1
        # Title: first non-blank line, max 40 chars
        first_line = ""
        for line in chunk.split("\n"):
            s = line.strip()
            if s:
                first_line = s
                break
        if first_line and len(first_line) <= 40:
            title = first_line
        elif first_line:
            title = first_line[:38] + "…"
        else:
            title = f"第 {chapter_num} 段"
        chapters.append(ChapterInfo(
            chapter_num=chapter_num,
            title=title,
            content=chunk,
            word_count=len(chunk),
        ))

    if not chapters:
        chapters = [ChapterInfo(
            chapter_num=1, title="全文",
            content=text.strip(), word_count=len(text.strip()),
        )]

    return SplitResult(chapters=chapters, matched_mode="manual")


def _split_by_matches(
    text: str, mode: str, matches: list[re.Match]
) -> list[ChapterInfo]:
    """Split text at match positions and build ChapterInfo list."""
    chapters: list[ChapterInfo] = []
    chapter_num = 0

    # Handle prologue (text before first match)
    prologue_text = _restore_paragraphs(text[: matches[0].start()].strip())
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
        content = _restore_paragraphs(text[content_start:content_end].strip())

        # Skip empty chapters (duplicate markers or formatting artifacts)
        if not content:
            continue

        chapter_num += 1

        # Separator mode: derive title from content's first sentence
        if mode == "separator":
            title = _derive_separator_title(chapter_num, content)

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


# Sentence-ending punctuation for title truncation
_SENT_END_RE = re.compile(r'[。！？…」』）\n]')


def _derive_separator_title(chapter_num: int, content: str) -> str:
    """Derive a chapter title from the first sentence of separator-split content.

    For novels that use --- separators without explicit chapter headings,
    extract the first sentence (up to ~40 chars) as a summary title.
    """
    # Take the first non-blank line
    first_line = ""
    for line in content.split("\n"):
        s = line.strip()
        if s:
            first_line = s
            break
    if not first_line:
        return f"第 {chapter_num} 章"

    # Truncate at first sentence-ending punctuation, max 40 chars
    m = _SENT_END_RE.search(first_line)
    if m and m.start() <= 40:
        title = first_line[: m.start() + 1]
    elif len(first_line) <= 40:
        title = first_line
    else:
        title = first_line[:38] + "…"

    return title


def _extract_title(mode: str, match: re.Match) -> str:
    """Extract a clean chapter title from a regex match."""
    if mode == "separator":
        return ""  # Placeholder — overridden in _split_by_matches

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

    # section_zh: always include the marker prefix (第X部/回/节 etc.) for clarity
    if mode == "section_zh":
        return match.group(0).strip()

    # For chapter_zh, markdown: group 1 is the title (subtitle after marker)
    title = match.group(1).strip() if match.group(1) else ""
    if title:
        return title

    # chapter_zh with 第X部 prefix: extract just the chapter marker
    # e.g. "第二部 不夜之候 第一章" → "第一章"
    full = match.group(0).strip()
    if mode == "chapter_zh":
        ch_marker = re.search(r"第[零〇一二两三四五六七八九十百千万\d]+章", full)
        if ch_marker and ch_marker.start() > 0:
            return ch_marker.group(0)

    # Fallback: use the entire matched line
    return full


def _assign_volumes(text: str, chapters: list[ChapterInfo]) -> None:
    """Detect volume markers in text and assign volume info to chapters.

    Finds all volume markers (第X卷/部/集) in the original text,
    then assigns each chapter to the appropriate volume based on text position.
    Also strips volume marker lines from chapter content.
    """
    vol_matches = list(_VOLUME_PATTERN.finditer(text))
    if not vol_matches:
        return

    # If both 卷X and 第X部 exist, prefer 卷X for volume splitting
    juan_matches = [m for m in vol_matches if m.group('vol_name').startswith('卷')]
    if juan_matches and len(juan_matches) < len(vol_matches):
        vol_matches = juan_matches

    # Build volume list sorted by position: (start_pos, vol_num, vol_title)
    # Deduplicate: same vol_name appearing multiple times gets the same vol_num
    # Use the volume marker name (第X部) as title; append subtitle if it's not
    # a chapter header (avoid "第一部 第一章" → title "第一部 第一章")
    _ch_header_re = re.compile(r"^第[零〇一二两三四五六七八九十百千万\d]+[章回节]")
    volumes = []
    vol_name_to_num: dict[str, int] = {}
    vol_counter = 0
    for m in vol_matches:
        vol_name = m.group("vol_name")
        subtitle = (m.group(2) or "").strip()
        if subtitle and _ch_header_re.match(subtitle):
            subtitle = ""  # trailing text is a chapter header, not a subtitle
        if len(subtitle) > 50:
            subtitle = ""  # subtitle too long, likely captured prose
        # Assign vol_num: same vol_name reuses the same number
        if vol_name not in vol_name_to_num:
            vol_counter += 1
            vol_name_to_num[vol_name] = vol_counter
        vol_num = vol_name_to_num[vol_name]
        title = f"{vol_name} {subtitle}".strip() if subtitle else vol_name
        volumes.append((m.start(), vol_num, title))

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


def _dedup_adjacent_chapters(chapters: list[ChapterInfo]) -> list[ChapterInfo]:
    """Merge adjacent chapters whose titles are identical after whitespace normalization.

    Some TXT sources contain duplicate chapter headers (e.g., full-width vs half-width
    spaces). When two consecutive chapters have the same normalized title, merge the
    second chapter's content into the first and drop the duplicate.
    """
    if len(chapters) < 2:
        return chapters

    def _norm(title: str) -> str:
        # Collapse all whitespace variants (full-width space, tabs, etc.) into single space
        return re.sub(r"[\s\u3000]+", " ", title).strip()

    result: list[ChapterInfo] = [chapters[0]]
    for ch in chapters[1:]:
        prev = result[-1]
        if _norm(prev.title) == _norm(ch.title):
            # Merge: append content, update word count
            merged_content = prev.content + "\n\n" + ch.content
            prev.content = merged_content
            prev.word_count = len(merged_content)
        else:
            result.append(ch)

    # Renumber chapters sequentially
    for i, ch in enumerate(result):
        ch.chapter_num = i + 1

    return result


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
        if _is_url_like(stripped):
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
    prologue_text = _restore_paragraphs("\n".join(prologue_lines).strip())
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
        content_text = _restore_paragraphs("\n".join(lines[content_start:content_end]).strip())
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
        chunk_start = best_break + 1  # Skip past the \n

    return chapters if chapters else [
        ChapterInfo(chapter_num=1, title="第 1 段", content=text.strip(), word_count=len(text.strip()))
    ]
