"""FR-1.2/FR-1.3 关系维度抽取测试:维度解析(合法/非法值)、开关回退、投票多数决与平局。

全部使用 mock LLM,不打真实 API。
"""

from __future__ import annotations

import logging

import pytest

from src.extraction import chapter_fact_extractor as cfe
from src.extraction.chapter_fact_extractor import (
    CONSERVATIVE_SUBTYPE,
    ChapterFactExtractor,
    _pick_majority_subtype,
    _sanitize_relation_dimensions,
)
from src.infra import config
from src.infra.llm_client import LlmUsage
from src.models.chapter_fact import ChapterFact, RelationshipFact

VOTE_PROMPT_MARKER = "请为以下人物关系逐条判定"


class MockLLM:
    """Mock LLM: first (non-vote) call returns the chapter fact, vote calls
    return queued vote responses."""

    def __init__(self, fact_response: dict, vote_responses: list | None = None):
        self.fact_response = fact_response
        self.vote_responses = list(vote_responses or [])
        self.prompts: list[str] = []
        self.systems: list[str] = []

    async def generate(self, system, prompt, format=None, temperature=0.1,
                       max_tokens=4096, timeout=120, num_ctx=None):
        self.prompts.append(prompt)
        self.systems.append(system)
        if VOTE_PROMPT_MARKER in prompt:
            if not self.vote_responses:
                raise RuntimeError("no vote response queued")
            return self.vote_responses.pop(0), LlmUsage(10, 5, 15)
        return self.fact_response, LlmUsage(100, 50, 150)


def _fact_response(relationships: list[dict]) -> dict:
    return {
        "chapter_id": 1,
        "novel_id": "test-novel",
        "characters": [{"name": "宋江"}, {"name": "武松"}],
        "relationships": relationships,
        "locations": [{"name": "柴进庄", "type": "庄园"}],
        "events": [],
    }


def _rel(subtype: str | None = "结拜", polarity: str | None = "positive",
         closeness: str | None = "close") -> dict:
    return {
        "person_a": "宋江",
        "person_b": "武松",
        "relation_type": "结拜兄弟",
        "is_new": True,
        "evidence": "宋江与武松结拜为义兄弟",
        "polarity": polarity,
        "rel_subtype": subtype,
        "closeness": closeness,
    }


def _votes(*subtypes: str) -> dict:
    return {"votes": [{"index": i, "rel_subtype": s} for i, s in enumerate(subtypes)]}


@pytest.fixture
def dims_off(monkeypatch):
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", False)


@pytest.fixture
def no_vote(monkeypatch):
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", True)
    monkeypatch.setattr(config, "RELATION_SUBTYPE_VOTE_SAMPLES", 1)


# ── FR-1.2 维度解析 ──


@pytest.mark.asyncio
async def test_valid_dimensions_pass_through(no_vote):
    llm = MockLLM(_fact_response([_rel()]))
    extractor = ChapterFactExtractor(llm=llm)
    fact, usage, _ = await extractor.extract("test-novel", 1, "宋江与武松结拜。")
    rel = fact.relationships[0]
    assert rel.polarity == "positive"
    assert rel.rel_subtype == "结拜"
    assert rel.closeness == "close"
    assert rel.subtype_vote is None  # voting disabled at samples=1
    assert len(llm.prompts) == 1  # no extra vote calls


@pytest.mark.asyncio
async def test_invalid_dimensions_rejected_and_logged(no_vote, caplog):
    bad = _rel(subtype="结拜兄弟", polarity="友善", closeness="很近")
    llm = MockLLM(_fact_response([bad]))
    extractor = ChapterFactExtractor(llm=llm)
    with caplog.at_level(logging.WARNING, logger="src.extraction.chapter_fact_extractor"):
        fact, _, _ = await extractor.extract("test-novel", 1, "章节文本")
    rel = fact.relationships[0]
    assert rel.polarity is None
    assert rel.rel_subtype is None
    assert rel.closeness is None
    warnings = caplog.text
    assert "invalid polarity" in warnings
    assert "invalid rel_subtype" in warnings
    assert "invalid closeness" in warnings


@pytest.mark.asyncio
async def test_missing_dimensions_stay_none(no_vote):
    rel = _rel()
    for k in ("polarity", "rel_subtype", "closeness"):
        del rel[k]
    llm = MockLLM(_fact_response([rel]))
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, "章节文本")
    r = fact.relationships[0]
    assert r.polarity is None and r.rel_subtype is None and r.closeness is None


# ── 开关关闭(NFR-3 回退)──


@pytest.mark.asyncio
async def test_switch_off_prompt_and_parsing_unchanged(dims_off, monkeypatch):
    monkeypatch.setattr(config, "RELATION_SUBTYPE_VOTE_SAMPLES", 3)
    llm = MockLLM(_fact_response([_rel(subtype="任意脏值")]))
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, "章节文本")

    # prompt 不含维度指引(与改动前一致):system 与 few-shot 示例均无维度字段
    assert "关系维度判定" not in llm.systems[0]
    assert "rel_subtype" not in llm.systems[0]
    assert "rel_subtype" not in llm.prompts[0]
    # 解析不做维度清洗:脏值原样通过(改动前行为)
    assert fact.relationships[0].rel_subtype == "任意脏值"
    # 不做投票:仅 1 次 LLM 调用
    assert len(llm.prompts) == 1
    assert fact.relationships[0].subtype_vote is None


