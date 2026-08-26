"""FR-3.1 证据 span 落库测试:EventFact.evidence 兼容、证据锚定清洗、开关回退。

全部使用 mock LLM,不打真实 API。
验收口径:新抽取的关系/事件 ≥90% 带非空 evidence,且 span 能在原章节文本中
定位(子串匹配,允许 normalize 空白)。
"""

from __future__ import annotations

import logging

import pytest

from src.extraction import chapter_fact_extractor as cfe
from src.extraction.chapter_fact_extractor import (
    ChapterFactExtractor,
    _sanitize_evidence_grounding,
    span_located,
)
from src.infra import config
from src.infra.llm_client import LlmUsage
from src.models.chapter_fact import ChapterFact, EventFact, RelationshipFact

# 章节原文,evidence span 均出自此文本
CHAPTER_TEXT = (
    "宋江与武松在柴进庄上结拜为义兄弟。两人把酒言欢,结为生死之交。\n"
    "次日,宋江辞别柴进,独自上路投青州去了。"
)


class MockLLM:
    """Mock LLM:返回固定的 chapter fact 响应。"""

    def __init__(self, fact_response: dict):
        self.fact_response = fact_response
        self.prompts: list[str] = []
        self.systems: list[str] = []

    async def generate(self, system, prompt, format=None, temperature=0.1,
                       max_tokens=4096, timeout=120, num_ctx=None):
        self.prompts.append(prompt)
        self.systems.append(system)
        return self.fact_response, LlmUsage(100, 50, 150)


def _fact_response(relationships: list[dict], events: list[dict]) -> dict:
    return {
        "chapter_id": 1,
        "novel_id": "test-novel",
        "characters": [{"name": "宋江"}, {"name": "武松"}, {"name": "柴进"}],
        "relationships": relationships,
        "locations": [{"name": "柴进庄", "type": "庄园"}],
        "events": events,
    }


def _rel(evidence: str = "宋江与武松在柴进庄上结拜为义兄弟") -> dict:
    return {
        "person_a": "宋江",
        "person_b": "武松",
        "relation_type": "结拜兄弟",
        "evidence": evidence,
    }


def _event(evidence: str = "宋江辞别柴进,独自上路投青州去了") -> dict:
    return {
        "summary": "宋江辞别柴进独自上路",
        "type": "旅行",
        "importance": "medium",
        "participants": ["宋江", "柴进"],
        "location": "柴进庄",
        "evidence": evidence,
    }


@pytest.fixture
def evidence_on(monkeypatch):
    """只开证据锚定,关闭维度功能以隔离被测行为。"""
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", True)
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", False)
    monkeypatch.setattr(config, "RELATION_SUBTYPE_VOTE_SAMPLES", 1)


@pytest.fixture
def evidence_off(monkeypatch):
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", False)
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", False)
    monkeypatch.setattr(config, "RELATION_SUBTYPE_VOTE_SAMPLES", 1)


# ── 模型兼容(NFR-3)──


def test_event_fact_legacy_json_deserializes_with_empty_evidence():
    """旧 JSON(无 evidence 字段)反序列化不变,默认 ""。"""
    ev = EventFact.model_validate({"summary": "石猴出世", "type": "成长"})
    assert ev.evidence == ""
    assert ev.importance == "medium"


def test_event_fact_evidence_round_trip():
    ev = EventFact(summary="石猴出世", type="成长", evidence="仙石迸裂")
    ev2 = EventFact.model_validate(ev.model_dump())
    assert ev2 == ev
    assert ev2.evidence == "仙石迸裂"


# ── 验收:≥90% 带非空 evidence 且 span 可定位 ──


@pytest.mark.asyncio
async def test_extraction_evidence_coverage_and_locatability(evidence_on):
    """合规 mock 下,新抽取关系/事件 100% 带非空 evidence 且可在原文定位。"""
    llm = MockLLM(_fact_response([_rel() for _ in range(5)],
                                 [_event() for _ in range(5)]))
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, meta = await extractor.extract("test-novel", 1, CHAPTER_TEXT)

    records = list(fact.relationships) + list(fact.events)
    with_evidence = [r for r in records if r.evidence.strip()]
    assert len(with_evidence) / len(records) >= 0.9
    for r in with_evidence:
        assert span_located(r.evidence, CHAPTER_TEXT)
    assert meta.evidence_missing_relations == 0
    assert meta.evidence_missing_events == 0
    assert meta.evidence_unlocated_spans == 0


# ── 无证据:降置信度并标记 ──


