"""预扫描候选合并（issue #18）。

三层覆盖：
1. `merge_entries()` 纯函数：别名并集、频次求和、链式合并、环、失效引用、
   naming 优先级；
2. 服务层：override 落库 → 合并视图 → 撤销恢复；
3. API：GET 返回合并视图与 merges 列表、POST 合并、自合并/未知名/成环 400、
   DELETE 撤销。
"""

import pytest

from src.models.entity_dict import EntityDictEntry
from src.services.prescan_merge import (
    delete_merge,
    get_merged_dictionary,
    list_merges,
    merge_entries,
    save_merge,
)


def _entry(name, freq=10, etype="person", aliases=None, source="freq", conf="medium"):
    return EntityDictEntry(
        name=name, entity_type=etype, frequency=freq, confidence=conf,
        aliases=aliases or [], source=source,
    )


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------

def test_merge_two_entries_unions_aliases_and_sums_frequency():
    entries = [
        _entry("罗切斯特", freq=30, aliases=["罗先生"]),
        _entry("罗彻斯特", freq=20, aliases=["彻斯特"]),
        _entry("简爱", freq=40),
    ]
    merged, absorbed = merge_entries(
        entries, [{"source": "罗彻斯特", "target": "罗切斯特"}]
    )
    by_name = {e.name: e for e in merged}
    assert set(by_name) == {"罗切斯特", "简爱"}
    spec = by_name["罗切斯特"]
    assert spec.frequency == 50  # 30 + 20
    assert set(spec.aliases) == {"罗先生", "彻斯特", "罗彻斯特"}  # 被合并的名字进入别名
    assert absorbed == {"罗切斯特": ["罗彻斯特"]}
    # 排序：频次降序
    assert [e.name for e in merged] == ["罗切斯特", "简爱"]


def test_merge_chain_collapses_to_one_canonical():
    entries = [_entry("A", 10), _entry("B", 70), _entry("C", 30)]
    merged, absorbed = merge_entries(
        entries,
        [{"source": "A", "target": "B"}, {"source": "B", "target": "C"}],
    )
    assert len(merged) == 1
    # C 从未被吸收，是唯一候选 → canonical = C
    assert merged[0].name == "C"
    assert merged[0].frequency == 110
    assert absorbed == {"C": ["A", "B"]}


def test_cycle_collapses_without_infinite_loop():
    entries = [_entry("A", 10), _entry("B", 20)]
    merged, _ = merge_entries(
        entries, [{"source": "A", "target": "B"}, {"source": "B", "target": "A"}]
    )
    assert len(merged) == 1
    assert merged[0].frequency == 30


def test_stale_merges_are_ignored():
    """重新预扫描后旧名字可能消失，失效引用不能复活幽灵条目。"""
    entries = [_entry("甲", 5), _entry("乙", 6)]
    merged, absorbed = merge_entries(
        entries, [{"source": "已删除的名字", "target": "甲"}]
    )
    assert {e.name for e in merged} == {"甲", "乙"}
    assert absorbed == {}


def test_no_merges_returns_original_entries():
    entries = [_entry("甲", 5), _entry("乙", 6)]
    merged, absorbed = merge_entries(entries, [])
    assert merged == entries
    assert absorbed == {}


def test_naming_source_priority_survives_merge():
    entries = [
        _entry("二愣子", 3, source="naming"),
        _entry("愣子", 40, source="freq"),
    ]
    merged, _ = merge_entries(
        entries, [{"source": "愣子", "target": "二愣子"}]
    )
    assert merged[0].source == "naming"  # 命名来源优先注入的语义不能丢


def test_unknown_type_falls_back_to_majority():
    entries = [
        _entry("某地", 5, etype="unknown"),
        _entry("某地别名", 4, etype="location"),
    ]
    merged, _ = merge_entries(
        entries, [{"source": "某地别名", "target": "某地"}]
    )
    assert merged[0].entity_type == "location"


# ---------------------------------------------------------------------------
# 服务层（memory_db）
# ---------------------------------------------------------------------------

async def _seed_dict(conn, novel_id, entries):
    await conn.execute(
        "INSERT OR REPLACE INTO novels (id, title, file_hash, total_chapters, total_words)"
        " VALUES (?, ?, ?, ?, ?)",
        (novel_id, "测试书", "hash-" + novel_id, 1, 100),
    )
    for e in entries:
        await conn.execute(
            "INSERT OR REPLACE INTO entity_dictionary"
            " (novel_id, name, entity_type, frequency, confidence, aliases, source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (novel_id, e.name, e.entity_type, e.frequency, e.confidence,
             __import__("json").dumps(e.aliases, ensure_ascii=False), e.source),
        )
    await conn.commit()


