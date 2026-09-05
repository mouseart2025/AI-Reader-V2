"""canonical-name 污染防线测试(2026-09 第一批修复)。

覆盖五条防线:
- A1: NameResolver.accumulate_from_chapter 双向原文锚定(canonical 端 + alias 端);
- A2: 匿名/外貌指代(神秘人/黑衣人/蒙面人 等)进入 alias_safety_level level 0;
- A3: FactValidator 自动补 character(事件参与者/关系人名)的原文锚定;
- A4: character.name 复合名防线(同章 ≥2 个他人全名拼接 → 剔除);
- A5: 幻觉判定层把 new_aliases 纳入送审(高置信幻觉别名剔除 + 审计)。

全部使用 mock LLM / 纯内存 ChapterFact,不打真实 API。
"""

from __future__ import annotations

import json

import pytest

from src.extraction import hallucination_reviewer as hr
from src.extraction.fact_validator import FactValidator
from src.extraction.name_resolver import NameResolver
from src.extraction.hallucination_reviewer import (
    apply_alias_verdicts,
    find_alias_candidates,
    review_chapter_characters,
)
from src.infra import config
from src.models.chapter_fact import (
    ChapterFact,
    CharacterFact,
    EventFact,
    RelationshipFact,
)
from src.services.name_authority import alias_safety_level, is_blocked_name

# 章节原文:出现 孙悟空/美猴王/唐僧,绝无 "八戒沙僧"、"银驮"、"孙行者悟空"
CHAPTER_TEXT = (
    "话说唐僧师徒行至平顶山,忽见一山挡路。孙悟空举目观看,只见那山\n"
    "嵯峨险峻。美猴王道:师父且慢行,待我前去探路。唐僧点头称善。"
)


def _make_fact(chars: list[CharacterFact], **kwargs) -> ChapterFact:
    return ChapterFact(chapter_id=1, novel_id="test", characters=chars, **kwargs)


# ── A1: NameResolver 双向锚定 ──


class TestNameResolverGrounding:
    """accumulate_from_chapter 传入 chapter_text 后的双向原文锚定。"""

    def test_ungrounded_canonical_alias_declaration_dropped(self):
        """拼接名(原文不存在)作为 canonical 端:其别名声明不进入映射。"""
        nr = NameResolver()
        fact = _make_fact([
            CharacterFact(name="孙行者悟空", new_aliases=["大圣爷"]),  # 均不在原文
        ])
        nr.accumulate_from_chapter(fact, chapter_text=CHAPTER_TEXT)
        assert "大圣爷" not in nr._canonical_map
        assert nr._freq.get("孙行者悟空", 0) == 0  # 不计 freq
        assert nr._ungrounded_dropped["canonical"] == 1
        assert nr.ungrounded_drop_count == 1

    def test_ungrounded_alias_dropped_from_mapping_and_fact(self):
        """原文存在的 canonical 声明原文不存在的别名:声明丢弃、new_aliases 剔除。"""
        nr = NameResolver()
        fact = _make_fact([
            CharacterFact(name="孙悟空", new_aliases=["美猴王", "齐天大圣"]),
        ])
        # 原文有 孙悟空/美猴王,无 "齐天大圣"
        nr.accumulate_from_chapter(fact, chapter_text=CHAPTER_TEXT)
        assert nr._canonical_map.get("美猴王") == "孙悟空"  # 可定位,正常归并
        assert "齐天大圣" not in nr._canonical_map  # 不可定位,丢弃
        assert fact.characters[0].new_aliases == ["美猴王"]  # 声明从 fact 剔除
        assert nr._ungrounded_dropped["alias"] == 1

    def test_grounded_merge_unaffected(self):
        """原文存在的正常别名归并不受影响(与无锚定行为一致)。"""
        nr = NameResolver()
        fact = _make_fact([
            CharacterFact(name="孙悟空", new_aliases=["美猴王"]),
        ])
        nr.accumulate_from_chapter(fact, chapter_text=CHAPTER_TEXT)
        assert nr._canonical_map.get("美猴王") == "孙悟空"
        assert nr._freq["孙悟空"] == 1
        assert nr.ungrounded_drop_count == 0

    def test_dictionary_canonical_exempt_from_grounding(self):
        """词典初始化路径不需要校验:词典名作为 canonical 端天然锚定。"""
        from src.models.entity_dict import EntityDictEntry
        nr = NameResolver()
        nr.load_from_entity_dictionary([
            EntityDictEntry(name="孙悟空", entity_type="person", frequency=152,
                            aliases=["行者"], source="freq"),
        ])
        assert nr._canonical_map.get("行者") == "孙悟空"  # 词典路径不受影响
        # 本章原文没有 "孙悟空"(resolve 后被改写为词典 canonical 的情形),
        # 但其别名声明中原文可定位的别名仍正常累积
        fact = _make_fact([
            CharacterFact(name="孙悟空", new_aliases=["美猴王", "齐天大圣"]),
        ])
        nr.accumulate_from_chapter(fact, chapter_text=CHAPTER_TEXT)
        assert nr._canonical_map.get("美猴王") == "孙悟空"  # 词典名豁免 canonical 锚定
        assert "齐天大圣" not in nr._canonical_map  # 别名端仍须锚定
        assert nr._ungrounded_dropped["canonical"] == 0

    def test_established_canonical_exempt(self):
        """既有 canonical 映射值(前序章节已确立)豁免 canonical 端锚定。"""
        nr = NameResolver()
        nr._canonical_map["行者"] = "孙悟空"  # 模拟前序章节已确立
        fact = _make_fact([
            CharacterFact(name="孙悟空", new_aliases=["美猴王"]),
        ])
        nr.accumulate_from_chapter(fact, chapter_text="行者前来探路,美猴王随后。")
        assert nr._canonical_map.get("美猴王") == "孙悟空"
        assert nr._ungrounded_dropped["canonical"] == 0

    def test_disambiguated_name_grounded_by_base(self):
        """"X·樵夫" 类消歧名按 "·" 后基本名锚定。"""
        nr = NameResolver()
        fact = _make_fact([
            CharacterFact(name="平顶山·樵夫", new_aliases=["樵夫哥"]),
        ])
        nr.accumulate_from_chapter(
            fact, chapter_text="山中有一樵夫指路,人称樵夫哥。",
        )
        assert nr._canonical_map.get("樵夫哥") == "平顶山·樵夫"
        assert nr._ungrounded_dropped["canonical"] == 0

    def test_no_chapter_text_legacy_behavior(self):
        """不传 chapter_text:保持旧行为(不锚定,全部累积)。"""
        nr = NameResolver()
        fact = _make_fact([
            CharacterFact(name="孙行者悟空", new_aliases=["齐天大圣"]),
        ])
        nr.accumulate_from_chapter(fact)
        assert nr._canonical_map.get("齐天大圣") == "孙行者悟空"
        assert nr.ungrounded_drop_count == 0


