"""Integration tests for the naming pipeline: EntityDict → NameResolver → AliasResolver.

These tests verify that ALL components in the naming pipeline produce consistent
results when composed together. This is the test layer that was missing when
v0.70 introduced NameResolver and caused canonical name regression.

No DB, no LLM — pure data flow tests using constructed fixtures.
"""

import json
from collections import Counter
from unittest.mock import patch

import pytest
import pytest_asyncio

import src.db.entity_override_store as entity_override_store_mod
import src.services.alias_resolver as alias_resolver_mod
import src.services.visualization_service as visualization_mod
from src.extraction.name_resolver import NameResolver
from src.models.chapter_fact import (
    ChapterFact, CharacterFact, RelationshipFact, EventFact,
)
from src.models.entity_dict import EntityDictEntry
from src.services.alias_resolver import build_alias_map
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


# ── Story 2.1 (cont.): AliasResolver.build_alias_map + graph output ──────
#
# The tests above cover NameResolver in isolation. The tests below run the
# REAL AliasResolver and visualization_service code paths against an
# in-memory SQLite DB, closing the loop: EntityDict → alias_map → graph
# node names. No LLM involved.


class _NonClosingConnection:
    """Proxy that prevents the service under test from closing the shared DB."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass  # no-op — the memory_db fixture manages the lifecycle


def _make_conn_factory(memory_db):
    async def _factory():
        return _NonClosingConnection(memory_db)
    return _factory


async def _seed_naming_db(db, novel_id, dict_entries, facts,
                          title="命名管线测试小说"):
    """Insert novel + entity_dictionary + chapters + chapter_facts rows.

    The neutral title keeps person_knowledge_prior out of the picture so the
    tests exercise the pure Union-Find + pick_canonical pipeline.
    """
    await db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", (novel_id, title))
    for entry in dict_entries:
        await db.execute(
            "INSERT INTO entity_dictionary "
            "(novel_id, name, entity_type, frequency, aliases, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (novel_id, entry.name, entry.entity_type, entry.frequency,
             json.dumps(entry.aliases, ensure_ascii=False), entry.source),
        )
    for fact in facts:
        cur = await db.execute(
            "INSERT INTO chapters (novel_id, chapter_num, title, content) "
            "VALUES (?, ?, ?, ?)",
            (novel_id, fact.chapter_id, f"第{fact.chapter_id}回", "正文……"),
        )
        await db.execute(
            "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json) "
            "VALUES (?, ?, ?)",
            (novel_id, cur.lastrowid,
             json.dumps(fact.model_dump(), ensure_ascii=False)),
        )
    await db.commit()


@pytest_asyncio.fixture
async def alias_db(memory_db):
    """In-memory DB wired into alias_resolver + entity_override_store."""
    factory = _make_conn_factory(memory_db)
    alias_resolver_mod._alias_cache.clear()
    with patch("src.services.alias_resolver.get_connection", factory), \
         patch("src.db.entity_override_store.get_connection", factory):
        yield memory_db
    alias_resolver_mod._alias_cache.clear()


class TestBuildAliasMapIntegration:
    """build_alias_map() output canonicals must match expectations (Story 2.1).

    Runs the real Union-Find + name_authority.pick_canonical pipeline over a
    simulated 西游记 entity dictionary + 3 chapters of facts.
    """

    NOVEL = "test-alias-map"

    @pytest.mark.asyncio
    async def test_alias_map_canonical_names(self, alias_db):
        await _seed_naming_db(alias_db, self.NOVEL,
                              _xiyouji_entity_dict(), _xiyouji_chapter_facts())
        alias_map = await build_alias_map(self.NOVEL)

        expectations = {
            # 高频常用名 vs 低频正式名
            "陈玄奘": "唐僧",
            "唐三藏": "唐僧",
            # 同一人物的别名正确合并 (孙悟空 = 行者 = 大圣)
            "猴王": "孙悟空",
            "行者": "孙悟空",
            "大圣": "孙悟空",
            "齐天大圣": "孙悟空",
            "美猴王": "孙悟空",
            # 其余主角
            "猪刚鬣": "猪八戒",
            # 注: "天蓬元帅" 是元帅头衔, alias_safety_level=0 被安全层有意拦截
            "沙和尚": "沙僧",
            "沙悟净": "沙僧",
            "罗刹女": "铁扇公主",
        }
        failures = [
            f"  - alias '{a}': expected canonical '{e}', "
            f"got '{alias_map.get(a)}' (source: simulated 西游记 fixture)"
            for a, e in expectations.items()
            if alias_map.get(a) != e
        ]
        assert not failures, (
            f"{len(failures)} alias→canonical mismatches:\n" + "\n".join(failures)
        )

    @pytest.mark.asyncio
    async def test_canonical_names_not_self_mapped(self, alias_db):
        """alias_map contract: canonical names must not map to themselves."""
        await _seed_naming_db(alias_db, self.NOVEL,
                              _xiyouji_entity_dict(), _xiyouji_chapter_facts())
        alias_map = await build_alias_map(self.NOVEL)

        for canonical in ["孙悟空", "唐僧", "猪八戒", "沙僧", "牛魔王", "铁扇公主"]:
            assert canonical not in alias_map, \
                f"Canonical '{canonical}' maps to itself/other: " \
                f"{canonical} → {alias_map.get(canonical)}"

    @pytest.mark.asyncio
    async def test_generic_terms_never_in_alias_map(self, alias_db):
        """泛称 (师父/长老/呆子) must be blocked before entering alias_map."""
        await _seed_naming_db(alias_db, self.NOVEL,
                              _xiyouji_entity_dict(), _xiyouji_chapter_facts())
        alias_map = await build_alias_map(self.NOVEL)

        generic_terms = {"师父", "长老", "呆子", "菩萨", "大王", "妖精"}
        leaked_keys = generic_terms & set(alias_map.keys())
        leaked_vals = generic_terms & set(alias_map.values())
        assert not leaked_keys, f"Generic terms as alias_map keys: {leaked_keys}"
        assert not leaked_vals, \
            f"Generic terms as canonical targets: {leaked_vals}"

    @pytest.mark.asyncio
    async def test_similar_names_never_merged(self, alias_db):
        """阮小二 ≠ 阮小五 ≠ 阮小七 — structurally similar names must stay
        separate even when a (buggy) prescan LLM declares them aliases.

        This exercises the _similar_name_conflict guard (merge blocker) via
        the real build_alias_map code path, at BOTH the dict stage and the
        chapter-fact stage.
        """
        dict_entries = [
            # Simulates a prescan LLM error: 阮小二 declared with 阮小五 as alias
            EntityDictEntry(name="阮小二", entity_type="person", frequency=50,
                            aliases=["阮小五"], source="freq"),
            EntityDictEntry(name="阮小五", entity_type="person", frequency=40,
                            aliases=["阮小七"], source="freq"),
            EntityDictEntry(name="阮小七", entity_type="person", frequency=30,
                            aliases=[], source="freq"),
        ]
        facts = [
            ChapterFact(chapter_id=1, novel_id=self.NOVEL, characters=[
                # LLM per-chapter error: claims 阮小七 is an alias of 阮小二
                CharacterFact(name="阮小二", new_aliases=["阮小七"]),
                CharacterFact(name="阮小五"),
            ], relationships=[
                RelationshipFact(person_a="阮小二", person_b="阮小五",
                                 relation_type="兄弟"),
            ]),
        ]
        await _seed_naming_db(alias_db, self.NOVEL, dict_entries, facts)
        alias_map = await build_alias_map(self.NOVEL)

        brothers = {"阮小二", "阮小五", "阮小七"}
        merged = {
            a: c for a, c in alias_map.items()
            if a in brothers or c in brothers
        }
        assert not merged, (
            "Similar-named distinct characters were merged "
            "(阮氏兄弟 regression):\n"
            + "\n".join(f"  - {a} → {c}" for a, c in merged.items())
        )


class TestGraphOutputNames:
    """visualization_service.get_graph_data() must emit canonical node names
    after applying alias_map (Story 2.1, link 3 of the chain)."""

    NOVEL = "test-graph-names"

    @pytest_asyncio.fixture
    async def graph_db(self, memory_db):
        factory = _make_conn_factory(memory_db)
        alias_resolver_mod._alias_cache.clear()

        async def _no_ungrounded(novel_id, names, alias_map):
            return set()

        async def _no_override_targets(novel_id):
            return {}

        with patch("src.services.visualization_service.get_connection", factory), \
             patch("src.services.alias_resolver.get_connection", factory), \
             patch("src.db.entity_override_store.get_connection", factory), \
             patch("src.services.hallucination_filter.get_ungrounded_persons",
                   _no_ungrounded), \
             patch("src.services.alias_resolver.get_override_targets",
                   _no_override_targets):
            yield memory_db
        alias_resolver_mod._alias_cache.clear()

    @pytest.mark.asyncio
    async def test_graph_nodes_use_canonical_names(self, graph_db):
        await _seed_naming_db(graph_db, self.NOVEL,
                              _xiyouji_entity_dict(), _xiyouji_chapter_facts())

        graph = await visualization_mod.get_graph_data(self.NOVEL, 1, 100)
        node_names = {n["name"] for n in graph["nodes"]}

        # Canonical names present
        for expected in ["孙悟空", "唐僧", "猪八戒", "牛魔王", "铁扇公主",
                         "菩提祖师"]:
            assert expected in node_names, \
                f"Expected canonical node '{expected}' missing from graph; " \
                f"nodes: {sorted(node_names)}"

        # Fragment/alias names must NOT appear as nodes
        fragments = {"猴王", "行者", "大圣", "三藏", "八戒", "陈玄奘",
                     "猪刚鬣", "沙和尚", "罗刹女"}
        leaked = fragments & node_names
        assert not leaked, \
            f"Fragment names appeared as graph nodes: {sorted(leaked)}"

    @pytest.mark.asyncio
    async def test_graph_node_aliases_tracked(self, graph_db):
        """Resolved-away names are recorded on the canonical node's aliases."""
        await _seed_naming_db(graph_db, self.NOVEL,
                              _xiyouji_entity_dict(), _xiyouji_chapter_facts())

        graph = await visualization_mod.get_graph_data(self.NOVEL, 1, 100)
        wukong = next(n for n in graph["nodes"] if n["name"] == "孙悟空")
        assert {"猴王", "行者", "大圣"} <= set(wukong["aliases"]), \
            f"孙悟空 node aliases incomplete: {wukong['aliases']}"

    @pytest.mark.asyncio
    async def test_graph_edges_use_canonical_names(self, graph_db):
        await _seed_naming_db(graph_db, self.NOVEL,
                              _xiyouji_entity_dict(), _xiyouji_chapter_facts())

        graph = await visualization_mod.get_graph_data(self.NOVEL, 1, 100)
        edges = {(e["source"], e["target"]) for e in graph["edges"]}

        # 三藏↔行者 师徒 must become 唐僧↔孙悟空 (edge endpoints are sorted)
        assert ("唐僧", "孙悟空") in edges, \
            f"Expected edge 唐僧—孙悟空 missing; edges: {sorted(edges)}"
        # 大圣↔牛魔王 结拜 must become 孙悟空↔牛魔王
        assert ("孙悟空", "牛魔王") in edges, \
            f"Expected edge 孙悟空—牛魔王 missing; edges: {sorted(edges)}"
        # No self-edges caused by alias resolution
        assert all(s != t for s, t in edges), \
            f"Self-edge after alias resolution: {sorted(edges)}"
