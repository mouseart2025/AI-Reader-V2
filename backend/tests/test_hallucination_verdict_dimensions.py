"""E1 (issue #70): 幻觉审查 verdict 双维度语义测试。

v3 起 verdict 拆为 entity_exists(原文是否存在这个人物)与
name_supported(这个名字本身是否被当前 source 支持)两个维度,
is_real 保留为派生兼容字段(is_real = entity_exists AND name_supported)。

验收口径:
- 拼接名(A 姓 + B 名):人存在但名字不支持 → 不 confirmed;
  high → removed,非 high → suspect。
- 别名变体:两个维度都 true → confirmed。
- 纯幻觉:两个维度都 false + high → removed。
- source 外知识:prompt 明确禁止引入原文之外的作品知识/常识补全姓名。
- 旧格式(仅 is_real)输出兼容:两个维度退化为同一值。

全部使用 mock LLM,不打真实 API。
"""

from __future__ import annotations

import json

import pytest

from src.extraction import hallucination_reviewer as hr
from src.extraction.hallucination_reviewer import (
    apply_alias_verdicts,
    apply_verdicts,
    build_review_prompt,
    parse_verdicts,
    review_chapter_characters,
)
from src.infra import config
from src.models.chapter_fact import (
    ChapterFact,
    CharacterFact,
    EventFact,
    RelationshipFact,
)

# 章节原文:出现 五河士道 / 夜刀神十香,绝无 "五河天一"(拼接名)与 "银驮"
CHAPTER_TEXT = (
    "五河士道推开家门,夜刀神十香正坐在客厅里吃着黄豆粉面包。\n"
    "「士道,今天去哪里?」十香问道。士道想了想,说去学校吧。"
)


def _fact_with_splice() -> ChapterFact:
    """含拼接名"五河天一"(五河士道的姓 + 夜刀神天X式的名)的 ChapterFact。"""
    return ChapterFact(
        chapter_id=1,
        novel_id="date-a-live",
        characters=[
            CharacterFact(name="五河士道"),
            CharacterFact(name="夜刀神十香"),
            CharacterFact(name="五河天一"),  # 拼接名:原文无此名
        ],
        relationships=[
            RelationshipFact(person_a="五河天一", person_b="夜刀神十香",
                             relation_type="同伴"),
            RelationshipFact(person_a="五河士道", person_b="夜刀神十香",
                             relation_type="同伴"),
        ],
        events=[
            EventFact(summary="五河天一与十香对话", type="社交",
                      participants=["五河天一", "夜刀神十香"]),
        ],
    )


class MockLLM:
    """Mock LLM:按 verdicts_map 返回 v3 双维度裁决 {name: dict}。"""

    def __init__(self, verdicts_map: dict | None = None):
        self.verdicts_map = verdicts_map or {}
        self.calls = 0

    async def generate(self, system, prompt, format=None, **kw):
        self.calls += 1
        verdicts = [{"name": name, **v} for name, v in self.verdicts_map.items()]
        from src.infra.llm_client import LlmUsage
        return {"verdicts": verdicts}, LlmUsage(10, 5, 15)


@pytest.fixture
def review_on(monkeypatch):
    monkeypatch.setattr(config, "HALLUCINATION_REVIEW_ENABLED", True)


# ── parse_verdicts:双维度解析与派生 is_real ──


def test_parse_verdicts_derives_is_real_as_and():
    """is_real = entity_exists AND name_supported;三个字段都在 verdict 里。"""
    verdicts = parse_verdicts(
        {"verdicts": [
            {"name": "五河天一", "entity_exists": True, "name_supported": False,
             "confidence": "high", "reason": "五河士道的姓 + 他名的拼接"},
            {"name": "十香", "entity_exists": True, "name_supported": True,
             "confidence": "high", "reason": "夜刀神十香的简称"},
            {"name": "银驮", "entity_exists": False, "name_supported": False,
             "confidence": "high", "reason": "原文无此人"},
        ]},
        ["五河天一", "十香", "银驮"],
    )
    assert verdicts["五河天一"]["is_real"] is False   # 人存在但名字不支持
    assert verdicts["五河天一"]["entity_exists"] is True
    assert verdicts["五河天一"]["name_supported"] is False
    assert verdicts["十香"]["is_real"] is True         # 别名变体:都为 true
    assert verdicts["银驮"]["is_real"] is False        # 纯幻觉:都为 false


