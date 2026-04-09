"""Tests for NameResolver — upstream character name unification.

Verifies that name variants are resolved to canonical forms in ChapterFact
before DB save, preventing alias fragmentation.
"""

import pytest
from collections import Counter

from src.extraction.name_resolver import NameResolver, _GENERIC_BLOCK
from src.models.chapter_fact import (
    ChapterFact, CharacterFact, RelationshipFact, EventFact,
)


class TestNameResolverMapping:
    """Test canonical mapping construction."""

    def _make_resolver_with_aliases(self, aliases: dict[str, list[str]]) -> NameResolver:
        """Helper: create resolver with manual alias entries."""
        nr = NameResolver()
        for canonical, alias_list in aliases.items():
            for alias in alias_list:
                nr._canonical_map[alias] = canonical
        return nr

    def test_basic_mapping(self):
        nr = self._make_resolver_with_aliases({
            "孙悟空": ["行者", "孙大圣", "孙行者"],
        })
        assert nr._canonical_map["行者"] == "孙悟空"
        assert nr._canonical_map["孙大圣"] == "孙悟空"
        assert nr.mapping_count == 3

    def test_generic_blocked(self):
        """Generic terms should not enter canonical_map."""
        assert "哥哥" in _GENERIC_BLOCK
        assert "师父" in _GENERIC_BLOCK
        assert "外公" in _GENERIC_BLOCK

    def test_accumulate_from_chapter(self):
        """accumulate_from_chapter adds new aliases from chapter facts."""
        nr = NameResolver()
        fact = ChapterFact(chapter_id=1, novel_id="test",
            characters=[
                CharacterFact(name="孙悟空", new_aliases=["齐天大圣", "美猴王"]),
                CharacterFact(name="唐僧", new_aliases=["师父"]),  # 师父 is generic, should be blocked
            ],
            relationships=[],
        )
        nr.accumulate_from_chapter(fact)
        assert nr._canonical_map.get("齐天大圣") == "孙悟空"
        assert nr._canonical_map.get("美猴王") == "孙悟空"
        assert "师父" not in nr._canonical_map  # blocked


class TestCanonicalFromDictFrequency:
    """Test that load_from_entity_dictionary picks highest-frequency name as canonical."""

    def test_prefer_common_name_over_formal(self):
        """唐僧(freq=829) should beat 陈玄奘(freq=14) as canonical."""
        from src.models.entity_dict import EntityDictEntry
        nr = NameResolver()
        nr.load_from_entity_dictionary([
            EntityDictEntry(name="陈玄奘", entity_type="person", frequency=14,
                            aliases=["唐僧", "三藏法师"], source="llm"),
            EntityDictEntry(name="唐僧", entity_type="person", frequency=829,
                            aliases=["三藏", "唐三藏", "御弟"], source="freq"),
        ])
        # 陈玄奘 should map to 唐僧 (higher freq), not the other way around
        assert nr._canonical_map.get("陈玄奘") == "唐僧"
        assert "唐僧" not in nr._canonical_map  # 唐僧 IS the canonical
        assert nr._canonical_map.get("三藏") == "唐僧"
        assert nr._canonical_map.get("三藏法师") == "唐僧"

    def test_prefer_common_name_zhu_bajie(self):
        """猪八戒(freq=182) should beat 猪刚鬣(freq=5)."""
        from src.models.entity_dict import EntityDictEntry
        nr = NameResolver()
        nr.load_from_entity_dictionary([
            EntityDictEntry(name="猪刚鬣", entity_type="person", frequency=5,
                            aliases=["猪八戒", "八戒", "呆子"], source="llm"),
            EntityDictEntry(name="猪八戒", entity_type="person", frequency=182,
                            aliases=["八戒", "天蓬元帅"], source="freq"),
        ])
        assert nr._canonical_map.get("猪刚鬣") == "猪八戒"
        assert "猪八戒" not in nr._canonical_map

    def test_single_entry_uses_entry_name(self):
        """When no alias is a primary entry, use entry.name as canonical."""
        from src.models.entity_dict import EntityDictEntry
        nr = NameResolver()
        nr.load_from_entity_dictionary([
            EntityDictEntry(name="孙悟空", entity_type="person", frequency=152,
                            aliases=["行者", "猴王", "大圣"], source="freq"),
        ])
        assert nr._canonical_map.get("行者") == "孙悟空"
        assert nr._canonical_map.get("猴王") == "孙悟空"
        assert "孙悟空" not in nr._canonical_map

    def test_non_person_entries_ignored(self):
        """Non-person entries should not create mappings."""
        from src.models.entity_dict import EntityDictEntry
        nr = NameResolver()
        nr.load_from_entity_dictionary([
            EntityDictEntry(name="花果山", entity_type="location", frequency=50,
                            aliases=["水帘洞"], source="freq"),
        ])
        assert nr.mapping_count == 0


