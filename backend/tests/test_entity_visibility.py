"""实体级可见性 override(entity_hide / entity_retype)集成测试 — issue #66 Epic 1。

覆盖:
- FR-1.1 隐藏在四个消费端生效(聚合列表/图谱/地图/百科/阅读高亮),软删可撤销
- FR-1.2 改型覆盖 4 个目标类型,跨类型路由不崩,alias_map 不变
- NFR-2 fact_json 字节级不可变
- NFR-3 无 override(或撤销后)各端输出与基线逐字节一致
"""

import json

import pytest
import pytest_asyncio
from unittest.mock import patch

from src.db import entity_override_store
from src.services import alias_resolver as alias_resolver_mod
from src.services import entity_aggregator, visualization_service
from src.services import encyclopedia_service
from src.db import chapter_store

NOVEL = "test-visibility"


# ── Fixture ───────────────────────────────────────


def _fact_json() -> dict:
    """两章最小事实集:每类实体至少一个。"""
    return {
        1: {
            "characters": [
                {"name": "孙悟空", "new_aliases": ["猴王"], "locations_in_chapter": ["花果山"]},
                {"name": "白骨精", "locations_in_chapter": ["白骨洞"]},
            ],
            "locations": [
                {"name": "花果山", "type": "山", "description": "仙山"},
                {"name": "白骨洞", "type": "洞穴"},
            ],
            "item_events": [
                {"item_name": "金箍棒", "item_type": "兵器", "action": "获得", "actor": "孙悟空"},
            ],
            "org_events": [
                {"org_name": "天庭", "org_type": "朝廷", "member": "孙悟空", "action": "任职"},
            ],
            "events": [
                {"summary": "孙悟空大战白骨精", "type": "战斗", "importance": "high",
                 "participants": ["孙悟空", "白骨精"], "location": "白骨洞"},
            ],
            "relationships": [
                {"person_a": "孙悟空", "person_b": "白骨精",
                 "relation_type": "敌对", "is_new": True},
            ],
            "new_concepts": [
                {"name": "筋斗云", "category": "功法", "definition": "腾云之法"},
            ],
        },
        2: {
            "characters": [{"name": "孙悟空", "locations_in_chapter": ["花果山"]}],
            "locations": [{"name": "花果山", "type": "山"}],
            "events": [],
        },
    }


async def _seed(db) -> None:
    await db.execute("INSERT INTO novels (id, title) VALUES (?, ?)", (NOVEL, "测试"))
    for ch, fact in _fact_json().items():
        cur = await db.execute(
            "INSERT INTO chapters (novel_id, chapter_num, title, content) "
            "VALUES (?, ?, ?, ?)",
            (NOVEL, ch, f"第{ch}回", "正文……"),
        )
        await db.execute(
            "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json) "
            "VALUES (?, ?, ?)",
            (NOVEL, cur.lastrowid, json.dumps(fact, ensure_ascii=False)),
        )
    await db.commit()


@pytest_asyncio.fixture
async def vis_db(memory_db):
    """In-memory DB wired into every consumer of the visibility layer."""

    class _NonClosing:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        async def close(self):
            pass

    async def factory():
        return _NonClosing(memory_db)

    async def _no_ungrounded(_novel_id, _names, _alias_map):
        return set()

    async def _empty_layout(_novel_id, _hash, locations, *_a, **_kw):
        # 轻量布局桩:只验证 locations 过滤,不跑约束求解器
        return ([{"name": l["name"], "x": 0.0, "y": 0.0} for l in locations],
                "hierarchy", None, None)

    await _seed(memory_db)
    with (
        patch("src.services.entity_aggregator.get_connection", factory),
        patch("src.services.alias_resolver.get_connection", factory),
        patch("src.db.entity_override_store.get_connection", factory),
        patch("src.db.chapter_fact_store.get_connection", factory),
        patch("src.db.chapter_store.get_connection", factory),
        patch("src.db.world_structure_store.get_connection", factory),
        patch("src.services.visualization_service.get_connection", factory),
        patch(
            "src.services.visualization_service._compute_or_load_layout",
            _empty_layout,
        ),
        patch(
            "src.services.hallucination_filter.get_ungrounded_persons",
            _no_ungrounded,
        ),
    ):
        yield memory_db
    alias_resolver_mod.invalidate_alias_cache(NOVEL)
    entity_aggregator.invalidate_cache(NOVEL)
    visualization_service.invalidate_map_response_cache(NOVEL)


async def _reset_overrides(db) -> None:
    """每个用例从干净状态开始 + 全部缓存失效。"""
    await entity_override_store.delete_all_overrides(NOVEL)
    entity_aggregator.invalidate_cache(NOVEL)
    visualization_service.invalidate_map_response_cache(NOVEL)


