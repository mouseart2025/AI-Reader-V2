"""FR-4.1 两遍制 recall pass 测试:补漏合并、来源标记、开关回退、失败非致命。

全部使用 mock LLM,不打真实 API。
验收口径(mock 层证明机制正确,真实效果以抽检报告为准):
- 补漏记录标记 source="recall_pass",可追溯来源;
- 首遍结果不被改写;
- 开关关闭时不发起第二遍调用,行为与 v0.73 一致(单遍);
- 单章 LLM 调用 ≤2 倍 (NFR-2)。
"""

from __future__ import annotations

import pytest

from src.extraction.chapter_fact_extractor import ChapterFactExtractor
from src.infra import config
from src.infra.llm_client import LlmUsage
from src.models.chapter_fact import CharacterFact, EventFact, RelationshipFact

CHAPTER_TEXT = (
    "宋江与武松在柴进庄上结拜为义兄弟。两人把酒言欢,结为生死之交。\n"
    "席间柴进提起林冲风雪上梁山之事,众人唏嘘不已。\n"
    "次日,宋江辞别柴进,独自上路投青州去了。"
)


class MockLLM:
    """Mock LLM:首遍返回 main_response,查漏调用(system 含"查漏专家")
    返回 recall_response;recall_error 非空时查漏调用抛错。"""

    def __init__(self, main_response: dict, recall_response: dict | None = None,
                 recall_error: Exception | None = None):
        self.main_response = main_response
        self.recall_response = recall_response or {}
        self.recall_error = recall_error
        self.prompts: list[str] = []
        self.recall_calls = 0

    async def generate(self, system, prompt, format=None, temperature=0.1,
                       max_tokens=4096, timeout=120, num_ctx=None):
        self.prompts.append(prompt)
        if "查漏专家" in system:
            self.recall_calls += 1
            if self.recall_error is not None:
                raise self.recall_error
            return self.recall_response, LlmUsage(10, 5, 15)
        return self.main_response, LlmUsage(100, 50, 150)


def _main_response() -> dict:
    """首遍抽取结果:宋江/武松/柴进 + 1 关系 + 1 事件。"""
    return {
        "chapter_id": 1,
        "novel_id": "test-novel",
        "characters": [{"name": "宋江"}, {"name": "武松"}, {"name": "柴进"}],
        "relationships": [
            {
                "person_a": "宋江",
                "person_b": "武松",
                "relation_type": "结拜兄弟",
                "evidence": "宋江与武松在柴进庄上结拜为义兄弟",
            }
        ],
        "events": [
            {
                "summary": "宋江辞别柴进独自上路",
                "type": "旅行",
                "importance": "medium",
                "participants": ["宋江", "柴进"],
                "location": "柴进庄",
                "evidence": "宋江辞别柴进,独自上路投青州去了",
            }
        ],
    }


def _recall_response() -> dict:
    """查漏补漏:新增林冲 + 1 新关系 + 1 新事件(均为首遍遗漏)。"""
    return {
        "chapter_id": 1,
        "novel_id": "test-novel",
        "characters": [{"name": "林冲"}],
        "relationships": [
            {
                "person_a": "柴进",
                "person_b": "林冲",
                "relation_type": "旧识",
                "evidence": "柴进提起林冲风雪上梁山之事",
            }
        ],
        "events": [
            {
                "summary": "柴进席间提起林冲之事",
                "type": "社交",
                "importance": "low",
                "participants": ["柴进", "林冲"],
                "location": "柴进庄",
                "evidence": "席间柴进提起林冲风雪上梁山之事",
            }
        ],
    }


@pytest.fixture
def recall_on(monkeypatch):
    """只开 recall pass,关闭维度/投票/证据锚定以隔离被测行为。"""
    monkeypatch.setattr(config, "RECALL_PASS_ENABLED", True)
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", False)
    monkeypatch.setattr(config, "RELATION_SUBTYPE_VOTE_SAMPLES", 1)
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", False)


@pytest.fixture
def recall_off(monkeypatch):
    monkeypatch.setattr(config, "RECALL_PASS_ENABLED", False)
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", False)
    monkeypatch.setattr(config, "RELATION_SUBTYPE_VOTE_SAMPLES", 1)
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", False)


# ── 模型兼容(NFR-3):旧 JSON 反序列化不变 ──


def test_source_field_legacy_json_defaults_to_main():
    """旧 JSON(无 source 字段)反序列化默认 "main"。"""
    ch = CharacterFact.model_validate({"name": "孙悟空"})
    rel = RelationshipFact.model_validate(
        {"person_a": "甲", "person_b": "乙", "relation_type": "朋友"}
    )
    ev = EventFact.model_validate({"summary": "石猴出世", "type": "成长"})
    assert ch.source == "main"
    assert rel.source == "main"
    assert ev.source == "main"


# ── 补漏合并与来源标记 ──


@pytest.mark.asyncio
async def test_recall_additions_merged_with_source_mark(recall_on):
    """补漏记录并入结果且标记 source="recall_pass",可追溯来源。"""
    llm = MockLLM(_main_response(), _recall_response())
    extractor = ChapterFactExtractor(llm=llm)
    fact, usage, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)

    char_sources = {ch.name: ch.source for ch in fact.characters}
    assert char_sources["林冲"] == "recall_pass"
    rel_sources = {(r.person_a, r.person_b): r.source for r in fact.relationships}
    assert rel_sources[("柴进", "林冲")] == "recall_pass"
    event_sources = {ev.summary: ev.source for ev in fact.events}
    assert event_sources["柴进席间提起林冲之事"] == "recall_pass"

    # 查漏 token 计入 usage
    assert usage.prompt_tokens == 100 + 10


