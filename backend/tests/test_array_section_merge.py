"""数组形式 LLM 响应合并 + relationships 键名归一测试。

背景(可复现性 harness 实测,DeepSeek):模型在输出截断压力下把响应拆成
多个部分 ChapterFact 对象的数组,section 分散在不同元素里;旧代码只取第一个
dict 元素,导致 events/item_events/org_events/new_concepts 等 section 被
静默丢弃。修复后按 section 合并所有元素,非对象元素记 warning。

全部使用 mock LLM,不打真实 API。
"""

from __future__ import annotations

import logging

import pytest

from src.extraction.chapter_fact_extractor import (
    ChapterFactExtractor,
    ExtractionError,
    _merge_array_result,
    _normalize_field_names,
)
from src.infra import config
from src.infra.llm_client import LlmUsage

CHAPTER_TEXT = (
    "宋江与武松在柴进庄上结拜为义兄弟。两人把酒言欢,结为生死之交。\n"
    "席间柴进提起林冲风雪上梁山之事,众人唏嘘不已。\n"
    "次日,宋江辞别柴进,独自上路投青州去了。"
)


class MockLLM:
    """Mock LLM:固定返回给定的 main_response(可以是 dict 或 list)。"""

    def __init__(self, main_response):
        self.main_response = main_response

    async def generate(self, system, prompt, format=None, temperature=0.1,
                       max_tokens=4096, timeout=120, num_ctx=None):
        return self.main_response, LlmUsage(100, 50, 150)


@pytest.fixture
def switches_off(monkeypatch):
    """关闭 recall/维度/证据锚定,隔离被测行为(单遍抽取)。"""
    monkeypatch.setattr(config, "RECALL_PASS_ENABLED", False)
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", False)
    monkeypatch.setattr(config, "RELATION_SUBTYPE_VOTE_SAMPLES", 1)
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", False)


def _first_fragment() -> dict:
    """数组响应的第一个元素:characters/relationships/locations。"""
    return {
        "characters": [{"name": "宋江"}, {"name": "武松"}],
        "relationships": [
            {"person_a": "宋江", "person_b": "武松", "relation_type": "结拜兄弟"},
        ],
        "locations": [{"name": "柴进庄", "type": "庄园"}],
    }


def _second_fragment() -> dict:
    """数组响应的第二个元素:events/item_events/org_events/new_concepts。"""
    return {
        "events": [
            {
                "summary": "宋江辞别柴进独自上路",
                "type": "旅行",
                "participants": ["宋江"],
            }
        ],
        "item_events": [
            {"item_name": "戒刀", "item_type": "兵器", "action": "获得",
             "actor": "武松"},
        ],
        "org_events": [
            {"org_name": "梁山泊", "org_type": "山寨", "member": "林冲",
             "action": "加入"},
        ],
        "new_concepts": [
            {"name": "结拜", "category": "其他", "definition": "结为异姓兄弟"},
        ],
    }


# ── 数组响应:section 分散在多个元素里,合并后不丢数据 ──


@pytest.mark.asyncio
async def test_array_response_events_not_dropped(switches_off):
    """events 在第二个元素里:合并后保留(旧代码只取第一个元素会丢)。"""
    llm = MockLLM([_first_fragment(), _second_fragment()])
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)
    assert [e.summary for e in fact.events] == ["宋江辞别柴进独自上路"]


@pytest.mark.asyncio
async def test_array_response_item_events_not_dropped(switches_off):
    llm = MockLLM([_first_fragment(), _second_fragment()])
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)
    assert [i.item_name for i in fact.item_events] == ["戒刀"]
    assert fact.item_events[0].item_type == "兵器"
    assert fact.item_events[0].action == "获得"


@pytest.mark.asyncio
async def test_array_response_org_events_not_dropped(switches_off):
    llm = MockLLM([_first_fragment(), _second_fragment()])
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)
    assert [o.org_name for o in fact.org_events] == ["梁山泊"]
    assert fact.org_events[0].action == "加入"


@pytest.mark.asyncio
async def test_array_response_new_concepts_not_dropped(switches_off):
    llm = MockLLM([_first_fragment(), _second_fragment()])
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)
    assert [c.name for c in fact.new_concepts] == ["结拜"]


@pytest.mark.asyncio
async def test_array_response_first_fragment_sections_kept(switches_off):
    """第一个元素里的 characters/relationships/locations 同样保留。"""
    llm = MockLLM([_first_fragment(), _second_fragment()])
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)
    assert {ch.name for ch in fact.characters} == {"宋江", "武松"}
    assert len(fact.relationships) == 1
    assert [loc.name for loc in fact.locations] == ["柴进庄"]