@pytest.mark.asyncio
async def test_missing_evidence_marked_and_downgraded(evidence_on, caplog):
    rels = [_rel()] + [_rel(evidence="") for _ in range(2)]
    events = [_event(), _event(evidence="")]
    llm = MockLLM(_fact_response(rels, events))
    extractor = ChapterFactExtractor(llm=llm)
    with caplog.at_level(logging.WARNING, logger="src.extraction.chapter_fact_extractor"):
        fact, _, meta = await extractor.extract("test-novel", 1, CHAPTER_TEXT)

    assert meta.evidence_missing_relations == 2
    assert meta.evidence_missing_events == 1
    # 无证据事件 importance 降一级(medium→low)
    assert fact.events[1].importance == "low"
    # 有证据事件不受影响
    assert fact.events[0].importance == "medium"
    assert "缺少 evidence" in caplog.text


def test_sanitize_downgrade_ladder():
    """high→medium→low,low 不再降。"""
    fact = ChapterFact(
        chapter_id=1, novel_id="t",
        events=[
            EventFact(summary="a", type="其他", importance="high"),
            EventFact(summary="b", type="其他", importance="medium"),
            EventFact(summary="c", type="其他", importance="low"),
        ],
    )
    _sanitize_evidence_grounding(fact, 1, "任意原文")
    assert [e.importance for e in fact.events] == ["medium", "low", "low"]


def test_unlocated_span_logged_and_counted(caplog):
    """非空但无法定位的 span:打日志 + 计数,不删改记录。"""
    fact = ChapterFact(
        chapter_id=1, novel_id="t",
        relationships=[RelationshipFact(
            person_a="甲", person_b="乙", relation_type="朋友",
            evidence="原文里根本没有这句话",
        )],
    )
    from src.extraction.chapter_fact_extractor import ExtractionMeta
    meta = ExtractionMeta()
    with caplog.at_level(logging.WARNING, logger="src.extraction.chapter_fact_extractor"):
        _sanitize_evidence_grounding(fact, 1, "完全不同的章节文本", meta)
    assert meta.evidence_unlocated_spans == 1
    assert fact.relationships[0].evidence == "原文里根本没有这句话"
    assert "无法在原文定位" in caplog.text


# ── span_located 纯函数 ──


def test_span_located_whitespace_normalized():
    text = "宋江与武松\n在柴进庄上  结拜为义兄弟。"
    assert span_located("宋江与武松在柴进庄上结拜为义兄弟", text)
    assert span_located("宋江与武松 在柴进庄上\n结拜", text)
    assert not span_located("柴进与武松结拜", text)
    assert not span_located("", text)


# ── 开关(NFR-3 回退)──


@pytest.mark.asyncio
async def test_switch_on_injects_guide_and_requirement(evidence_on):
    llm = MockLLM(_fact_response([_rel()], [_event()]))
    extractor = ChapterFactExtractor(llm=llm)
    await extractor.extract("test-novel", 1, CHAPTER_TEXT)
    assert "证据锚定规则" in llm.systems[0]
    assert "逐字引用原文" in llm.systems[0]
    assert "9. evidence" in llm.prompts[0]
    # few-shot 示例中的事件带 evidence
    assert '"evidence"' in llm.prompts[0]


@pytest.mark.asyncio
async def test_switch_off_prompt_and_parsing_unchanged(evidence_off):
    """关闭后 prompt 与 v0.73 逐字节一致,解析不做证据清洗。"""
    llm = MockLLM(_fact_response([_rel(evidence="")], [_event(evidence="")]))
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, meta = await extractor.extract("test-novel", 1, CHAPTER_TEXT)

    assert "证据锚定规则" not in llm.systems[0]
    assert "9. evidence" not in llm.prompts[0]
    # 示例中的事件 evidence 被剥离(v0.73 示例无此字段;关系 evidence 是 v0.73 既有)
    import json as _json
    example_section = llm.prompts[0].split("## 第 1 章")[0]
    block = example_section.split("```json", 1)[1].split("```", 1)[0]
    for ex in _json.loads(block):
        for ev in ex.get("events", []):
            assert "evidence" not in ev
    # 不做清洗:importance 不降级,meta 计数为零
    assert fact.events[0].importance == "medium"
    assert meta.evidence_missing_relations == 0
    assert meta.evidence_missing_events == 0


def test_schema_evidence_default_popped_only_when_on(monkeypatch):
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", True)
    schema = cfe._build_extraction_schema()
    assert "default" not in schema["$defs"]["EventFact"]["properties"]["evidence"]
    assert "default" not in schema["$defs"]["RelationshipFact"]["properties"]["evidence"]

    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", False)
    schema_off = cfe._build_extraction_schema()
    assert schema_off["$defs"]["EventFact"]["properties"]["evidence"].get("default") == ""
