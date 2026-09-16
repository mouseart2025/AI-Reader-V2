"""地理布局缓存自失效死循环修复(geo_failed 一次性失败标记)测试。

场景: geo_type='fantasy' + novel_genre_hint='wuxia'(凡人修仙传类虚构世界)。
stale-cache 判定把 effective geo_type 升级为 'mixed',而 geo 解析永远失败,
修复前每个冷进程都把 layer cache 判 stale → 全量重算(求解器 ~20s)。
修复后: 第一次失败置 geo_failed,之后 layer cache 正常命中;
delete_layer_layouts(重分析/层级重建)清除标记,允许重试 geo。
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from src.db import world_structure_store
from src.models.world_structure import LayerType, MapLayer, WorldStructure
from src.services import visualization_service

NOVEL = "test-geo-failed"


async def _seed_novel(db) -> None:
    await db.execute("INSERT INTO novels (id, title) VALUES (?, ?)", (NOVEL, "测试"))
    locations = [
        {"name": "青牛镇", "type": "镇"},
        {"name": "七玄门", "type": "门派"},
        {"name": "野狼帮", "type": "帮派"},
    ]
    for ch in (1, 2, 3):
        cur = await db.execute(
            "INSERT INTO chapters (novel_id, chapter_num, title, content) "
            "VALUES (?, ?, ?, ?)",
            (NOVEL, ch, f"第{ch}章", "正文……"),
        )
        fact = {
            "characters": [{"name": "韩立", "locations_in_chapter": ["青牛镇"]}],
            "locations": locations,
            "events": [],
        }
        await db.execute(
            "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json) "
            "VALUES (?, ?, ?)",
            (NOVEL, cur.lastrowid, json.dumps(fact, ensure_ascii=False)),
        )
    await db.commit()


async def _seed_ws() -> None:
    """写 WorldStructure(需走 world_structure_store,在 patch 上下文内调用)。"""
    ws = WorldStructure(
        novel_id=NOVEL,
        layers=[
            MapLayer(layer_id="overworld", name="主世界", layer_type=LayerType.overworld),
            MapLayer(layer_id="cave", name="洞府", layer_type=LayerType.pocket),
        ],
        novel_genre_hint="wuxia",
        geo_type="fantasy",
    )
    await world_structure_store.save(NOVEL, ws)


@pytest_asyncio.fixture
async def geo_failed_db(memory_db):
    """In-memory DB + geo 解析永远失败(虚构世界)的 get_map_data 环境。"""

    class _NonClosing:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        async def close(self):
            pass

    async def factory():
        return _NonClosing(memory_db)

    layered_calls: list[int] = []

    def _fake_layered_layout(ws_dict, locations, *_a, **_kw):
        layered_calls.append(1)
        items = [
            {"name": loc["name"], "x": 100.0 + i * 200.0, "y": 300.0}
            for i, loc in enumerate(locations)
        ]
        return {"overworld": items, "cave": []}

    async def _fake_geo_resolve(*_a, **_kw):
        # 虚构世界: genre override 升级 geo_type='mixed' 但无任何真实坐标可解析
        return (None, "mixed", None, {})

    def _no_terrain(*_a, **_kw):
        return None

    await _seed_novel(memory_db)
    visualization_service._map_cache.clear()
    with (
        patch("src.services.alias_resolver.get_connection", factory),
        patch("src.db.entity_override_store.get_connection", factory),
        patch("src.db.chapter_fact_store.get_connection", factory),
        patch("src.db.chapter_store.get_connection", factory),
        patch("src.db.world_structure_store.get_connection", factory),
        patch("src.services.visualization_service.get_connection", factory),
        patch(
            "src.services.visualization_service.geo_auto_resolve",
            AsyncMock(side_effect=_fake_geo_resolve),
        ),
        patch(
            "src.services.visualization_service.compute_layered_layout",
            _fake_layered_layout,
        ),
        patch("src.services.visualization_service.generate_terrain", _no_terrain),
        patch(
            "src.services.visualization_service.generate_landmasses",
            lambda *_a, **_kw: {"landmasses": [], "shelves": []},
        ),
        patch("src.services.visualization_service.generate_rivers", lambda *_a, **_kw: []),
        patch("src.services.visualization_service.generate_roads", lambda *_a, **_kw: []),
    ):
        await _seed_ws()
        yield memory_db, layered_calls
        visualization_service._map_cache.clear()


@pytest.mark.asyncio
async def test_geo_failed_marker_breaks_stale_invalidation_loop(geo_failed_db):
    """第一次请求 geo 失败置标记;第二次 layer cache 命中,不再重算。"""
    _db, layered_calls = geo_failed_db

    m1 = await visualization_service.get_map_data(NOVEL, 1, 3)
    assert m1["layout_mode"] == "layered"
    assert len(layered_calls) == 1
    assert await world_structure_store.get_geo_failed(NOVEL)

    # 冷进程语义: 内存缓存清空,layer_layouts / map_layout_meta 仍在
    visualization_service._map_cache.clear()
    m2 = await visualization_service.get_map_data(NOVEL, 1, 3)
    assert m2["layout_mode"] == "layered"
    assert len(layered_calls) == 1  # 求解器未被再次调用


@pytest.mark.asyncio
async def test_delete_layer_layouts_clears_geo_failed(geo_failed_db):
    """重分析/层级重建清缓存后,geo_failed 清除,允许重试 geo。"""
    _db, layered_calls = geo_failed_db

    await visualization_service.get_map_data(NOVEL, 1, 3)
    assert await world_structure_store.get_geo_failed(NOVEL)

    await world_structure_store.delete_layer_layouts(NOVEL)
    assert not await world_structure_store.get_geo_failed(NOVEL)

    # geo 被重试(再次失败 → 标记重新置位,缓存重建)
    visualization_service._map_cache.clear()
    await visualization_service.get_map_data(NOVEL, 1, 3)
    assert len(layered_calls) == 2
    assert await world_structure_store.get_geo_failed(NOVEL)
