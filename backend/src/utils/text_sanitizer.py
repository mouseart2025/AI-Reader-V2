"""Text hygiene detection and cleaning for uploaded novels.

Detects 5 categories of noise commonly found in web-scraped TXT novels:
1. URLs (http/https/www/domain suffixes)
2. Promo (WeChat/QQ/app download/subscription prompts)
3. Template (site boilerplate like "本书由...整理")
4. Decoration (repeated separator lines: -----, =====, ※※※※)
5. Repeated tail (identical lines appearing at the end of 50%+ chapters)
"""

import re
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class SuspectLine:
    """A single line flagged as noise."""
    line_num: int
    content: str
    category: str  # url, promo, template, decoration, repeated
    confidence: float  # 0.0 - 1.0


@dataclass
class NoiseReport:
    """Internal report on detected noise."""
    total_suspect_lines: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    samples: list[SuspectLine] = field(default_factory=list)
    all_suspects: list[SuspectLine] = field(default_factory=list)


# ── URL patterns ──
_URL_RE = re.compile(
    r"(?:https?://|www\.)\S+|"
    r"\S+\.(?:com|cn|net|org|cc|me|io|xyz|top|vip|club)\b",
    re.IGNORECASE,
)

# ── Promo keywords ──
_PROMO_KEYWORDS = [
    "公众号", "微信", "QQ群", "qq群", "QQ 群",
    "关注", "订阅", "书友群", "读者群",
    "下载APP", "下载app", "下载 APP",
    "扫码", "二维码", "加群", "入群",
    "微信号", "微信公众", "公号",
    "百度搜索", "搜索引擎",
    "WeChat", "wechat",
]
_PROMO_RE = re.compile("|".join(re.escape(k) for k in _PROMO_KEYWORDS), re.IGNORECASE)

# ── Template patterns ──
_TEMPLATE_PATTERNS = [
    re.compile(r"本书由.{1,20}整理"),
    re.compile(r"更多.{1,30}(?:请访问|请到|请搜索)"),
    re.compile(r"手机用户请到.{1,30}阅读"),
    re.compile(r"本(?:文|书|章)来自"),
    re.compile(r"(?:免费|正版).{0,10}(?:小说|阅读)"),
    re.compile(r"(?:转载|搬运).{0,10}(?:请注明|出处)"),
    re.compile(r"(?:起点|纵横|17k|晋江|红袖|潇湘|飞卢|番茄).{0,20}(?:首发|原创|独家|连载)"),
    re.compile(r"(?:txt|TXT).{0,10}(?:下载|全集|全本)"),
    re.compile(r"(?:新书|新作).{0,10}(?:求收藏|求推荐|求月票|求打赏)"),
    re.compile(r"(?:收藏|推荐|月票|打赏).{0,5}(?:感谢|谢谢|多谢)"),
]

# ── Decoration patterns ──
_DECORATION_RE = re.compile(
    r"^[\s]*[-=*#~_※◆◇■□●○▲△▼▽☆★♦♣♠♥]{5,}\s*$"
)


def _detect_urls(lines: list[str]) -> list[SuspectLine]:
    """Detect lines containing URLs."""
    results = []
    for i, line in enumerate(lines):
        if _URL_RE.search(line):
            results.append(SuspectLine(
                line_num=i + 1,
                content=line.strip()[:100],
                category="url",
                confidence=0.9,
            ))
    return results


def _detect_promo(lines: list[str]) -> list[SuspectLine]:
    """Detect promotional content."""
    results = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) > 200:
            continue
        if _PROMO_RE.search(stripped):
            results.append(SuspectLine(
                line_num=i + 1,
                content=stripped[:100],
                category="promo",
                confidence=0.85,
            ))
    return results


