"""Tests for source-language adapters used by analysis prompts."""

from src.extraction.chapter_fact_extractor import ChapterFactExtractor
from src.extraction.scene_llm_extractor import SceneLLMExtractor
from src.extraction.source_language_adapter import get_source_language_adapter


class _DummyLLM:
    pass


def test_auto_source_language_uses_existing_chinese_adapter():
    adapter = get_source_language_adapter("auto")

    assert adapter.id == "zh-CN"
    assert adapter.system_fragment == ""
    assert adapter.use_default_examples is True


def test_vietnamese_chapter_prompt_uses_language_adapter():
    extractor = ChapterFactExtractor(llm=_DummyLLM())

    prompt = extractor._build_user_prompt(
        chapter_id=1,
        chapter_text="Trần Quốc Tuấn đến bến Chương Dương.",
        example_text=extractor._build_example_text("vi"),
        source_language="vi",
    )

    assert extractor._build_example_text("vi") == ""
    assert "## Chương 1" in prompt
    assert "Trần Quốc Tuấn" in prompt


def test_vietnamese_scene_prompt_preserves_source_language_guidance():
    extractor = SceneLLMExtractor(llm=_DummyLLM())

    system = extractor._build_system_prompt(
        characters=["Trần Quốc Tuấn"],
        locations=["bến Chương Dương"],
        source_language="vi",
    )
    user_prompt = extractor._build_user_prompt(
        2,
        "【P0】Trần Quốc Tuấn đứng bên bến Chương Dương.",
        source_language="vi",
    )

    assert "越南语" in system
    assert "Trần Quốc Tuấn" in system
    assert "bến Chương Dương" in system
    assert user_prompt.startswith("## Chương 2")