class TestNameResolverResolve:
    """Test that resolve() correctly rewrites ChapterFact fields."""

    def _make_fact(self, chars, rels):
        return ChapterFact(chapter_id=1, novel_id="test",
            characters=[CharacterFact(name=n) for n in chars],
            relationships=[
                RelationshipFact(person_a=a, person_b=b, relation_type=t)
                for a, b, t in rels
            ],
        )

    def test_resolve_character_names(self):
        nr = NameResolver()
        nr._canonical_map = {"行者": "孙悟空", "三藏": "唐僧"}

        fact = self._make_fact(
            chars=["行者", "三藏", "猪八戒"],
            rels=[],
        )
        resolved = nr.resolve(fact)
        names = [c.name for c in resolved.characters]
        assert "孙悟空" in names
        assert "唐僧" in names
        assert "行者" not in names

    def test_resolve_relationship_persons(self):
        nr = NameResolver()
        nr._canonical_map = {"行者": "孙悟空", "三藏": "唐僧"}

        fact = self._make_fact(
            chars=[],
            rels=[("三藏", "行者", "师徒")],
        )
        resolved = nr.resolve(fact)
        rel = resolved.relationships[0]
        assert rel.person_a == "唐僧"
        assert rel.person_b == "孙悟空"

    def test_resolve_event_participants(self):
        nr = NameResolver()
        nr._canonical_map = {"八戒": "猪八戒"}

        fact = ChapterFact(chapter_id=1, novel_id="test",
            characters=[],
            relationships=[],
            events=[EventFact(summary="test", type="战斗", participants=["八戒", "沙僧"])],
        )
        resolved = nr.resolve(fact)
        assert resolved.events[0].participants == ["猪八戒", "沙僧"]

    def test_no_mapping_no_change(self):
        nr = NameResolver()
        fact = self._make_fact(
            chars=["孙悟空", "唐僧"],
            rels=[("孙悟空", "唐僧", "师徒")],
        )
        resolved = nr.resolve(fact)
        assert [c.name for c in resolved.characters] == ["孙悟空", "唐僧"]

    def test_generic_not_mapped(self):
        """Generics like 哥哥 should never be in canonical_map even if LLM says so."""
        nr = NameResolver()
        fact = ChapterFact(chapter_id=1, novel_id="test",
            characters=[
                CharacterFact(name="孙悟空", new_aliases=["哥哥", "大圣"]),
            ],
            relationships=[],
        )
        nr.accumulate_from_chapter(fact)
        assert "哥哥" not in nr._canonical_map
        assert nr._canonical_map.get("大圣") == "孙悟空"


class TestGenericFiltering:
    """Test that generic person/location filtering catches review findings."""

    def test_person_generics_from_review(self):
        from src.extraction.fact_validator import _is_generic_person
        # These should all be filtered (from 西游记 review)
        for name in ["黄门官", "文武多官", "四值功曹", "八大金刚",
                      "四大天师", "巡海夜叉", "比丘僧", "龙子龙孙"]:
            result = _is_generic_person(name)
            assert result is not None, f"{name} should be filtered but passed"

    def test_person_valid_from_review(self):
        from src.extraction.fact_validator import _is_generic_person
        # These should NOT be filtered
        for name in ["孙悟空", "猪八戒", "唐僧", "牛魔王", "玉皇大帝",
                      "太白金星", "托塔李天王", "观音菩萨"]:
            result = _is_generic_person(name)
            assert result is None, f"{name} should pass but was filtered: {result}"

    def test_location_generics_from_review(self):
        from src.extraction.fact_validator import _is_generic_location
        # These should all be filtered
        for name in ["方丈", "禅堂", "高山", "山凹", "客房", "天",
                      "地", "龙床", "天罗地网", "格子", "檐柱"]:
            result = _is_generic_location(name)
            assert result is not None, f"{name} should be filtered but passed"

    def test_location_valid_from_review(self):
        from src.extraction.fact_validator import _is_generic_location
        # These should NOT be filtered
        for name in ["花果山", "水帘洞", "灵霄殿", "天庭", "大雷音寺",
                      "长安城", "南天门"]:
            result = _is_generic_location(name)
            assert result is None, f"{name} should pass but was filtered: {result}"

    def test_alias_safety_blocks_generics(self):
        from src.services.alias_resolver import _alias_safety_level
        # Hard-blocked (level 0)
        for alias in ["哥哥", "师父", "外公", "徒弟", "大爷",
                       "老和尚", "贤弟", "那长老"]:
            level = _alias_safety_level(alias)
            assert level == 0, f"{alias} should be level 0 but got {level}"

    def test_alias_safety_allows_real_aliases(self):
        from src.services.alias_resolver import _alias_safety_level
        # Should be safe (level 2)
        for alias in ["齐天大圣", "美猴王", "弼马温", "金蝉子"]:
            level = _alias_safety_level(alias)
            assert level >= 1, f"{alias} should be safe but got level {level}"


class TestTierOverrides:
    """Test tier classification overrides from review."""

    def test_tianting_region(self):
        from src.services.geo_skills.tier_classifier import TierClassifier
        updates = TierClassifier._multi_feature_refine(
            tiers={"天庭": "continent"},
            parents={"天庭": "天界"},
            frequencies=Counter({"天庭": 29}),
            children_count={"天庭": 7},
        )
        assert updates.get("天庭") == "region"

    def test_buzhou_continent(self):
        from src.services.geo_skills.tier_classifier import TierClassifier
        updates = TierClassifier._multi_feature_refine(
            tiers={"南赡部洲": "site"},
            parents={"南赡部洲": "主世界"},
            frequencies=Counter({"南赡部洲": 7}),
            children_count={"南赡部洲": 5},
        )
        assert updates.get("南赡部洲") == "continent"

    def test_qitian_fu_site(self):
        from src.services.geo_skills.tier_classifier import TierClassifier
        updates = TierClassifier._multi_feature_refine(
            tiers={"齐天大圣府": "kingdom"},
            parents={"齐天大圣府": "天庭"},
            frequencies=Counter({"齐天大圣府": 3}),
            children_count={},
        )
        assert updates.get("齐天大圣府") == "site"