def test_parse_verdicts_legacy_is_real_only_fallback():
    """旧格式(仅 is_real)兼容:两个维度退化为同一值。"""
    verdicts = parse_verdicts(
        {"verdicts": [
            {"name": "银驮", "is_real": False, "confidence": "high", "reason": ""},
            {"name": "十香", "is_real": True, "confidence": "low", "reason": ""},
        ]},
        ["银驮", "十香"],
    )
    assert verdicts["银驮"]["entity_exists"] is False
    assert verdicts["银驮"]["name_supported"] is False
    assert verdicts["银驮"]["is_real"] is False
    assert verdicts["十香"]["entity_exists"] is True
    assert verdicts["十香"]["is_real"] is True


# ── apply_verdicts:拼接名不 confirmed ──


def test_spliced_name_high_confidence_removed():
    """拼接名:entity_exists=true 但 name_supported=false,high → removed。

    「名字错但人存在」的正确处理是拒绝这个错误名字(不 confirmed),
    级联清理其关系/事件参与者。
    """
    fact = _fact_with_splice()
    new_fact, actions = apply_verdicts(
        fact, ["五河天一"],
        {"五河天一": {"entity_exists": True, "name_supported": False,
                      "is_real": False, "confidence": "high",
                      "reason": "五河士道的姓与他人名的错误拼接"}},
    )
    assert {ch.name for ch in new_fact.characters} == {"五河士道", "夜刀神十香"}
    assert [(r.person_a, r.person_b) for r in new_fact.relationships] == [
        ("五河士道", "夜刀神十香")
    ]
    ev = new_fact.events[0]
    assert ev.participants == ["夜刀神十香"]
    assert actions[0]["action"] == "removed"
    # 审计 action 里保留双维度细节
    assert actions[0]["entity_exists"] is True
    assert actions[0]["name_supported"] is False
    assert actions[0]["is_real"] is False


def test_spliced_name_low_confidence_suspect_kept():
    """拼接名但拿不准(low)→ suspect:保留,不剔除,不 confirmed。"""
    fact = _fact_with_splice()
    new_fact, actions = apply_verdicts(
        fact, ["五河天一"],
        {"五河天一": {"entity_exists": True, "name_supported": False,
                      "is_real": False, "confidence": "low",
                      "reason": "疑似拼接但不确定"}},
    )
    assert "五河天一" in {ch.name for ch in new_fact.characters}
    assert actions[0]["action"] == "suspect"


def test_alias_variant_both_true_confirmed():
    """别名变体:两个维度都 true → confirmed,原样保留。"""
    fact = _fact_with_splice()
    new_fact, actions = apply_verdicts(
        fact, ["五河天一"],
        {"五河天一": {"entity_exists": True, "name_supported": True,
                      "is_real": True, "confidence": "medium",
                      "reason": "原文以此称呼该人物"}},
    )
    assert new_fact is fact
    assert actions[0]["action"] == "confirmed"


def test_pure_hallucination_both_false_removed():
    """纯幻觉:两个维度都 false + high → removed。"""
    fact = _fact_with_splice()
    new_fact, actions = apply_verdicts(
        fact, ["五河天一"],
        {"五河天一": {"entity_exists": False, "name_supported": False,
                      "is_real": False, "confidence": "high",
                      "reason": "原文不存在的人物"}},
    )
    assert "五河天一" not in {ch.name for ch in new_fact.characters}
    assert actions[0]["action"] == "removed"