def _detect_template(lines: list[str]) -> list[SuspectLine]:
    """Detect site template/boilerplate lines."""
    results = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) > 200:
            continue
        for pat in _TEMPLATE_PATTERNS:
            if pat.search(stripped):
                results.append(SuspectLine(
                    line_num=i + 1,
                    content=stripped[:100],
                    category="template",
                    confidence=0.8,
                ))
                break
    return results


def _detect_decoration(lines: list[str]) -> list[SuspectLine]:
    """Detect decorative separator lines."""
    results = []
    for i, line in enumerate(lines):
        if _DECORATION_RE.match(line):
            results.append(SuspectLine(
                line_num=i + 1,
                content=line.strip()[:100],
                category="decoration",
                confidence=0.7,
            ))
    return results


def _detect_repeated_tails(text: str, chapters: list | None = None) -> list[SuspectLine]:
    """Detect repeated tail lines across chapters.

    If chapters are provided, checks the last 5 lines of each chapter.
    Otherwise, splits text by double newlines as a rough proxy.
    """
    # Split into rough "sections" if no chapters provided
    if chapters is None:
        sections = [s.strip() for s in text.split("\n\n\n") if s.strip()]
    else:
        sections = [ch.content if hasattr(ch, "content") else str(ch) for ch in chapters]

    if len(sections) < 4:
        return []

    # Collect tail lines from each section
    tail_counter: Counter[str] = Counter()
    for section in sections:
        section_lines = section.strip().split("\n")
        tail = section_lines[-5:] if len(section_lines) >= 5 else section_lines
        for line in tail:
            stripped = line.strip()
            if stripped and len(stripped) > 3:
                tail_counter[stripped] += 1

    # Lines appearing in >= 50% of sections are "repeated tails"
    threshold = len(sections) * 0.5
    repeated_lines = {line for line, count in tail_counter.items() if count >= threshold}

    if not repeated_lines:
        return []

    # Find all occurrences in the full text
    results = []
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped in repeated_lines:
            results.append(SuspectLine(
                line_num=i + 1,
                content=stripped[:100],
                category="repeated",
                confidence=0.75,
            ))

    return results


def detect_noise(text: str, chapters: list | None = None) -> NoiseReport:
    """Run all noise detection passes on text.

    Returns a NoiseReport with all suspect lines.
    """
    lines = text.split("\n")

    all_suspects: list[SuspectLine] = []
    all_suspects.extend(_detect_urls(lines))
    all_suspects.extend(_detect_promo(lines))
    all_suspects.extend(_detect_template(lines))
    all_suspects.extend(_detect_decoration(lines))
    all_suspects.extend(_detect_repeated_tails(text, chapters))

    # Deduplicate by line number (keep highest confidence)
    by_line: dict[int, SuspectLine] = {}
    for s in all_suspects:
        if s.line_num not in by_line or s.confidence > by_line[s.line_num].confidence:
            by_line[s.line_num] = s
    deduped = sorted(by_line.values(), key=lambda s: s.line_num)

    # Build category counts
    by_category: dict[str, int] = {}
    for s in deduped:
        by_category[s.category] = by_category.get(s.category, 0) + 1

    return NoiseReport(
        total_suspect_lines=len(deduped),
        by_category=by_category,
        samples=deduped[:10],
        all_suspects=deduped,
    )


def clean_text(text: str, report: NoiseReport, mode: str = "conservative") -> str:
    """Remove noise lines from text.

    Args:
        text: Original text.
        report: NoiseReport from detect_noise().
        mode: "conservative" (only confidence >= 0.8) or "aggressive" (all suspects).

    Returns:
        Cleaned text with noise lines removed.
    """
    if not report.all_suspects:
        return text

    threshold = 0.8 if mode == "conservative" else 0.0
    lines_to_remove = {s.line_num for s in report.all_suspects if s.confidence >= threshold}

    if not lines_to_remove:
        return text

    lines = text.split("\n")
    cleaned = [line for i, line in enumerate(lines) if (i + 1) not in lines_to_remove]
    return "\n".join(cleaned)