@pytest.mark.asyncio
async def test_array_response_same_section_split_across_elements(switches_off):
    """同一 section 被拆到不同元素:跨元素拼接,两边都不丢。"""
    frag_a = {"events": [{"summary": "事件甲", "type": "战斗",
                          "participants": ["宋江"]}]}
    frag_b = {"events": [{"summary": "事件乙", "type": "社交",
                          "participants": ["武松"]}]}
    llm = MockLLM([frag_a, frag_b])
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)
    assert [e.summary for e in fact.events] == ["事件甲", "事件乙"]


@pytest.mark.asyncio
async def test_array_response_exact_duplicates_deduped(switches_off):
    """模型在不同元素里重复同一条目:按精确内容去重,不重复计数。"""
    frag = _second_fragment()
    llm = MockLLM([frag, dict(frag)])  # 两个元素内容完全相同
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)
    assert len(fact.events) == 1
    assert len(fact.item_events) == 1
    assert len(fact.org_events) == 1
    assert len(fact.new_concepts) == 1


@pytest.mark.asyncio
async def test_array_response_non_dict_elements_skipped_with_warning(
    switches_off, caplog,
):
    """数组中的非对象元素被跳过并记 warning,不再静默。"""
    llm = MockLLM([_first_fragment(), "garbage", None, _second_fragment()])
    extractor = ChapterFactExtractor(llm=llm)
    with caplog.at_level(logging.WARNING):
        fact, _, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)
    assert [e.summary for e in fact.events] == ["宋江辞别柴进独自上路"]
    assert any("非对象元素" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_array_response_no_dict_elements_raises(switches_off):
    """数组中没有任何 dict 元素:抛 ExtractionError(重试后仍失败)。"""
    llm = MockLLM(["garbage", 42])
    extractor = ChapterFactExtractor(llm=llm)
    with pytest.raises(ExtractionError):
        await extractor.extract("test-novel", 1, CHAPTER_TEXT)


# ── 回归:既有路径行为不变 ──


@pytest.mark.asyncio
async def test_single_element_array_regression(switches_off):
    """单元素数组:取该元素,行为与旧代码一致。"""
    llm = MockLLM([_first_fragment()])
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)
    assert {ch.name for ch in fact.characters} == {"宋江", "武松"}
    assert fact.events == []


@pytest.mark.asyncio
async def test_plain_dict_response_unchanged(switches_off):
    """既有单 dict 路径回归:正常响应原样通过。"""
    response = _first_fragment()
    response.update(_second_fragment())
    llm = MockLLM(response)
    extractor = ChapterFactExtractor(llm=llm)
    fact, usage, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)
    assert {ch.name for ch in fact.characters} == {"宋江", "武松"}
    assert [e.summary for e in fact.events] == ["宋江辞别柴进独自上路"]
    assert [i.item_name for i in fact.item_events] == ["戒刀"]
    assert [o.org_name for o in fact.org_events] == ["梁山泊"]
    assert [c.name for c in fact.new_concepts] == ["结拜"]
    assert usage.prompt_tokens == 100


# ── relationships 键名归一:source/target → person_a/person_b ──


@pytest.mark.asyncio
async def test_relationship_source_target_keys_normalized(switches_off):
    """截断压力下模型用 source/target 代替 person_a/person_b:归一后通过校验。"""
    response = _first_fragment()
    response["relationships"] = [
        {"source": "宋江", "target": "武松", "relation_type": "结拜兄弟"},
    ]
    llm = MockLLM(response)
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)
    assert len(fact.relationships) == 1
    rel = fact.relationships[0]
    assert rel.person_a == "宋江"
    assert rel.person_b == "武松"
    assert rel.relation_type == "结拜兄弟"


def test_normalize_source_not_clobbered_when_person_a_present():
    """person_a 已存在时不动 source 键(避免覆盖正常字段)。"""
    data = {
        "relationships": [
            {"person_a": "宋江", "person_b": "武松", "relation_type": "结拜兄弟",
             "source": "main"},
        ],
    }
    _normalize_field_names(data)
    rel = data["relationships"][0]
    assert rel["person_a"] == "宋江"
    assert rel["source"] == "main"


# ── _merge_array_result 单元口径 ──


def test_merge_array_result_scalar_first_non_empty_wins():
    """标量键取第一个非空值。"""
    merged = _merge_array_result([
        {"chapter_id": 0, "novel_id": ""},
        {"chapter_id": 7, "novel_id": "n1"},
    ])
    assert merged["chapter_id"] == 7  # 0 是空值,被后者的 7 覆盖
    assert merged["novel_id"] == "n1"
