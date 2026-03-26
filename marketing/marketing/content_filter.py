"""内容安全过滤 — 敏感词检测"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from marketing.config import get_config
from marketing.logger import get_logger

log = get_logger("filter")


@dataclass
class FilterHit:
    """敏感词命中"""
    word: str
    category: str
    position: int
    context: str  # 命中位置前后的上下文


@dataclass
class FilterResult:
    """过滤结果"""
    passed: bool
    hits: list[FilterHit] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.passed:
            return "通过"
        categories = set(h.category for h in self.hits)
        return f"命中 {len(self.hits)} 个敏感词 (类别: {', '.join(categories)})"


class ContentFilter:
    """内容安全过滤器"""

    def __init__(self) -> None:
        self._wordlists: dict[str, set[str]] = {}
        self._loaded = False

    def _load_wordlists(self) -> None:
        """加载敏感词库"""
        if self._loaded:
            return

        cfg = get_config()
        filter_cfg = cfg.get("content_filter", {})

        categories = {
            "political": filter_cfg.get("political_words", ""),
            "competitive": filter_cfg.get("competitive_words", ""),
            "copyright": filter_cfg.get("copyright_words", ""),
        }

        for category, filepath in categories.items():
            if not filepath:
                continue
            path = Path(filepath)
            if path.exists():
                words = set()
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        words.add(line)
                self._wordlists[category] = words
                log.info("加载敏感词库 [%s]: %d 词", category, len(words))
            else:
                log.warning("敏感词库不存在: %s", path)

        self._loaded = True

    def scan(self, text: str) -> FilterResult:
        """扫描文本中的敏感词"""
        self._load_wordlists()

        hits: list[FilterHit] = []
        text_lower = text.lower()

        for category, words in self._wordlists.items():
            for word in words:
                pos = text_lower.find(word.lower())
                while pos >= 0:
                    # 提取上下文 (前后 20 字)
                    start = max(0, pos - 20)
                    end = min(len(text), pos + len(word) + 20)
                    context = text[start:end]

                    hits.append(FilterHit(
                        word=word,
                        category=category,
                        position=pos,
                        context=context,
                    ))
                    pos = text_lower.find(word.lower(), pos + 1)

        return FilterResult(passed=len(hits) == 0, hits=hits)


# 模块级单例
_filter = ContentFilter()


def check_content(text: str) -> FilterResult:
    """检查内容是否安全"""
    return _filter.scan(text)