def test_apply_alias_verdicts_name_unsupported_removed():
    """别名链同路径:name_supported=false + high → alias_removed,不动角色本身。"""
    fact = ChapterFact(
        chapter_id=1, novel_id="n",
        characters=[CharacterFact(name="五河士道", new_aliases=["五河天一"])],
    )
    new_fact, actions = apply_alias_verdicts(
        fact, {"五河士道": ["五河天一"]},
        {"五河天一": {"entity_exists": True, "name_supported": False,
                      "is_real": False, "confidence": "high",
                      "reason": "拼接名"}},
    )
    assert new_fact.characters[0].name == "五河士道"  # 角色本身不动
    assert new_fact.characters[0].new_aliases == []   # 错误别名被剔除
    assert actions[0]["action"] == "alias_removed"


# ── prompt:禁止 source 外知识 + 双维度输出指令 ──


def test_prompt_forbids_out_of_source_knowledge():
    """system prompt 明确禁止引入原文之外的作品知识/常识补全姓名。"""
    assert "严禁引入原文之外的作品知识" in hr._SYSTEM_PROMPT
    assert "entity_exists" in hr._SYSTEM_PROMPT
    assert "name_supported" in hr._SYSTEM_PROMPT
    # 拼接名处理规则写入 prompt
    assert "拼接" in hr._SYSTEM_PROMPT


def test_build_review_prompt_requests_two_dimensions():
    """user prompt 的输出指令要求 entity_exists / name_supported 双维度。"""
    prompt = build_review_prompt(["五河天一"], CHAPTER_TEXT)
    assert "entity_exists" in prompt
    assert "name_supported" in prompt
    assert "is_real" not in prompt.split("## 章节原文")[1]


def test_prompt_version_bumped_v3():
    """PROMPT_VERSION 升到 hr-char-v3;schema 要求双维度字段。"""
    assert hr.PROMPT_VERSION == "hr-char-v3"
    item_props = hr._VERDICT_SCHEMA["properties"]["verdicts"]["items"]
    assert "entity_exists" in item_props["properties"]
    assert "name_supported" in item_props["properties"]
    assert "entity_exists" in item_props["required"]
    assert "name_supported" in item_props["required"]


# ── 主入口端到端(mock LLM):拼接名剔除 + 审计双维度落盘 ──


@pytest.mark.asyncio
async def test_review_spliced_name_end_to_end(review_on, tmp_path):
    """拼接名场景:LLM 判 entity_exists=true / name_supported=false + high
    → 剔除,审计日志保留双维度与派生 is_real。"""
    log_path = tmp_path / "hallucination_log.jsonl"
    llm = MockLLM({
        "五河天一": {"entity_exists": True, "name_supported": False,
                     "confidence": "high",
                     "reason": "五河士道的姓 + 其他角色的名,错误拼接"},
    })
    fact = _fact_with_splice()
    new_fact = await review_chapter_characters(
        fact,
        chapter_text=CHAPTER_TEXT,
        llm=llm,
        novel_id="date-a-live",
        chapter_id=1,
        log_path=log_path,
        record_cost=False,
    )
    assert {ch.name for ch in new_fact.characters} == {"五河士道", "夜刀神十香"}
    assert llm.calls == 1

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["prompt_version"] == "hr-char-v3"
    action = record["actions"][0]
    assert action["action"] == "removed"
    assert action["entity_exists"] is True
    assert action["name_supported"] is False
    assert action["is_real"] is False


@pytest.mark.asyncio
async def test_review_external_knowledge_verdict_not_confirmed(review_on, tmp_path):
    """source 外知识场景:reviewer 引入作品外知识补全姓名时,应判
    name_supported=false —— 此处模拟该裁决并验证不 confirmed。"""
    log_path = tmp_path / "log.jsonl"
    llm = MockLLM({
        "五河天一": {"entity_exists": True, "name_supported": False,
                     "confidence": "medium",
                     "reason": "需结合作品外设定才能补全,本章原文不支持此名"},
    })
    fact = _fact_with_splice()
    new_fact = await review_chapter_characters(
        fact, chapter_text=CHAPTER_TEXT, llm=llm,
        novel_id="date-a-live", chapter_id=1,
        log_path=log_path, record_cost=False,
    )
    # medium 置信 → suspect(保守保留),但绝不是 confirmed
    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    action = record["actions"][0]
    assert action["action"] == "suspect"
    assert action["is_real"] is False
    assert "五河天一" in {ch.name for ch in new_fact.characters}
