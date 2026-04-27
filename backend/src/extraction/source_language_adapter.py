"""Source-language prompt adapters for analysis extraction.

These adapters keep the existing Chinese-oriented prompts untouched for
`zh-CN`, while making non-Chinese source text explicit to the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.utils.source_language import DEFAULT_SOURCE_LANGUAGE, SourceLanguage, normalize_source_language


@dataclass(frozen=True)
class SourceLanguageAdapter:
    """Small prompt adapter for one source text language."""

    id: SourceLanguage
    system_fragment: str = ""
    scene_fragment: str = ""
    chapter_heading_prefix: str = "第"
    chapter_heading_suffix: str = "章"
    segment_label_template: str = "（第 {index}/{total} 部分）"
    use_default_examples: bool = True

    def chapter_heading(self, chapter_id: int, segment_hint: str = "") -> str:
        return f"## {self.chapter_heading_prefix} {chapter_id} {self.chapter_heading_suffix}{segment_hint}"

    def segment_label(self, index: int, total: int) -> str:
        return self.segment_label_template.format(index=index, total=total)


_VI_SYSTEM_FRAGMENT = """

## 源文本语言提示
本章源文本语言是越南语。分析时必须遵守：
- 人名、地名、组织名、物品名、证据引用必须保留原文越南语和变音符号，不要翻译、拼音化或改写。
- 越南语多词姓名、尊称、别名、亲属称谓和职务称呼应作为完整称呼识别，例如 họ tên、tước hiệu、biệt danh、ông/bà/cô/cậu/chú/bác。
- 可识别 chương、hồi、phần、quyển、tập 等章节引用，以及 La Mã数字和阿拉伯数字。
- JSON 字段名、schema 结构和现有分类值保持兼容；仅实体名称、证据、摘要内容来自源文本。
"""

_VI_SCENE_FRAGMENT = """

## 源文本语言提示
章节文本是越南语。场景标题、摘要、代表性对话、人名和地名必须保留越南语原文与变音符号，不要翻译或改写实体名称。
分类字段保持兼容枚举：emotional_tone 使用 平静/紧张/悲伤/欢乐/战斗/推理/恐怖/感动；event_type 使用 对话/战斗/旅行/描写/回忆/推理/调查。
"""

_EN_SYSTEM_FRAGMENT = """

## 源文本语言提示
本章源文本语言是英语。分析时必须遵守：
- 人名、地名、组织名、物品名、证据引用必须保留原文英语拼写，不要翻译或改写。
- 可识别 Chapter、Part、Book、Volume 等章节引用。
- JSON 字段名、schema 结构和现有分类值保持兼容；仅实体名称、证据、摘要内容来自源文本。
"""

_EN_SCENE_FRAGMENT = """

## 源文本语言提示
章节文本是英语。场景标题、摘要、代表性对话、人名和地名必须保留英语原文拼写，不要翻译或改写实体名称。
分类字段保持兼容枚举：emotional_tone 使用 平静/紧张/悲伤/欢乐/战斗/推理/恐怖/感动；event_type 使用 对话/战斗/旅行/描写/回忆/推理/调查。
"""


_ADAPTERS: dict[SourceLanguage, SourceLanguageAdapter] = {
    "zh-CN": SourceLanguageAdapter(id="zh-CN"),
    "vi": SourceLanguageAdapter(
        id="vi",
        system_fragment=_VI_SYSTEM_FRAGMENT,
        scene_fragment=_VI_SCENE_FRAGMENT,
        chapter_heading_prefix="Chương",
        chapter_heading_suffix="",
        segment_label_template="（phần {index}/{total}）",
        use_default_examples=False,
    ),
    "en": SourceLanguageAdapter(
        id="en",
        system_fragment=_EN_SYSTEM_FRAGMENT,
        scene_fragment=_EN_SCENE_FRAGMENT,
        chapter_heading_prefix="Chapter",
        chapter_heading_suffix="",
        segment_label_template="（part {index}/{total}）",
        use_default_examples=False,
    ),
}


def get_source_language_adapter(value: str | None) -> SourceLanguageAdapter:
    """Return the adapter used by analysis.

    `auto` currently falls back to the existing Chinese-oriented adapter until
    language detection is added and persisted separately.
    """

    source_language = normalize_source_language(value)
    if source_language == "auto":
        source_language = DEFAULT_SOURCE_LANGUAGE
    return _ADAPTERS.get(source_language, _ADAPTERS[DEFAULT_SOURCE_LANGUAGE])