# ── A2: 匿名/外貌指代 level 0 ──


class TestAnonymousReferenceBlocklist:
    """匿名/外貌指代不可作为别名归并(level 0)。"""

    def test_anonymous_references_level_0(self):
        for name in ["神秘人", "神秘人物", "黑衣人", "蒙面人", "白衣人",
                     "白衣女子", "灰衣人", "来人", "陌生人", "面具人"]:
            level = alias_safety_level(name)
            assert level == 0, f"{name} should be level 0 but got {level}"
            assert is_blocked_name(name)

    def test_real_character_names_unaffected(self):
        """精确匹配不误伤真实角色名(如某书角色真叫"白衣")。"""
        for name in ["白衣", "林黛玉", "孙悟空", "灰衣"]:
            level = alias_safety_level(name)
            assert level >= 1, f"{name} should not be hard-blocked but got {level}"

    def test_end_to_end_anonymous_alias_rejected(self):
        """端到端:new_aliases 声称"神秘人"是 X 的别名 → 不进入 canonical 映射。"""
        nr = NameResolver()
        fact = _make_fact([
            CharacterFact(name="唐僧", new_aliases=["神秘人", "神秘人物"]),
        ])
        # 即使不传 chapter_text(旧路径),level 0 名单也应拦截
        nr.accumulate_from_chapter(fact)
        assert "神秘人" not in nr._canonical_map
        assert "神秘人物" not in nr._canonical_map

    def test_anonymous_reference_not_a_character(self):
        """匿名指代作为 character.name 也会被 is_generic_person 拦截。"""
        validator = FactValidator()
        fact = _make_fact([
            CharacterFact(name="孙悟空"),
            CharacterFact(name="神秘人"),
        ])
        validated = validator.validate(fact)
        assert {ch.name for ch in validated.characters} == {"孙悟空"}


# ── A3: 自动补 character 的原文锚定 ──


