"""Integration tests for the naming pipeline: EntityDict → NameResolver → AliasResolver.

These tests verify that ALL components in the naming pipeline produce consistent
results when composed together. This is the test layer that was missing when
v0.70 introduced NameResolver and caused canonical name regression.

No DB, no LLM — pure data flow tests using constructed fixtures.
"""

import pytest
from collections import Counter

from src.extraction.name_resolver import NameResolver
from src.models.chapter_fact import (
    ChapterFact, CharacterFact, RelationshipFact, EventFact,
)
from src.models.entity_dict import EntityDictEntry
from src.services.name_authority import pick_canonical


# ── Fixtures ──────────────────────────────────────────────────


def _xiyouji_entity_dict() -> list[EntityDictEntry]:
    """Simulated 西游记 entity dictionary (pre-scan output)."""
    return [
        EntityDictEntry(name="孙悟空", entity_type="person", frequency=152,
                        aliases=["行者", "大圣", "齐天大圣", "美猴王", "猴王"],
                        source="freq"),
        EntityDictEntry(name="唐僧", entity_type="person", frequency=829,
                        aliases=["三藏", "唐三藏", "御弟", "长老"],
                        source="freq"),
        EntityDictEntry(name="陈玄奘", entity_type="person", frequency=14,
                        aliases=["唐僧", "三藏法师", "金蝉子"],
                        source="llm"),
        EntityDictEntry(name="猪八戒", entity_type="person", frequency=182,
                        aliases=["八戒", "天蓬元帅", "呆子", "猪刚鬣"],
                        source="freq"),
        EntityDictEntry(name="沙僧", entity_type="person", frequency=94,
                        aliases=["沙和尚", "沙悟净", "卷帘大将"],
                        source="freq"),
        EntityDictEntry(name="牛魔王", entity_type="person", frequency=30,
                        aliases=["大力牛魔王"],
                        source="freq"),
        EntityDictEntry(name="铁扇公主", entity_type="person", frequency=25,
                        aliases=["罗刹女"],
                        source="freq"),
    ]


def _xiyouji_chapter_facts() -> list[ChapterFact]:
    """Simulated chapter facts with name variants."""
    return [
        ChapterFact(chapter_id=1, novel_id="test", characters=[
            CharacterFact(name="猴王", new_aliases=["石猴"]),
            CharacterFact(name="菩提祖师"),
        ], relationships=[
            RelationshipFact(person_a="猴王", person_b="菩提祖师", relation_type="师徒"),
        ]),
        ChapterFact(chapter_id=15, novel_id="test", characters=[
            CharacterFact(name="行者", new_aliases=["孙行者"]),
            CharacterFact(name="三藏"),
            CharacterFact(name="八戒"),
        ], relationships=[
            RelationshipFact(person_a="三藏", person_b="行者", relation_type="师徒"),
            RelationshipFact(person_a="行者", person_b="八戒", relation_type="师兄弟"),
        ]),
        ChapterFact(chapter_id=61, novel_id="test", characters=[
            CharacterFact(name="大圣"),
            CharacterFact(name="牛魔王"),
            CharacterFact(name="铁扇公主", new_aliases=["罗刹女"]),
        ], relationships=[
            RelationshipFact(person_a="大圣", person_b="牛魔王", relation_type="结拜兄弟"),
            RelationshipFact(person_a="牛魔王", person_b="铁扇公主", relation_type="夫妻"),
        ]),
    ]


# ── Story 2.1: Pipeline integration tests ────────────────────


class TestNameResolverPipelineIntegration:
    """Test that NameResolver correctly resolves names using entity dict."""

    def test_resolver_uses_common_name_as_canonical(self):
        """The most critical test: NameResolver must pick 唐僧 over 陈玄奘."""
        nr = NameResolver()
        nr.load_from_entity_dictionary(_xiyouji_entity_dict())

        # 陈玄奘 should map to 唐僧 (higher freq)
        assert nr._canonical_map.get("陈玄奘") == "唐僧"
        # 猪刚鬣 should map to 猪八戒
        assert nr._canonical_map.get("猪刚鬣") == "猪八戒"
        # 猴王 should map to 孙悟空
        assert nr._canonical_map.get("猴王") == "孙悟空"
        # 唐僧 should NOT be in the map (it IS the canonical)
        assert "唐僧" not in nr._canonical_map

    def test_resolver_resolves_chapter_fact_names(self):
        """After resolve(), chapter facts should use canonical names."""
        nr = NameResolver()
        nr.load_from_entity_dictionary(_xiyouji_entity_dict())

        facts = _xiyouji_chapter_facts()
        for fact in facts:
            nr.resolve(fact)
            nr.accumulate_from_chapter(fact)

        # Chapter 1: "猴王" → "孙悟空"
        assert facts[0].characters[0].name == "孙悟空"

        # Chapter 15: "行者" → "孙悟空", "三藏" → "唐僧", "八戒" → "猪八戒"
        ch15_names = {c.name for c in facts[1].characters}
        assert "孙悟空" in ch15_names
        assert "唐僧" in ch15_names
        assert "猪八戒" in ch15_names
        assert "行者" not in ch15_names
        assert "三藏" not in ch15_names

        # Chapter 15 relationships should also be resolved
        rel = facts[1].relationships[0]
        assert rel.person_a == "唐僧"
        assert rel.person_b == "孙悟空"

    def test_resolver_resolves_chapter61_aliases(self):
        """Chapter 61: "大圣" → "孙悟空"."""
        nr = NameResolver()
        nr.load_from_entity_dictionary(_xiyouji_entity_dict())

        facts = _xiyouji_chapter_facts()
        for fact in facts:
            nr.resolve(fact)
            nr.accumulate_from_chapter(fact)

        ch61_names = {c.name for c in facts[2].characters}
        assert "孙悟空" in ch61_names
        assert "大圣" not in ch61_names
        # 牛魔王 stays (it IS its own canonical)
        assert "牛魔王" in ch61_names

    def test_generic_terms_never_become_canonical(self):
        """泛称 (师父, 长老, 呆子) must never appear as canonical targets."""
        nr = NameResolver()
        nr.load_from_entity_dictionary(_xiyouji_entity_dict())

        # None of these should be in the canonical_map as VALUES
        canonicals = set(nr._canonical_map.values())
        generic_terms = {"师父", "长老", "呆子", "菩萨", "大王", "哥哥",
                         "妖精", "老和尚"}
        for term in generic_terms:
            assert term not in canonicals, \
                f"Generic term '{term}' became a canonical target"

    def test_different_characters_not_merged(self):
        """Distinct characters must remain separate."""
        nr = NameResolver()
        nr.load_from_entity_dictionary(_xiyouji_entity_dict())

        facts = _xiyouji_chapter_facts()
        for fact in facts:
            nr.resolve(fact)

        # 牛魔王 and 孙悟空 must remain separate
        ch61_names = [c.name for c in facts[2].characters]
        assert "孙悟空" in ch61_names
        assert "牛魔王" in ch61_names
        assert len(set(ch61_names)) == len(ch61_names)  # no duplicates