@pytest.mark.asyncio
async def test_switch_on_injects_dimension_guide(no_vote):
    llm = MockLLM(_fact_response([_rel()]))
    extractor = ChapterFactExtractor(llm=llm)
    await extractor.extract("test-novel", 1, "章节文本")
    assert "关系维度判定" in llm.systems[0]
    assert "先判文化特定关系" in llm.systems[0]


# ── FR-1.3 采样投票 ──


@pytest.mark.asyncio
async def test_vote_majority_wins(monkeypatch):
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", True)
    monkeypatch.setattr(config, "RELATION_SUBTYPE_VOTE_SAMPLES", 3)
    llm = MockLLM(
        _fact_response([_rel(subtype="结拜")]),
        vote_responses=[_votes("朋友-社交"), _votes("朋友-社交")],
    )
    extractor = ChapterFactExtractor(llm=llm)
    fact, usage, _ = await extractor.extract("test-novel", 1, "章节文本")
    rel = fact.relationships[0]
    assert rel.rel_subtype == "朋友-社交"  # 1 vs 2,多数决
    assert rel.subtype_vote == {"结拜": 1, "朋友-社交": 2}
    # 1 次主抽取 + 2 次轻量投票(不是整章重抽 3 遍)
    assert len(llm.prompts) == 3
    # 投票 token 计入 usage
    assert usage.prompt_tokens == 100 + 10 + 10


@pytest.mark.asyncio
async def test_vote_tie_picks_conservative_subtype(monkeypatch):
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", True)
    monkeypatch.setattr(config, "RELATION_SUBTYPE_VOTE_SAMPLES", 3)
    # 三票各不同:敌对(主抽取) / 师门-师徒 / 朋友-社交 → 平局取保守类
    llm = MockLLM(
        _fact_response([_rel(subtype="敌对")]),
        vote_responses=[_votes("师门-师徒"), _votes(CONSERVATIVE_SUBTYPE)],
    )
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, "章节文本")
    rel = fact.relationships[0]
    assert rel.rel_subtype == CONSERVATIVE_SUBTYPE
    assert rel.subtype_vote == {"敌对": 1, "师门-师徒": 1, CONSERVATIVE_SUBTYPE: 1}


@pytest.mark.asyncio
async def test_vote_invalid_sample_values_dropped(monkeypatch, caplog):
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", True)
    monkeypatch.setattr(config, "RELATION_SUBTYPE_VOTE_SAMPLES", 3)
    llm = MockLLM(
        _fact_response([_rel(subtype="结拜")]),
        vote_responses=[
            {"votes": [{"index": 0, "rel_subtype": "不在表内"}]},
            _votes("结拜"),
        ],
    )
    extractor = ChapterFactExtractor(llm=llm)
    with caplog.at_level(logging.WARNING, logger="src.extraction.chapter_fact_extractor"):
        fact, _, _ = await extractor.extract("test-novel", 1, "章节文本")
    rel = fact.relationships[0]
    assert rel.rel_subtype == "结拜"
    assert rel.subtype_vote == {"结拜": 2}  # 非法票被丢弃
    assert "invalid rel_subtype" in caplog.text


@pytest.mark.asyncio
async def test_vote_call_failure_non_fatal(monkeypatch):
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", True)
    monkeypatch.setattr(config, "RELATION_SUBTYPE_VOTE_SAMPLES", 3)
    llm = MockLLM(_fact_response([_rel(subtype="结拜")]))
    # 两次投票调用都失败(vote_responses 为空 → MockLLM 抛错)
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, "章节文本")
    rel = fact.relationships[0]
    assert rel.rel_subtype == "结拜"  # 保留主抽取结果
    assert rel.subtype_vote == {"结拜": 1}


# ── 纯函数单测 ──


def test_pick_majority_simple():
    assert _pick_majority_subtype({"结拜": 2, "朋友-社交": 1}) == "结拜"


def test_pick_majority_tie_prefers_conservative():
    assert _pick_majority_subtype({"结拜": 1, "朋友-社交": 1}) == CONSERVATIVE_SUBTYPE
    # 平局候选都不含保守类时,取次序表中更靠后(更泛化)的一个
    assert _pick_majority_subtype({"辈分-亲属": 1, "敌对": 1}) == "敌对"


def test_sanitize_only_touches_invalid_values():
    fact = ChapterFact(
        chapter_id=1, novel_id="t",
        relationships=[
            RelationshipFact(person_a="甲", person_b="乙", relation_type="兄弟",
                             rel_subtype="辈分-亲属"),
            RelationshipFact(person_a="丙", person_b="丁", relation_type="朋友",
                             rel_subtype="兄弟"),
        ],
    )
    _sanitize_relation_dimensions(fact, 1)
    assert fact.relationships[0].rel_subtype == "辈分-亲属"
    assert fact.relationships[1].rel_subtype is None


def test_vote_schema_hidden_from_llm_output_schema():
    schema = cfe._build_extraction_schema()
    rel_props = schema["$defs"]["RelationshipFact"]["properties"]
    assert "subtype_vote" not in rel_props
    # 三维字段保留在输出 schema 中,供 LLM 填写
    for f in ("polarity", "rel_subtype", "closeness"):
        assert f in rel_props
