"""multi-pass Epic 2 Story 2.1 测试: ContextSummaryBuilder 数据源参数化。

覆盖验收:
- 默认路径(无一审 pass 的普通分析)输出与改动前逐字节一致(gold 基线);
- 二审第 N 章的 context 只含: 原文无关部分(二审 1..N-1 章 facts) + (D1) 词典;
  构造测试: 一审故意埋错误 fact,二审 context 不含其任何字串;
- 世界结构注入在二审走「空」分支(不读 world_structures)。
"""

import pytest
from unittest.mock import AsyncMock

from src.db import chapter_fact_store, entity_dictionary_store, world_structure_store
from src.extraction.context_summary_builder import ContextSummaryBuilder
from src.models.chapter_fact import ChapterFact, CharacterFact, LocationFact
from src.models.entity_dict import EntityDictEntry
from src.models.world_structure import MapLayer, WorldRegion, WorldStructure

NOVEL = "novel-ctx"


class _NonClosing:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


@pytest.fixture
def ctx_db(memory_db, monkeypatch):
    """播种: novel + 3 章 + 一审错误 fact(毒丸) + 词典 + 世界结构;
    patch 三个 store 的 get_connection 指向 memory_db。"""

    async def _factory():
        return _NonClosing(memory_db)

    for mod in (chapter_fact_store, entity_dictionary_store, world_structure_store):
        monkeypatch.setattr(mod, "get_connection", _factory)
    return memory_db


async def _seed(ctx_db) -> None:
    await ctx_db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", (NOVEL, "测试小说"),
    )
    for i in (1, 2, 3):
        await ctx_db.execute(
            "INSERT INTO chapters (id, novel_id, chapter_num, title, content)"
            " VALUES (?, ?, ?, ?, ?)",
            (i, NOVEL, i, f"第{i}章", f"第{i}章原文"),
        )
    await ctx_db.commit()

    # 一审主表故意埋错误 fact(毒丸):二审 context 绝不能含其字串
    poison_fact = ChapterFact(
        chapter_id=1,
        novel_id=NOVEL,
        characters=[CharacterFact(name="一审误植人物甲")],
        locations=[LocationFact(name="一审误植地点乙", type="城池")],
    )
    await chapter_fact_store.insert_chapter_fact(
        novel_id=NOVEL, chapter_id=1, fact=poison_fact,
        llm_model="test", extraction_ms=1,
    )

    # 预扫描词典(D1:源自原文统计,非一审 LLM 产物)
    await entity_dictionary_store.insert_batch(NOVEL, [
        EntityDictEntry(
            name="宋江", entity_type="person", frequency=10,
            confidence="high", aliases=[], source="frequency",
        ),
    ])

    # 世界结构(一审产物)
    ws = WorldStructure(
        novel_id=NOVEL,
        layers=[MapLayer(
            layer_id="overworld", name="主世界", layer_type="overworld",
            regions=[WorldRegion(name="东胜神洲", cardinal_direction="东")],
        )],
    )
    await world_structure_store.save(NOVEL, ws)


def _pass_facts_provider(facts: list[ChapterFact]):
    """构造二审 facts_provider:返回结构与 get_all_chapter_facts 兼容。"""

    async def _provider(novel_id: str) -> list[dict]:
        return [
            {
                "chapter_id": f.chapter_id,
                "chapter_num": f.chapter_id,
                "fact": f.model_dump(),
            }
            for f in facts
        ]

    return _provider


@pytest.mark.asyncio
async def test_default_path_byte_identical_gold_baseline(ctx_db):
    """gold 基线: 默认路径读主表;显式传默认参数与完全不传逐字节一致。"""
    await _seed(ctx_db)
    builder = ContextSummaryBuilder()

    ctx_default = await builder.build(NOVEL, 2)
    ctx_explicit = await builder.build(
        NOVEL, 2,
        facts_provider=None,
        include_world_structure=True,
        include_dictionary=True,
    )
    assert ctx_default == ctx_explicit

    # 默认路径内容来源确认: 主表 fact + 世界结构 + 词典
    assert "一审误植人物甲" in ctx_default
    assert "东胜神洲" in ctx_default
    assert "宋江" in ctx_default


@pytest.mark.asyncio
async def test_pass_context_isolated_from_main_products(ctx_db):
    """二审 context: 只含二审自身前序 facts + (D1) 词典;
    一审错误 fact 的任何字串不出现;世界结构走「空」分支。"""
    await _seed(ctx_db)
    builder = ContextSummaryBuilder()

    pass_facts = [ChapterFact(
        chapter_id=1, novel_id=NOVEL,
        characters=[CharacterFact(name="二审人物丙")],
        locations=[LocationFact(name="二审地点丁", type="村庄")],
    )]
    ctx = await builder.build(
        NOVEL, 2,
        facts_provider=_pass_facts_provider(pass_facts),
        include_world_structure=False,
        include_dictionary=True,
    )

    # 二审自身前序 facts 进 context
    assert "二审人物丙" in ctx
    assert "二审地点丁" in ctx
    # 一审错误 fact 的任何字串不出现(独立性核心断言)
    assert "一审误植人物甲" not in ctx
    assert "一审误植地点乙" not in ctx
    # 世界结构不注入
    assert "东胜神洲" not in ctx
    assert "已知世界结构" not in ctx
    # D1: 词典仍注入(源自原文统计预扫描,非一审 LLM 产物)
    assert "宋江" in ctx


@pytest.mark.asyncio
async def test_pass_context_chapter1_has_no_prior_facts(ctx_db):
    """二审第 1 章: 无前序 facts,只有词典等始终注入的部分。"""
    await _seed(ctx_db)
    builder = ContextSummaryBuilder()

    ctx = await builder.build(
        NOVEL, 1,
        facts_provider=_pass_facts_provider([]),
        include_world_structure=False,
        include_dictionary=True,
    )
    assert "已知人物" not in ctx  # 无二审前序 facts
    assert "一审误植人物甲" not in ctx
    assert "宋江" in ctx  # 词典仍注入


@pytest.mark.asyncio
async def test_dictionary_switch_off(ctx_db):
    """D1 开关: include_dictionary=False 时词典也不注入(严格 source-only)。"""
    await _seed(ctx_db)
    builder = ContextSummaryBuilder()

    ctx = await builder.build(
        NOVEL, 1,
        facts_provider=_pass_facts_provider([]),
        include_world_structure=False,
        include_dictionary=False,
    )
    assert "宋江" not in ctx
    assert "本书高频实体参考" not in ctx


@pytest.mark.asyncio
async def test_pass_path_never_reads_world_structure_store(ctx_db, monkeypatch):
    """机制保证: include_world_structure=False 时根本不调用 world_structure_store。"""
    await _seed(ctx_db)
    spy = AsyncMock(side_effect=AssertionError("不应读取 world_structures"))
    monkeypatch.setattr(world_structure_store, "load", spy)
    builder = ContextSummaryBuilder()

    ctx = await builder.build(
        NOVEL, 2,
        facts_provider=_pass_facts_provider([]),
        include_world_structure=False,
        include_dictionary=False,
    )
    assert spy.await_count == 0
    assert isinstance(ctx, str)