# ── Story 2.3: Canonical regression guards ────────────────────


class TestCanonicalRegressionGuards:
    """Hardcoded assertions for core character canonical names.

    These tests act as CI guardrails: any code change that causes
    孙悟空 to become 猴王 will break CI immediately.
    """

    # ── 西游记 ──

    XIYOUJI_EXPECTATIONS = {
        "孙悟空": (["孙悟空", "行者", "猴王", "大圣", "齐天大圣", "悟空"],
                   {"孙悟空": 152, "行者": 300, "猴王": 89, "大圣": 100,
                    "齐天大圣": 20, "悟空": 374}),
        "唐僧": (["唐僧", "三藏", "陈玄奘", "唐三藏", "御弟"],
                 {"唐僧": 829, "三藏": 200, "陈玄奘": 14, "唐三藏": 30, "御弟": 5}),
        "猪八戒": (["猪八戒", "八戒", "猪刚鬣", "天蓬元帅"],
                   {"猪八戒": 182, "八戒": 1700, "猪刚鬣": 5, "天蓬元帅": 10}),
        "沙僧": (["沙僧", "沙和尚", "沙悟净"],
                 {"沙僧": 94, "沙和尚": 150, "沙悟净": 10}),
    }

    @pytest.mark.parametrize("expected,data", XIYOUJI_EXPECTATIONS.items(),
                             ids=XIYOUJI_EXPECTATIONS.keys())
    def test_xiyouji_canonical(self, expected, data):
        members, freq = data
        result = pick_canonical(members, freq)
        assert result == expected, \
            f"Expected canonical '{expected}' but got '{result}'"

    # ── 红楼梦 ──

    HONGLOU_EXPECTATIONS = {
        "贾宝玉": (["贾宝玉", "宝玉", "宝二爷"],
                   {"贾宝玉": 500, "宝玉": 2000, "宝二爷": 100}),
        "林黛玉": (["林黛玉", "黛玉", "林妹妹", "颦儿"],
                   {"林黛玉": 300, "黛玉": 1500, "林妹妹": 80, "颦儿": 20}),
        "薛宝钗": (["薛宝钗", "宝钗", "宝姐姐"],
                   {"薛宝钗": 200, "宝钗": 800, "宝姐姐": 50}),
        "王熙凤": (["王熙凤", "凤姐", "凤丫头", "琏二奶奶"],
                   {"王熙凤": 200, "凤姐": 800, "凤丫头": 50, "琏二奶奶": 30}),
    }

    @pytest.mark.parametrize("expected,data", HONGLOU_EXPECTATIONS.items(),
                             ids=HONGLOU_EXPECTATIONS.keys())
    def test_honglou_canonical(self, expected, data):
        members, freq = data
        result = pick_canonical(members, freq)
        assert result == expected, \
            f"Expected canonical '{expected}' but got '{result}'"

    # ── 水浒传 ──

    SHUIHU_EXPECTATIONS = {
        "宋江": (["宋江", "宋公明", "及时雨", "呼保义"],
                 {"宋江": 800, "宋公明": 30, "及时雨": 20, "呼保义": 15}),
        "林冲": (["林冲", "豹子头"],
                 {"林冲": 300, "豹子头": 40}),
        "武松": (["武松", "武二郎", "行者武松"],
                 {"武松": 500, "武二郎": 30, "行者武松": 10}),
    }

    @pytest.mark.parametrize("expected,data", SHUIHU_EXPECTATIONS.items(),
                             ids=SHUIHU_EXPECTATIONS.keys())
    def test_shuihu_canonical(self, expected, data):
        members, freq = data
        result = pick_canonical(members, freq)
        assert result == expected, \
            f"Expected canonical '{expected}' but got '{result}'"

    def test_distinct_characters_never_share_canonical(self):
        """Core characters from different groups must never resolve to same canonical."""
        # Each group should produce a different canonical
        all_groups = {**self.XIYOUJI_EXPECTATIONS,
                      **self.HONGLOU_EXPECTATIONS,
                      **self.SHUIHU_EXPECTATIONS}
        canonicals = []
        for expected, (members, freq) in all_groups.items():
            result = pick_canonical(members, freq)
            canonicals.append(result)
        # All canonicals must be unique
        assert len(set(canonicals)) == len(canonicals), \
            f"Duplicate canonicals detected: {canonicals}"
