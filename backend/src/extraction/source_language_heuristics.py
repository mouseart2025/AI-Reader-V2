"""Source-language heuristic adapters for pre-scan and analysis guards."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from src.models.entity_dict import EntityDictEntry
from src.utils.source_language import DEFAULT_SOURCE_LANGUAGE, SourceLanguage, normalize_source_language

ContextExtractor = Callable[[str, str], str | None]


@dataclass(frozen=True)
class SourceLanguageHeuristics:
    """Feature switches for language-sensitive heuristic code."""

    id: SourceLanguage
    uses_chinese_prescan: bool = False
    uses_chinese_name_corrections: bool = False
    uses_llm_prescan_classifier: bool = False
    supports_prescan: bool = False


_ZH_HEURISTICS = SourceLanguageHeuristics(
    id="zh-CN",
    uses_chinese_prescan=True,
    uses_chinese_name_corrections=True,
    uses_llm_prescan_classifier=True,
    supports_prescan=True,
)
_VI_HEURISTICS = SourceLanguageHeuristics(id="vi", supports_prescan=True)
_EN_HEURISTICS = SourceLanguageHeuristics(id="en")


def get_source_language_heuristics(value: str | None) -> SourceLanguageHeuristics:
    """Return heuristic switches for the given source language."""

    source_language = normalize_source_language(value)
    if source_language == "auto":
        source_language = DEFAULT_SOURCE_LANGUAGE
    if source_language == "vi":
        return _VI_HEURISTICS
    if source_language == "en":
        return _EN_HEURISTICS
    return _ZH_HEURISTICS


_VI_UPPER = "A-ZÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬĐÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴ"
_VI_LOWER = "a-zàáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ"
_VI_CAP_WORD = rf"[{_VI_UPPER}][{_VI_LOWER}]+"
_VI_NAME_PATTERN = rf"{_VI_CAP_WORD}(?:\s+{_VI_CAP_WORD}){{1,3}}"

_VI_INITIAL_STOPWORDS = frozenset({
    "Ai", "Bấy", "Bởi", "Các", "Cho", "Có", "Còn", "Cuối", "Dẫu", "Dưới",
    "Đang", "Đây", "Đó", "Đêm", "Đến", "Đi", "Điều", "Đoạn", "Hôm", "Khi",
    "Không", "Lúc", "Một", "Nếu", "Ngày", "Ngoài", "Nhưng", "Rồi", "Sau",
    "Sáng", "Tại", "Theo", "Trong", "Trên", "Trước", "Vì", "Với",
})

_VI_TITLE_WORDS = frozenset({
    "Ông", "Bà", "Cô", "Cậu", "Chú", "Bác", "Thầy", "Sư", "Quan", "Tướng",
    "Vua", "Chúa", "Hoàng", "Công",
})

_VI_LOCATION_KEYWORDS = (
    "kinh đô", "thành", "làng", "bản", "xóm", "núi", "sông", "suối", "hồ",
    "đầm", "biển", "bến", "cảng", "cầu", "chùa", "đền", "miếu", "phủ",
    "quán", "đình", "cửa biển", "ải", "thung lũng",
)
_VI_ORG_KEYWORDS = (
    "triều đình", "nghĩa quân", "quân", "đội", "hội", "nhà", "phái",
    "giáo phái", "bang", "bộ tộc", "dòng họ",
)
_VI_PERSON_TITLES = (
    "Tướng quân", "Công chúa", "Hoàng tử", "Ông", "Bà", "Cô", "Cậu", "Chú",
    "Bác", "Thầy", "Sư", "Quan", "Tướng", "Vua", "Chúa",
)
_VI_DIALOGUE_VERBS = (
    "nói", "hỏi", "đáp", "thưa", "bảo", "gọi", "kêu", "mắng", "quát",
    "cười", "than", "nhắc", "dặn",
)
_VI_NAMING_PHRASES = (
    "tên là", "tên thật là", "gọi là", "hiệu là", "biệt danh là", "xưng là",
    "tự xưng là", "tức là",
)


def _keyword_pattern(words: tuple[str, ...]) -> str:
    variants: list[str] = []
    for word in sorted(words, key=len, reverse=True):
        variants.append(re.escape(word))
        variants.append(re.escape(word[:1].upper() + word[1:]))
    return "|".join(dict.fromkeys(variants))


_VI_LOCATION_PATTERN = re.compile(
    rf"(?<![\w])(?P<keyword>{_keyword_pattern(_VI_LOCATION_KEYWORDS)})\s+"
    rf"(?P<name>{_VI_CAP_WORD}(?:\s+{_VI_CAP_WORD}){{0,3}})"
)
_VI_ORG_PATTERN = re.compile(
    rf"(?<![\w])(?P<keyword>{_keyword_pattern(_VI_ORG_KEYWORDS)})\s+"
    rf"(?P<name>{_VI_CAP_WORD}(?:\s+{_VI_CAP_WORD}){{0,3}})"
)
_VI_PERSON_TITLE_PATTERN = re.compile(
    rf"(?<![\w])(?:{_keyword_pattern(_VI_PERSON_TITLES)})\s+(?P<name>{_VI_NAME_PATTERN})"
)
_VI_DIALOGUE_AFTER_PATTERN = re.compile(
    rf"[\"“”](?:[^\"“”]{{1,200}})[\"“”]\s*,?\s*"
    rf"(?P<name>{_VI_NAME_PATTERN})\s+(?:{'|'.join(_VI_DIALOGUE_VERBS)})\b"
)
_VI_DIALOGUE_BEFORE_PATTERN = re.compile(
    rf"(?<![\w])(?P<name>{_VI_NAME_PATTERN})\s+"
    rf"(?:{'|'.join(_VI_DIALOGUE_VERBS)})\s*[:：,]"
)
_VI_NAMING_PATTERN = re.compile(
    rf"(?:{_keyword_pattern(_VI_NAMING_PHRASES)})\s+(?P<name>{_VI_NAME_PATTERN})"
)
_VI_PROPER_NAME_PATTERN = re.compile(rf"(?<![\w])(?P<name>{_VI_NAME_PATTERN})(?![\w])")


def scan_vietnamese_prescan_candidates(
    chapters: list[str],
    titles: list[str],
    full_text: str,
    sample_context: ContextExtractor,
) -> list[EntityDictEntry]:
    """Build a small Vietnamese entity dictionary without Chinese NLP tools."""

    counters: dict[str, Counter] = {
        "proper": Counter(),
        "title": Counter(),
        "dialogue": Counter(),
        "naming": Counter(),
        "location": Counter(),
        "org": Counter(),
    }
    entity_types: dict[str, str] = {}

    def add(name: str, source: str, entity_type: str = "unknown", count: int = 1) -> None:
        cleaned = _clean_vi_name(name)
        if not cleaned:
            return
        counters[source][cleaned] += count
        if entity_type != "unknown":
            entity_types[cleaned] = entity_type

    for pattern in (_VI_LOCATION_PATTERN,):
        for match in pattern.finditer(full_text):
            keyword = match.group("keyword").lower()
            add(f"{keyword} {match.group('name')}", "location", "location")

    for match in _VI_ORG_PATTERN.finditer(full_text):
        keyword = match.group("keyword").lower()
        add(f"{keyword} {match.group('name')}", "org", "org")

    for match in _VI_PERSON_TITLE_PATTERN.finditer(full_text):
        add(match.group("name"), "title", "person")

    for pattern in (_VI_DIALOGUE_AFTER_PATTERN, _VI_DIALOGUE_BEFORE_PATTERN):
        for match in pattern.finditer(full_text):
            add(match.group("name"), "dialogue", "person")

    for match in _VI_NAMING_PATTERN.finditer(full_text):
        add(match.group("name"), "naming", "person")

    title_text = "\n".join(titles)
    for match in _VI_PROPER_NAME_PATTERN.finditer(title_text):
        add(match.group("name"), "title")

    scan_text = full_text[:500_000] if len(full_text) > 500_000 else full_text
    for match in _VI_PROPER_NAME_PATTERN.finditer(scan_text):
        add(match.group("name"), "proper")

    all_names = set().union(*(counter.keys() for counter in counters.values()))
    typed_names = {name for name in all_names if entity_types.get(name, "unknown") != "unknown"}

    entries: list[EntityDictEntry] = []
    for name in all_names:
        source, frequency, confidence = _score_vi_candidate(name, counters)
        if not source:
            continue
        entity_type = entity_types.get(name, "unknown")
        if entity_type == "unknown" and any(name in typed and name != typed for typed in typed_names):
            continue
        entries.append(EntityDictEntry(
            name=name,
            entity_type=entity_type,
            frequency=frequency,
            confidence=confidence,
            aliases=[],
            source=f"vi_{source}",
            sample_context=sample_context(name, full_text),
        ))

    entries.sort(key=lambda item: (item.confidence != "high", -item.frequency, item.name))
    return entries[:500]


def _clean_vi_name(name: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", name.strip(" \t\r\n.,;:!?()[]{}\"'“”‘’"))
    if not cleaned:
        return None
    parts = cleaned.split()
    if len(parts) > 5:
        return None
    if parts[0] in _VI_INITIAL_STOPWORDS:
        return None
    if len(parts) == 1 and parts[0] in _VI_TITLE_WORDS:
        return None
    return cleaned


def _score_vi_candidate(name: str, counters: dict[str, Counter]) -> tuple[str | None, int, str]:
    priority = ("naming", "dialogue", "location", "org", "title", "proper")
    frequencies = {source: counter.get(name, 0) for source, counter in counters.items()}
    frequency = max(frequencies.values())
    source = next((key for key in priority if frequencies.get(key, 0)), None)
    if not source:
        return None, 0, "low"

    if source in {"naming", "dialogue", "location", "org", "title"}:
        return source, max(1, frequency), "high"
    if frequency >= 2:
        return source, frequency, "medium"
    return None, frequency, "low"
