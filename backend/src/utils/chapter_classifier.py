"""Non-content chapter classifier.

Detects chapters that are likely not part of the novel's main narrative
(e.g., introductions, author bios, literary reviews, appendices, publishing
metadata) based on title keywords, content features, and position heuristics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.utils.chapter_splitter import ChapterInfo

# ── Title keyword patterns ────────────────────────────────────

# Titles that are almost certainly non-content
_TITLE_STRONG: set[str] = {
    "作者简介", "作者介绍", "关于作者", "作品简介", "内容简介", "内容提要",
    "出版说明", "出版后记", "编者的话", "编者按", "编辑推荐",
    "附录", "参考文献", "注释", "版权声明", "版权信息",
    "创作谈", "创作手记", "写作手记",
    "书评", "读后感", "推荐序", "代序",
    "纪念", "悼念", "追忆",
    "目录", "总目录",
}

# Titles that are suspect when combined with other signals
_TITLE_SUSPECT_KEYWORDS: list[str] = [
    "序", "前言", "引言", "导读", "导语", "楔子",
    "后记", "尾声", "完本感言", "完结感言", "结语", "跋",
    "番外", "特别篇",
]

# ── Content keyword patterns ──────────────────────────────────

# Publishing/metadata keywords — strong indicator of non-content
_CONTENT_PUBLISHING_KEYWORDS: list[str] = [
    "ISBN", "出版社", "出版日期", "印刷", "字数：", "定价",
    "版权所有", "版权归", "转载请", "未经许可",
    "责任编辑", "封面设计", "排版", "校对",
]

# Literary criticism keywords — moderate indicator
_CONTENT_CRITICISM_KEYWORDS: list[str] = [
    "作者以", "小说以", "本书以", "作品以",
    "叙事手法", "文学价值", "创作背景",
    "茅盾文学奖", "鲁迅文学奖", "诺贝尔",
    "文学史", "文学评论", "文学批评",
    "读者评", "豆瓣评分",
]

# Dialogue markers — their ABSENCE suggests non-narrative content
_DIALOGUE_MARKERS = re.compile(r'[""「」『』]')
_DIALOGUE_LINE = re.compile(r'^[""「]', re.MULTILINE)

# ── Thresholds ────────────────────────────────────────────────

_MAX_SUSPECT_PROLOGUE_WORDS = 3000  # 序章 < 3000 字 → suspect
_MIN_DIALOGUE_RATIO = 0.03  # < 3% dialogue lines → suspect


@dataclass
class ClassifyResult:
    """Classification result for a single chapter."""
    is_suspect: bool
    reason: str = ""


def classify_chapters(chapters: list[ChapterInfo]) -> list[bool]:
    """Classify each chapter as suspect non-content (True) or likely content (False).

    Pure function — no DB, no network, no side effects.
    Returns a list of booleans aligned with *chapters*.
    """
    if not chapters:
        return []

    total = len(chapters)
    results: list[bool] = []

    for i, ch in enumerate(chapters):
        result = _classify_single(ch, index=i, total=total)
        results.append(result.is_suspect)

    return results


def classify_chapters_detailed(chapters: list[ChapterInfo]) -> list[ClassifyResult]:
    """Like classify_chapters but returns detailed reasons."""
    if not chapters:
        return []

    total = len(chapters)
    return [_classify_single(ch, index=i, total=total) for i, ch in enumerate(chapters)]


def _classify_single(ch: ChapterInfo, index: int, total: int) -> ClassifyResult:
    """Classify a single chapter."""
    title = ch.title.strip()
    content = ch.content
    word_count = ch.word_count

    # ── Rule 1: Strong title match ────────────────────────
    title_clean = title.replace(" ", "").replace("　", "")
    if title_clean in _TITLE_STRONG:
        return ClassifyResult(True, f"标题强匹配: {title}")

    # Partial match for strong keywords
    for kw in _TITLE_STRONG:
        if kw in title_clean:
            return ClassifyResult(True, f"标题包含: {kw}")

    # ── Rule 2: Publishing metadata in content ────────────
    pub_hits = sum(1 for kw in _CONTENT_PUBLISHING_KEYWORDS if kw in content)
    if pub_hits >= 2:
        return ClassifyResult(True, f"出版元数据关键词命中 {pub_hits} 个")

    # ── Rule 3: Auto-generated prologue "序章" ────────────
    # chapter_splitter generates "序章" for text before the first chapter marker.
    # These are often book introductions, not actual story prologues.
    if index == 0 and title == "序章" and word_count < _MAX_SUSPECT_PROLOGUE_WORDS:
        # Check if it reads like narrative (has dialogue) or like an introduction
        if not _has_meaningful_dialogue(content):
            return ClassifyResult(True, "自动序章且无对话内容")

    # ── Rule 4: Suspect title at document boundaries ──────
    is_head = index <= 1  # first 2 chapters
    is_tail = index >= total - 2  # last 2 chapters

    for kw in _TITLE_SUSPECT_KEYWORDS:
        if kw in title_clean:
            # Head/tail chapters with suspect titles need content check
            if is_head or is_tail:
                if not _has_meaningful_dialogue(content):
                    return ClassifyResult(True, f"边界章节标题含「{kw}」且无对话")
            break  # Only check once per chapter

    # ── Rule 5: Literary criticism content ────────────────
    crit_hits = sum(1 for kw in _CONTENT_CRITICISM_KEYWORDS if kw in content)
    if crit_hits >= 3:
        return ClassifyResult(True, f"文学评论关键词命中 {crit_hits} 个")

    # ── Rule 6: Very short first/last chapter with no dialogue ──
    if (is_head or is_tail) and word_count < 500 and not _has_meaningful_dialogue(content):
        return ClassifyResult(True, "边界极短章节且无对话")

    return ClassifyResult(False)


def _has_meaningful_dialogue(content: str) -> bool:
    """Check if content has meaningful dialogue (narrative indicator).

    Returns True if dialogue markers appear in at least MIN_DIALOGUE_RATIO
    of lines, suggesting this is narrative prose rather than commentary.
    """
    lines = content.split("\n")
    if not lines:
        return False

    dialogue_lines = sum(1 for line in lines if _DIALOGUE_LINE.search(line.strip()))
    total_lines = len([l for l in lines if l.strip()])
    if total_lines == 0:
        return False

    ratio = dialogue_lines / total_lines
    return ratio >= _MIN_DIALOGUE_RATIO