async def _hide(name: str) -> None:
    await entity_override_store.save_override(
        NOVEL, "entity_hide", name, {"auto_snapshot": {"type": "person"}},
    )
    entity_aggregator.invalidate_cache(NOVEL)
    visualization_service.invalidate_map_response_cache(NOVEL)


async def _retype(name: str, to: str, frm: str = "") -> None:
    await entity_override_store.save_override(
        NOVEL, "entity_retype", name,
        {"from": frm, "to": to, "auto_snapshot": {"type": frm}},
    )
    entity_aggregator.invalidate_cache(NOVEL)
    visualization_service.invalidate_map_response_cache(NOVEL)


# ── FR-1.1 实体隐藏 ────────────────────────────────


@pytest.mark.asyncio
async def test_hide_removes_entity_from_all_views(vis_db):
    """FR-1.1: 隐藏生效于 聚合列表/图谱/地图/百科/阅读高亮。"""
    await _reset_overrides(vis_db)
    await _hide("白骨精")

    entities = await entity_aggregator.get_all_entities(NOVEL)
    assert "白骨精" not in {e.name for e in entities}

    graph = await visualization_service.get_graph_data(NOVEL, 1, 2)
    node_names = {n["name"] for n in graph["nodes"]}
    assert "白骨精" not in node_names
    assert "孙悟空" in node_names
    assert all(
        "白骨精" not in (e["source"], e["target"]) for e in graph["edges"]
    )

    entries = await encyclopedia_service.get_encyclopedia_entries(NOVEL)
    assert "白骨精" not in {e["name"] for e in entries}

    highlight = await chapter_store.get_chapter_entities(NOVEL, 1)
    assert "白骨精" not in {e["name"] for e in highlight}

    # 数据仍在(软删):fact_json 原样
    cur = await vis_db.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id = ? ORDER BY chapter_id",
        (NOVEL,),
    )
    assert "白骨精" in (await cur.fetchone())[0]


@pytest.mark.asyncio
async def test_hide_location_removes_map_node(vis_db):
    await _reset_overrides(vis_db)
    base = await visualization_service.get_map_data(NOVEL, 1, 2)
    assert "白骨洞" in {l["name"] for l in base["locations"]}

    await _hide("白骨洞")
    m = await visualization_service.get_map_data(NOVEL, 1, 2)
    names = {l["name"] for l in m["locations"]}
    assert "白骨洞" not in names
    assert "花果山" in names
    assert all(
        it.get("name") != "白骨洞" for it in m["layout"]
    )


@pytest.mark.asyncio
async def test_hide_undo_restores_byte_identical(vis_db):
    """NFR-3/NFR-5: 删 override 即恢复,各端输出与基线逐字节一致。"""
    await _reset_overrides(vis_db)
    baseline_entities = [
        e.model_dump() for e in await entity_aggregator.get_all_entities(NOVEL)
    ]
    baseline_graph = await visualization_service.get_graph_data(NOVEL, 1, 2)
    baseline_entries = await encyclopedia_service.get_encyclopedia_entries(NOVEL)
    baseline_highlight = await chapter_store.get_chapter_entities(NOVEL, 1)

    await _hide("白骨精")
    # 撤销 = 删除 override 记录
    rows = await entity_override_store.load_overrides(NOVEL)
    assert len(rows) == 1
    await entity_override_store.delete_override(NOVEL, rows[0]["id"])
    entity_aggregator.invalidate_cache(NOVEL)
    visualization_service.invalidate_map_response_cache(NOVEL)

    assert [
        e.model_dump() for e in await entity_aggregator.get_all_entities(NOVEL)
    ] == baseline_entities
    assert await visualization_service.get_graph_data(NOVEL, 1, 2) == baseline_graph
    assert await encyclopedia_service.get_encyclopedia_entries(NOVEL) == baseline_entries
    assert await chapter_store.get_chapter_entities(NOVEL, 1) == baseline_highlight


# ── FR-1.2 实体类型修改(4 个目标类型各一例)─────────


@pytest.mark.asyncio
async def test_retype_location_to_person(vis_db):
    """跨类型路由:location→person 后出现在人物聚合与卡片,不崩。"""
    await _reset_overrides(vis_db)
    await _retype("白骨洞", "person", "location")

    entities = await entity_aggregator.get_all_entities(NOVEL)
    by_name = {e.name: e.type for e in entities}
    assert by_name["白骨洞"] == "person"

    # 人物聚合器对该名字返回合法 profile(无人物事实,内容稀疏但不崩)
    profile = await entity_aggregator.aggregate_person(NOVEL, "白骨洞")
    assert profile.name == "白骨洞"

    # 百科条目换类型;阅读高亮换色(type 字段)
    entries = await encyclopedia_service.get_encyclopedia_entries(NOVEL)
    assert {e["name"]: e["type"] for e in entries}["白骨洞"] == "person"
    highlight = await chapter_store.get_chapter_entities(NOVEL, 1)
    assert {e["name"]: e["type"] for e in highlight}["白骨洞"] == "person"

    # 地图上不再是地点节点
    m = await visualization_service.get_map_data(NOVEL, 1, 2)
    assert "白骨洞" not in {l["name"] for l in m["locations"]}


