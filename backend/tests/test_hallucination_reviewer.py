"""FR-4.2 幻觉人物 LLM 判定层测试:候选挑选、剔除/降级、白名单保护、审计日志。

全部使用 mock LLM,不打真实 API。
验收口径:银驮类(规则层抓不住的疑似幻觉人物)被 LLM 层正确处置;
真实人物不误杀(白名单 + 原文可定位保护);决策落 JSONL 审计日志 (NFR-5)。
"""

from __future__ import annotations

import json

import pytest

from src.extraction import hallucination_reviewer as hr
from src.extraction.hallucination_reviewer import (
    apply_verdicts,
    find_hallucination_candidates,
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

# 章节原文:出现 孙悟空/唐僧/金角大王,但绝无 "银驮"
CHAPTER_TEXT = (
    "话说唐僧师徒行至平顶山,忽见一山挡路。孙悟空举目观看,只见那山\n"
    "嵯峨险峻。行者道:师父且慢行,待老孙前去探路。早有金角大王在洞中\n"
    "稳坐,吩咐小妖们小心巡山。"
)


def _fact_with_suspect() -> ChapterFact:
    """含疑似幻觉人物"银驮"(原文无此名)的 ChapterFact。"""
    return ChapterFact(
        chapter_id=32,
        novel_id="xiyouji",
        characters=[
            CharacterFact(name="孙悟空"),
            CharacterFact(name="唐僧"),
            CharacterFact(name="银驮"),  # 幻觉:原文无此名
        ],
        relationships=[
            RelationshipFact(person_a="银驮", person_b="孙悟空",
                             relation_type="同伙"),
            RelationshipFact(person_a="孙悟空", person_b="唐僧",
                             relation_type="师徒"),
        ],
        events=[
            EventFact(summary="银驮与孙悟空交战", type="战斗",
                      participants=["银驮", "孙悟空"]),
            EventFact(summary="孙悟空探路", type="旅行",
                      participants=["孙悟空"]),
        ],
    )


class MockLLM:
    """Mock LLM:按 verdicts_map 返回裁决 {name: (is_real, confidence, reason)}。"""

    def __init__(self, verdicts_map: dict | None = None, error: Exception | None = None):
        self.verdicts_map = verdicts_map or {}
        self.error = error
        self.prompts: list[str] = []
        self.calls = 0

    async def generate(self, system, prompt, format=None, **kw):
        self.calls += 1
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        verdicts = [
            {
                "name": name,
                "is_real": v[0],
                "confidence": v[1],
                "reason": v[2] if len(v) > 2 else "",
            }
            for name, v in self.verdicts_map.items()
        ]
        from src.infra.llm_client import LlmUsage
        return {"verdicts": verdicts}, LlmUsage(10, 5, 15)


@pytest.fixture
def review_on(monkeypatch):
    monkeypatch.setattr(config, "HALLUCINATION_REVIEW_ENABLED", True)


@pytest.fixture
def review_off(monkeypatch):
    monkeypatch.setattr(config, "HALLUCINATION_REVIEW_ENABLED", False)


# ── 候选挑选(疑似信号:原文找不到 + 非白名单)──


def test_candidates_names_absent_from_text():
    """原文中找不到的名字进入候选;原文可定位的名字不进入。"""
    fact = _fact_with_suspect()
    candidates = find_hallucination_candidates(fact, CHAPTER_TEXT)
    assert candidates == ["银驮"]  # 孙悟空/唐僧在原文中可定位,不候选


def test_candidates_respect_protected_names():
    """白名单(protected_names)中的名字即使原文找不到也不进入候选。"""
    fact = _fact_with_suspect()
    candidates = find_hallucination_candidates(
        fact, CHAPTER_TEXT, protected_names={"银驮"},
    )
    assert candidates == []


def test_candidates_disambiguated_base_name():
    """"X·樵夫" 类消歧名按 "·" 后基本名查原文:基本名可定位则不候选。"""
    fact = ChapterFact(
        chapter_id=1, novel_id="n",
        characters=[CharacterFact(name="平顶山·樵夫")],
    )
    # 原文有"樵夫"→ 不候选
    assert find_hallucination_candidates(fact, "山中有一樵夫指路。") == []
    # 原文无"樵夫"→ 候选
    assert find_hallucination_candidates(fact, CHAPTER_TEXT) == ["平顶山·樵夫"]


# ── 裁决解析与处置 ──


def test_parse_verdicts_ignores_out_of_candidate_names():
    """LLM 返回候选集之外的裁决一律忽略。"""
    verdicts = parse_verdicts(
        {"verdicts": [
            {"name": "银驮", "is_real": False, "confidence": "high", "reason": "原文无"},
            {"name": "如来佛", "is_real": False, "confidence": "high", "reason": "越界"},
        ]},
        ["银驮"],
    )
    assert set(verdicts) == {"银驮"}


def test_parse_verdicts_invalid_confidence_falls_back_low():
    """非法置信度按 low 处理(保守,不剔除)。"""
    verdicts = parse_verdicts(
        {"verdicts": [
            {"name": "银驮", "is_real": False, "confidence": "极高", "reason": ""},
        ]},
        ["银驮"],
    )
    assert verdicts["银驮"]["confidence"] == "low"


def test_apply_verdicts_high_confidence_removed_with_cascade():
    """高置信幻觉:剔除人物并级联清理关系/事件参与者。"""
    fact = _fact_with_suspect()
    new_fact, actions = apply_verdicts(
        fact, ["银驮"],
        {"银驮": {"is_real": False, "confidence": "high", "reason": "原文无此名"}},
    )
    assert {ch.name for ch in new_fact.characters} == {"孙悟空", "唐僧"}
    # 涉及银驮的关系被剔除,无关关系保留
    assert [(r.person_a, r.person_b) for r in new_fact.relationships] == [
        ("孙悟空", "唐僧")
    ]
    # 事件参与者中的银驮被清理
    ev = next(e for e in new_fact.events if e.summary == "银驮与孙悟空交战")
    assert ev.participants == ["孙悟空"]
    assert actions[0]["action"] == "removed"


def test_apply_verdicts_low_confidence_suspect_kept():
    """低置信幻觉:降级为存疑,保留人物,仅审计标记。"""
    fact = _fact_with_suspect()
    new_fact, actions = apply_verdicts(
        fact, ["银驮"],
        {"银驮": {"is_real": False, "confidence": "low", "reason": "拿不准"}},
    )
    assert {ch.name for ch in new_fact.characters} == {"孙悟空", "唐僧", "银驮"}
    assert len(new_fact.relationships) == 2  # 关系不动
    assert actions[0]["action"] == "suspect"


def test_apply_verdicts_real_confirmed_kept():
    """判定真实:保留,action=confirmed。"""
    fact = _fact_with_suspect()
    new_fact, actions = apply_verdicts(
        fact, ["银驮"],
        {"银驮": {"is_real": True, "confidence": "high", "reason": "银角大王误写"}},
    )
    assert new_fact is fact  # 无剔除时原样返回
    assert actions[0]["action"] == "confirmed"


# ── 主入口:LLM 调用 + 审计日志 + 开关 ──


@pytest.mark.asyncio
async def test_review_removes_hallucination_and_writes_audit_log(
    review_on, tmp_path,
):
    """银驮类用例:LLM 判幻觉(高置信)→ 剔除,决策落 JSONL 审计日志。"""
    log_path = tmp_path / "hallucination_log.jsonl"
    llm = MockLLM({"银驮": (False, "high", "原文通篇无此名,系幻觉")})
    fact = _fact_with_suspect()
    new_fact = await review_chapter_characters(
        fact,
        chapter_text=CHAPTER_TEXT,
        llm=llm,
        novel_id="xiyouji",
        chapter_id=32,
        log_path=log_path,
        record_cost=False,
    )

    assert {ch.name for ch in new_fact.characters} == {"孙悟空", "唐僧"}
    assert llm.calls == 1

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["prompt_version"] == hr.PROMPT_VERSION
    assert record["novel_id"] == "xiyouji"
    assert record["chapter_id"] == 32
    assert record["candidates"] == ["银驮"]
    assert record["actions"][0]["action"] == "removed"
    assert record["actions"][0]["confidence"] == "high"
    assert record["timestamp"]


@pytest.mark.asyncio
async def test_review_keeps_real_character_no_false_kill(review_on, tmp_path):
    """真实人物不误杀:原文找不到但 LLM 判真实(别名/误写)→ 保留。"""
    log_path = tmp_path / "log.jsonl"
    llm = MockLLM({"银驮": (True, "medium", "疑为银角大王误写,上下文可对应")})
    fact = _fact_with_suspect()
    new_fact = await review_chapter_characters(
        fact, chapter_text=CHAPTER_TEXT, llm=llm,
        novel_id="xiyouji", chapter_id=32,
        log_path=log_path, record_cost=False,
    )
    assert "银驮" in {ch.name for ch in new_fact.characters}
    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["actions"][0]["action"] == "confirmed"


@pytest.mark.asyncio
async def test_review_whitelist_never_sent_to_llm(review_on, tmp_path):
    """白名单保护:protected_names 中的名字不发起 LLM 判定。"""
    llm = MockLLM()
    fact = _fact_with_suspect()
    new_fact = await review_chapter_characters(
        fact, chapter_text=CHAPTER_TEXT, llm=llm,
        protected_names={"银驮"},
        log_path=tmp_path / "log.jsonl", record_cost=False,
    )
    assert llm.calls == 0  # 无候选 → 不调用 LLM
    assert new_fact is fact


@pytest.mark.asyncio
async def test_review_no_candidates_no_llm_call(review_on, tmp_path):
    """所有人物原文可定位时不发起 LLM 调用。"""
    fact = ChapterFact(
        chapter_id=1, novel_id="n",
        characters=[CharacterFact(name="孙悟空"), CharacterFact(name="唐僧")],
    )
    llm = MockLLM()
    new_fact = await review_chapter_characters(
        fact, chapter_text=CHAPTER_TEXT, llm=llm,
        log_path=tmp_path / "log.jsonl", record_cost=False,
    )
    assert llm.calls == 0
    assert new_fact is fact


@pytest.mark.asyncio
async def test_review_switch_off_noop(review_off, tmp_path):
    """开关关闭:no-op,不调用 LLM,行为与 v0.73 一致。"""
    llm = MockLLM({"银驮": (False, "high", "幻觉")})
    fact = _fact_with_suspect()
    new_fact = await review_chapter_characters(
        fact, chapter_text=CHAPTER_TEXT, llm=llm,
        log_path=tmp_path / "log.jsonl", record_cost=False,
    )
    assert llm.calls == 0
    assert new_fact is fact


@pytest.mark.asyncio
async def test_review_llm_failure_keeps_fact(review_on, tmp_path):
    """LLM 调用失败非致命:原样返回 fact,不阻塞管线。"""
    llm = MockLLM(error=RuntimeError("LLM 超时"))
    fact = _fact_with_suspect()
    new_fact = await review_chapter_characters(
        fact, chapter_text=CHAPTER_TEXT, llm=llm,
        log_path=tmp_path / "log.jsonl", record_cost=False,
    )
    assert new_fact is fact