@pytest.mark.asyncio
async def test_first_pass_records_not_rewritten(recall_on):
    """首遍结果不被改写:内容、顺序、source 均保持。"""
    llm = MockLLM(_main_response(), _recall_response())
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)

    # 首遍人物保持 source="main" 且字段不变
    first_chars = {ch.name: ch for ch in fact.characters if ch.source == "main"}
    assert set(first_chars) == {"宋江", "武松", "柴进"}
    # 首遍关系/事件内容与 source 不变
    rel = next(r for r in fact.relationships
               if (r.person_a, r.person_b) == ("宋江", "武松"))
    assert rel.source == "main"
    assert rel.relation_type == "结拜兄弟"
    assert rel.evidence == "宋江与武松在柴进庄上结拜为义兄弟"
    ev = next(e for e in fact.events if e.summary == "宋江辞别柴进独自上路")
    assert ev.source == "main"
    assert ev.importance == "medium"


@pytest.mark.asyncio
async def test_recall_duplicates_not_double_added(recall_on):
    """查漏返回首遍已有内容时不重复添加。"""
    recall = _recall_response()
    # 重复:首遍已有的人物宋江、已有关系、已有事件
    recall["characters"].append({"name": "宋江"})
    recall["relationships"].append({
        "person_a": "宋江", "person_b": "武松", "relation_type": "结拜兄弟",
    })
    recall["events"].append({
        "summary": "宋江辞别柴进独自上路", "type": "旅行",
    })
    llm = MockLLM(_main_response(), recall)
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)

    assert [ch.name for ch in fact.characters].count("宋江") == 1
    assert len([r for r in fact.relationships
                if (r.person_a, r.person_b) == ("宋江", "武松")]) == 1
    assert len([e for e in fact.events
                if e.summary == "宋江辞别柴进独自上路"]) == 1


# ── 开关回退(NFR-3 / NFR-2)──


@pytest.mark.asyncio
async def test_switch_off_single_pass_no_recall_call(recall_off):
    """开关关闭:不发起第二遍调用,行为与 v0.73 单遍一致。"""
    llm = MockLLM(_main_response(), _recall_response())
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)

    assert llm.recall_calls == 0
    assert len(llm.prompts) == 1  # 只有首遍调用
    assert {ch.name for ch in fact.characters} == {"宋江", "武松", "柴进"}
    assert all(r.source == "main" for r in fact.relationships)
    assert all(e.source == "main" for e in fact.events)


@pytest.mark.asyncio
async def test_llm_calls_at_most_2x_per_chapter(recall_on):
    """NFR-2:开启 recall pass 后单章 LLM 调用恰好 2 倍(首遍 + 查漏)。"""
    llm = MockLLM(_main_response(), _recall_response())
    extractor = ChapterFactExtractor(llm=llm)
    await extractor.extract("test-novel", 1, CHAPTER_TEXT)
    assert len(llm.prompts) == 2
    assert llm.recall_calls == 1


# ── 失败非致命 ──


@pytest.mark.asyncio
async def test_recall_failure_keeps_first_pass_result(recall_on):
    """查漏调用失败不影响首遍结果。"""
    llm = MockLLM(_main_response(), recall_error=RuntimeError("LLM 超时"))
    extractor = ChapterFactExtractor(llm=llm)
    fact, usage, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)

    assert {ch.name for ch in fact.characters} == {"宋江", "武松", "柴进"}
    assert len(fact.relationships) == 1
    assert all(r.source == "main" for r in fact.relationships)


@pytest.mark.asyncio
async def test_recall_empty_additions(recall_on):
    """查漏无遗漏时结果与单遍一致(全部 source="main")。"""
    llm = MockLLM(_main_response(), {"characters": [], "relationships": [],
                                     "events": []})
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)

    assert len(fact.characters) == 3
    assert all(ch.source == "main" for ch in fact.characters)


# ── 补漏记录经与首遍相同的 sanitize ──


@pytest.mark.asyncio
async def test_recall_additions_sanitized_like_first_pass(monkeypatch):
    """补漏记录同样经维度值校验 + 证据锚定清洗(口径与首遍一致)。"""
    monkeypatch.setattr(config, "RECALL_PASS_ENABLED", True)
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", True)
    monkeypatch.setattr(config, "RELATION_SUBTYPE_VOTE_SAMPLES", 1)
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", True)

    recall = _recall_response()
    # 非法维度值 + 缺证据的事件(importance 应被降一级)
    recall["relationships"][0]["polarity"] = "非法值"
    recall["relationships"][0]["evidence"] = "柴进提起林冲风雪上梁山之事"
    recall["events"][0]["importance"] = "high"
    recall["events"][0]["evidence"] = ""  # 缺证据 → high 降 medium

    llm = MockLLM(_main_response(), recall)
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract("test-novel", 1, CHAPTER_TEXT)

    rel = next(r for r in fact.relationships
               if (r.person_a, r.person_b) == ("柴进", "林冲"))
    assert rel.source == "recall_pass"
    assert rel.polarity is None  # 非法维度值被清洗
    ev = next(e for e in fact.events if e.summary == "柴进席间提起林冲之事")
    assert ev.source == "recall_pass"
    assert ev.importance == "medium"  # 缺证据降级