@pytest.mark.asyncio
async def test_retype_person_to_item(vis_db):
    await _reset_overrides(vis_db)
    await _retype("白骨精", "item", "person")

    entities = await entity_aggregator.get_all_entities(NOVEL)
    assert {e.name: e.type for e in entities}["白骨精"] == "item"

    # 不再是 person → 图谱节点与相关边消失
    graph = await visualization_service.get_graph_data(NOVEL, 1, 2)
    assert "白骨精" not in {n["name"] for n in graph["nodes"]}
    assert all(
        "白骨精" not in (e["source"], e["target"]) for e in graph["edges"]
    )

    profile = await entity_aggregator.aggregate_item(NOVEL, "白骨精")
    assert profile.name == "白骨精"


@pytest.mark.asyncio
async def test_retype_item_to_org(vis_db):
    await _reset_overrides(vis_db)
    await _retype("金箍棒", "org", "item")

    entities = await entity_aggregator.get_all_entities(NOVEL)
    assert {e.name: e.type for e in entities}["金箍棒"] == "org"

    profile = await entity_aggregator.aggregate_org(NOVEL, "金箍棒")
    assert profile.name == "金箍棒"

    entries = await encyclopedia_service.get_encyclopedia_entries(NOVEL)
    assert {e["name"]: e["type"] for e in entries}["金箍棒"] == "org"


@pytest.mark.asyncio
async def test_retype_org_to_concept(vis_db):
    await _reset_overrides(vis_db)
    await _retype("天庭", "concept", "org")

    entities = await entity_aggregator.get_all_entities(NOVEL)
    assert {e.name: e.type for e in entities}["天庭"] == "concept"

    entries = await encyclopedia_service.get_encyclopedia_entries(NOVEL)
    entry = {e["name"]: e for e in entries}["天庭"]
    assert entry["type"] == "concept"
    assert entry["category"] == "其他"  # 改型入概念无子类,落默认分类

    highlight = await chapter_store.get_chapter_entities(NOVEL, 1)
    assert {e["name"]: e["type"] for e in highlight}["天庭"] == "concept"


@pytest.mark.asyncio
async def test_retype_does_not_touch_alias_map(vis_db):
    """FR-1.2: 改型不触发别名重算,alias_map 逐字节不变。"""
    await _reset_overrides(vis_db)
    before = await alias_resolver_mod.build_alias_map(NOVEL)
    await _retype("白骨精", "item", "person")
    after = await alias_resolver_mod.build_alias_map(NOVEL)
    assert before == after


@pytest.mark.asyncio
async def test_hide_and_retype_stack(vis_db):
    """改型 + 隐藏可叠加:隐藏优先,撤销隐藏后按改型呈现。"""
    await _reset_overrides(vis_db)
    await _retype("白骨精", "item", "person")
    await _hide("白骨精")

    entities = await entity_aggregator.get_all_entities(NOVEL)
    assert "白骨精" not in {e.name for e in entities}

    # 撤销隐藏,改型仍在
    rows = await entity_override_store.load_overrides(NOVEL)
    hide_id = next(r["id"] for r in rows if r["override_type"] == "entity_hide")
    await entity_override_store.delete_override(NOVEL, hide_id)
    entity_aggregator.invalidate_cache(NOVEL)

    entities = await entity_aggregator.get_all_entities(NOVEL)
    assert {e.name: e.type for e in entities}["白骨精"] == "item"


# ── NFR-2 原文不可变 ──────────────────────────────


@pytest.mark.asyncio
async def test_fact_json_byte_immutable(vis_db):
    """NFR-2: 全部修正操作执行后 fact_json 哈希不变。"""
    await _reset_overrides(vis_db)
    cur = await vis_db.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id = ? ORDER BY chapter_id",
        (NOVEL,),
    )
    before = [row[0] for row in await cur.fetchall()]

    await _retype("白骨精", "item", "person")
    await _hide("金箍棒")

    cur = await vis_db.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id = ? ORDER BY chapter_id",
        (NOVEL,),
    )
    after = [row[0] for row in await cur.fetchall()]
    assert before == after