class TestAutoAddedCharacterGrounding:
    """事件参与者/关系人名自动补 character 时的原文锚定。"""

    def test_fabricated_participant_not_added(self):
        """事件参与者为编造名(原文不存在)→ 不补 character,事件本身保留。"""
        validator = FactValidator()
        fact = _make_fact(
            [CharacterFact(name="孙悟空")],
            events=[EventFact(summary="银驮与孙悟空交战", type="战斗",
                              participants=["银驮", "孙悟空"])],
        )
        validated = validator.validate(fact, chapter_text=CHAPTER_TEXT)
        assert {ch.name for ch in validated.characters} == {"孙悟空"}
        # 事件记录本身保留
        assert validated.events[0].participants == ["银驮", "孙悟空"]

    def test_grounded_participant_still_added(self):
        """原文存在的参与者名字正常补成 character。"""
        validator = FactValidator()
        fact = _make_fact(
            [CharacterFact(name="孙悟空")],
            events=[EventFact(summary="唐僧与孙悟空交谈", type="社交",
                              participants=["唐僧", "孙悟空"])],
        )
        validated = validator.validate(fact, chapter_text=CHAPTER_TEXT)
        assert {ch.name for ch in validated.characters} == {"孙悟空", "唐僧"}

    def test_no_chapter_text_keeps_legacy_autofill(self):
        """不传 chapter_text:保持旧行为(不锚定,照常补 character)。"""
        validator = FactValidator()
        fact = _make_fact(
            [CharacterFact(name="孙悟空")],
            events=[EventFact(summary="银驮与孙悟空交战", type="战斗",
                              participants=["银驮", "孙悟空"])],
        )
        validated = validator.validate(fact)
        assert "银驮" in {ch.name for ch in validated.characters}

    def test_fabricated_relation_person_not_added(self):
        """关系人名为编造名(原文不存在)→ 不补 character。"""
        validator = FactValidator()
        characters = [CharacterFact(name="孙悟空")]
        rels = [RelationshipFact(person_a="孙悟空", person_b="银驮",
                                 relation_type="同伙")]
        result = validator._ensure_relation_persons_in_characters(
            characters, rels, CHAPTER_TEXT,
        )
        assert {ch.name for ch in result} == {"孙悟空"}
        # 原文存在的关系人名正常补
        rels2 = [RelationshipFact(person_a="孙悟空", person_b="唐僧",
                                  relation_type="师徒")]
        result2 = validator._ensure_relation_persons_in_characters(
            [CharacterFact(name="孙悟空")], rels2, CHAPTER_TEXT,
        )
        assert {ch.name for ch in result2} == {"孙悟空", "唐僧"}


# ── A4: character.name 复合名防线 ──


class TestCompositeCharacterName:
    """character.name 包含同章 ≥2 个他人全名 → 按 _clean_aliases Rule 3 同款剔除。"""

    def test_composite_name_dropped(self):
        """"八戒沙僧" 式复合名(含同章八戒/沙僧两个全名)被剔除。"""
        validator = FactValidator()
        fact = _make_fact([
            CharacterFact(name="八戒"),
            CharacterFact(name="沙僧"),
            CharacterFact(name="八戒沙僧"),
        ])
        validated = validator.validate(fact)
        assert {ch.name for ch in validated.characters} == {"八戒", "沙僧"}

    def test_single_containment_kept(self):
        """只含 1 个他人全名的合法长名不受影响("孙悟空" 含 "悟空")。"""
        validator = FactValidator()
        fact = _make_fact([
            CharacterFact(name="悟空"),
            CharacterFact(name="孙悟空"),
        ])
        validated = validator.validate(fact)
        assert {ch.name for ch in validated.characters} == {"悟空", "孙悟空"}

    def test_normal_names_unaffected(self):
        """正常名字(互不包含)不受影响。"""
        validator = FactValidator()
        fact = _make_fact([
            CharacterFact(name="孙悟空"),
            CharacterFact(name="唐僧"),
            CharacterFact(name="猪八戒"),
        ])
        validated = validator.validate(fact)
        assert {ch.name for ch in validated.characters} == {"孙悟空", "唐僧", "猪八戒"}


# ── A5: 幻觉层审别名 ──


class MockLLM:
    """Mock LLM:按 verdicts_map 返回裁决 {name: (is_real, confidence, reason)}。"""

    def __init__(self, verdicts_map: dict | None = None):
        self.verdicts_map = verdicts_map or {}
        self.calls = 0

    async def generate(self, system, prompt, format=None, **kw):
        self.calls += 1
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