@pytest.mark.asyncio
async def test_service_merge_then_undo_restores_auto_view(memory_db, monkeypatch):
    from src.db import entity_dictionary_store, entity_override_store

    async def _factory():
        class _NC:
            def __init__(self, c): self._c = c
            def __getattr__(self, n): return getattr(self._c, n)
            async def close(self): pass
        return _NC(memory_db)

    monkeypatch.setattr(entity_dictionary_store, "get_connection", _factory)
    monkeypatch.setattr(entity_override_store, "get_connection", _factory)

    await _seed_dict(memory_db, "n1", [
        _entry("罗切斯特", 30), _entry("罗彻斯特", 20), _entry("简爱", 50),
    ])

    auto, _, _ = await get_merged_dictionary("n1")
    assert len(auto) == 3

    await save_merge("n1", "罗彻斯特", "罗切斯特")
    merged, absorbed, merges = await get_merged_dictionary("n1")
    assert len(merged) == 2
    assert absorbed == {"罗切斯特": ["罗彻斯特"]}
    assert merges == [{"source": "罗彻斯特", "target": "罗切斯特"}]

    assert await list_merges("n1") == merges
    assert await delete_merge("n1", "罗彻斯特") is True
    restored, absorbed2, merges2 = await get_merged_dictionary("n1")
    assert len(restored) == 3
    assert absorbed2 == {} and merges2 == []
    # 撤销不存在的合并
    assert await delete_merge("n1", "不存在") is False


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_merge_list_and_undo(api_client):
    client, conn = api_client
    await _seed_dict(conn, "novel-1", [
        _entry("罗切斯特", 30), _entry("罗彻斯特", 20), _entry("简爱", 50),
    ])

    r = await client.get("/api/novels/novel-1/entity-dictionary")
    assert r.status_code == 200
    assert r.json()["total"] == 3
    assert r.json()["merges"] == []

    r = await client.post(
        "/api/novels/novel-1/entity-dictionary/merge",
        json={"source": "罗彻斯特", "target": "罗切斯特"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["canonical"] == "罗切斯特"
    assert body["merges"] == [{"source": "罗彻斯特", "target": "罗切斯特"}]

    r = await client.get("/api/novels/novel-1/entity-dictionary")
    data = {item["name"]: item for item in r.json()["data"]}
    assert set(data) == {"罗切斯特", "简爱"}
    assert data["罗切斯特"]["merged_from"] == ["罗彻斯特"]
    assert data["罗切斯特"]["frequency"] == 50

    r = await client.delete("/api/novels/novel-1/entity-dictionary/merge/罗彻斯特")
    assert r.status_code == 200
    r = await client.get("/api/novels/novel-1/entity-dictionary")
    assert r.json()["total"] == 3


@pytest.mark.asyncio
async def test_api_rejects_self_merge_unknown_and_cycle(api_client):
    client, conn = api_client
    await _seed_dict(conn, "novel-2", [_entry("甲", 5), _entry("乙", 6), _entry("丙", 7)])

    r = await client.post(
        "/api/novels/novel-2/entity-dictionary/merge",
        json={"source": "甲", "target": "甲"},
    )
    assert r.status_code == 400

    r = await client.post(
        "/api/novels/novel-2/entity-dictionary/merge",
        json={"source": "甲", "target": "不存在"},
    )
    assert r.status_code == 404

    # 甲→乙 合法
    r = await client.post(
        "/api/novels/novel-2/entity-dictionary/merge", json={"source": "甲", "target": "乙"},
    )
    assert r.status_code == 200

    # 乙→甲 会成环，必须拒绝
    r = await client.post(
        "/api/novels/novel-2/entity-dictionary/merge", json={"source": "乙", "target": "甲"},
    )
    assert r.status_code == 400
    assert "环" in r.json()["detail"]

    # 撤销不存在的合并
    r = await client.delete("/api/novels/novel-2/entity-dictionary/merge/丙")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 消费端：提示词注入使用合并后的字典（issue #18 的核心收益）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dictionary_section_uses_merged_view(memory_db, monkeypatch):
    from src.db import (
        entity_dictionary_store,
        entity_override_store,
        world_structure_store,
    )
    from src.extraction.context_summary_builder import ContextSummaryBuilder

    class _NC:
        def __init__(self, c): self._c = c
        def __getattr__(self, n): return getattr(self._c, n)
        async def close(self): pass

    async def _factory():
        return _NC(memory_db)

    monkeypatch.setattr(entity_dictionary_store, "get_connection", _factory)
    monkeypatch.setattr(entity_override_store, "get_connection", _factory)
    monkeypatch.setattr(world_structure_store, "get_connection", _factory)

    await _seed_dict(memory_db, "n2", [
        _entry("罗切斯特", 30), _entry("罗彻斯特", 20), _entry("简爱", 50),
    ])
    await save_merge("n2", "罗彻斯特", "罗切斯特")

    builder = ContextSummaryBuilder()
    section = await builder._build_dictionary_section("n2")

    # 高频条目行格式：`- 名字（类型，出现N次）`。被吸收的候选只能作为
    # 别名出现在同一行里，不能再占一行独立条目。
    assert "- 罗切斯特（" in section
    assert "- 罗彻斯特（" not in section
    assert "可能别名：罗彻斯特" in section
    assert "- 简爱（" in section