class TestAliasCandidates:
    """别名候选挑选:原文不可定位且非白名单的别名进入送审。"""

    def test_ungrounded_alias_is_candidate(self):
        fact = _make_fact([
            CharacterFact(name="孙悟空", new_aliases=["美猴王", "假雷公"]),
        ])
        # 原文有 "美猴王",无 "假雷公"
        cands = find_alias_candidates(fact, CHAPTER_TEXT)
        assert cands == {"孙悟空": ["假雷公"]}

    def test_protected_alias_not_candidate(self):
        fact = _make_fact([
            CharacterFact(name="孙悟空", new_aliases=["假雷公"]),
        ])
        cands = find_alias_candidates(fact, CHAPTER_TEXT, protected_names={"假雷公"})
        assert cands == {}


class TestApplyAliasVerdicts:
    """别名裁决处置:高置信幻觉别名从 new_aliases 剔除,character 不动。"""

    def test_high_confidence_alias_removed(self):
        fact = _make_fact([
            CharacterFact(name="孙悟空", new_aliases=["美猴王", "假雷公"]),
        ])
        new_fact, actions = apply_alias_verdicts(
            fact, {"孙悟空": ["假雷公"]},
            {"假雷公": {"is_real": False, "confidence": "high", "reason": "原文无"}},
        )
        assert new_fact.characters[0].new_aliases == ["美猴王"]
        assert new_fact.characters[0].name == "孙悟空"  # character 本身不动
        assert actions[0]["action"] == "alias_removed"
        assert actions[0]["owner"] == "孙悟空"

    def test_low_confidence_alias_kept_as_suspect(self):
        fact = _make_fact([
            CharacterFact(name="孙悟空", new_aliases=["假雷公"]),
        ])
        new_fact, actions = apply_alias_verdicts(
            fact, {"孙悟空": ["假雷公"]},
            {"假雷公": {"is_real": False, "confidence": "low", "reason": "拿不准"}},
        )
        assert new_fact is fact  # 无剔除时原样返回
        assert actions[0]["action"] == "alias_suspect"


@pytest.mark.asyncio
async def test_review_removes_hallucinated_alias_and_audits(review_on, tmp_path):
    """端到端:幻觉别名被 LLM 判死(high)后从 new_aliases 剔除并落审计日志。"""
    log_path = tmp_path / "hallucination_log.jsonl"
    llm = MockLLM({"假雷公": (False, "high", "原文通篇无此称呼,系幻觉")})
    fact = _make_fact([
        CharacterFact(name="孙悟空", new_aliases=["美猴王", "假雷公"]),
        CharacterFact(name="唐僧"),
    ])
    new_fact = await review_chapter_characters(
        fact,
        chapter_text=CHAPTER_TEXT,
        llm=llm,
        novel_id="xiyouji",
        chapter_id=32,
        log_path=log_path,
        record_cost=False,
    )
    wukong = next(ch for ch in new_fact.characters if ch.name == "孙悟空")
    assert wukong.new_aliases == ["美猴王"]
    assert llm.calls == 1  # 别名与人物同一次调用 (NFR-2)

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["prompt_version"] == hr.PROMPT_VERSION
    assert record["alias_candidates"] == {"孙悟空": ["假雷公"]}
    alias_action = next(a for a in record["actions"] if a["name"] == "假雷公")
    assert alias_action["action"] == "alias_removed"
    assert alias_action["owner"] == "孙悟空"


@pytest.mark.asyncio
async def test_review_keeps_real_alias(review_on, tmp_path):
    """LLM 判真实的别名保留(不误杀),审计记 alias_confirmed。"""
    log_path = tmp_path / "log.jsonl"
    llm = MockLLM({"假雷公": (True, "medium", "上下文可对应的戏称")})
    fact = _make_fact([
        CharacterFact(name="孙悟空", new_aliases=["假雷公"]),
    ])
    new_fact = await review_chapter_characters(
        fact, chapter_text=CHAPTER_TEXT, llm=llm,
        novel_id="xiyouji", chapter_id=32,
        log_path=log_path, record_cost=False,
    )
    assert new_fact.characters[0].new_aliases == ["假雷公"]
    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["actions"][0]["action"] == "alias_confirmed"


@pytest.mark.asyncio
async def test_review_alias_only_candidates_still_calls_llm(review_on, tmp_path):
    """人物全部可定位、仅别名可疑时也发起判定(别名链不再零校验)。"""
    llm = MockLLM({"假雷公": (False, "high", "幻觉")})
    fact = _make_fact([
        CharacterFact(name="孙悟空", new_aliases=["假雷公"]),
        CharacterFact(name="唐僧"),
    ])
    new_fact = await review_chapter_characters(
        fact, chapter_text=CHAPTER_TEXT, llm=llm,
        log_path=tmp_path / "log.jsonl", record_cost=False,
    )
    assert llm.calls == 1
    assert new_fact.characters[0].new_aliases == []
