"""geographic 模式 geo_coords 持久化(geo_coords_json)测试。

修复前: geographic 缓存命中后仍需每次冷进程重跑 geo_auto_resolve
(重建 geonames 索引 + 全书地名解析,实测三国 ~4.2s),因为 geo_coords
不在任何缓存里。修复后: geo_coords 随 map_geo_artifacts 持久化,
命中直接读库,未命中走原恢复逻辑并回填。
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from src.db import world_structure_store
from src.models.world_structure import LayerType, MapLayer, WorldStructure
from src.services import visualization_service
from src.services.map_layout_service import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    compute_chapter_hash,
)

NOVEL = "test-geo-coords"
CH_HASH = compute_chapter_hash(1, 3, CANVAS_WIDTH, CANVAS_HEIGHT)

_PERSISTED_COORDS = {
    "洛阳": {"lat": 34.62, "lng": 112.45},
    "长安": {"lat": 34.34, "lng": 108.94},
}


async def _seed_novel(db) -> None:
    await db.execute("INSERT INTO novels (id, title) VALUES (?, ?)", (NOVEL, "测试"))
    locations = [
        {"name": "洛阳", "type": "城池"},
        {"name": "长安", "type": "城池"},
    ]
    for ch in (1, 2, 3):
        cur = await db.execute(
            "INSERT INTO chapters (novel_id, chapter_num, title, content) "
            "VALUES (?, ?, ?, ?)",
            (NOVEL, ch, f"第{ch}回", "正文……"),
        )
        fact = {
            "characters": [{"name": "刘备", "locations_in_chapter": ["洛阳"]}],
            "locations": locations,
            "events": [],
        }
        await db.execute(
            "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json) "
            "VALUES (?, ?, ?)",
            (NOVEL, cur.lastrowid, json.dumps(fact, ensure_ascii=False)),
        )
    await db.commit()


async def _seed_ws_and_layer_cache() -> None:
    """WorldStructure(realistic) + geographic layer cache(须在 patch 上下文内)。"""
    ws = WorldStructure(
        novel_id=NOVEL,
        layers=[
            MapLayer(layer_id="overworld", name="主世界", layer_type=LayerType.overworld),
        ],
        novel_genre_hint="historical",
        geo_type="realistic",
    )
    await world_structure_store.save(NOVEL, ws)
    layout = [
        {"name": "洛阳", "x": 500.0, "y": 400.0},
        {"name": "长安", "x": 300.0, "y": 420.0},
    ]
    await world_structure_store.save_layer_layout(
        NOVEL, "overworld", CH_HASH,
        json.dumps(layout, ensure_ascii=False), "geographic",
    )


@pytest_asyncio.fixture
async def coords_db(memory_db):
    """In-memory DB + geographic layer cache;geo_auto_resolve 由用例自行 patch。"""

    class _NonClosing:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        async def close(self):
            pass

    async def factory():
        return _NonClosing(memory_db)

    await _seed_novel(memory_db)
    visualization_service._map_cache.clear()
    with (
        patch("src.services.alias_resolver.get_connection", factory),
        patch("src.db.entity_override_store.get_connection", factory),
        patch("src.db.chapter_fact_store.get_connection", factory),
        patch("src.db.chapter_store.get_connection", factory),
        patch("src.db.world_structure_store.get_connection", factory),
        patch("src.services.visualization_service.get_connection", factory),
    ):
        await _seed_ws_and_layer_cache()
        yield memory_db
        visualization_service._map_cache.clear()


@pytest.mark.asyncio
async def test_cached_geographic_reads_persisted_geo_coords(coords_db):
    """artifacts 有 geo_coords: 跳过 geo_auto_resolve,响应直接含持久化坐标。"""
    await world_structure_store.save_geo_coords(
        NOVEL, "overworld", CH_HASH,
        json.dumps(_PERSISTED_COORDS, ensure_ascii=False),
    )

    async def _boom(*_a, **_kw):
        raise AssertionError("geo_auto_resolve must not run when geo_coords persisted")

    with patch(
        "src.services.visualization_service.geo_auto_resolve",
        AsyncMock(side_effect=_boom),
    ):
        m = await visualization_service.get_map_data(NOVEL, 1, 3)

    assert m["layout_mode"] == "geographic"
    assert m["geo_coords"] == _PERSISTED_COORDS


@pytest.mark.asyncio
async def test_missing_geo_coords_restores_and_backfills(coords_db):
    """artifacts 无 geo_coords: 走 geo_auto_resolve 恢复,并回填 artifacts。"""
    async def _fake_resolve(*_a, **_kw):
        return ("china", "realistic", object(), {"洛阳": (34.62, 112.45)})

    def _fake_estimate(names, resolved, _parent_map):
        return {n: (34.34, 108.94) for n in names}

    with (
        patch(
            "src.services.visualization_service.geo_auto_resolve",
            AsyncMock(side_effect=_fake_resolve),
        ),
        patch(
            "src.services.visualization_service.place_unresolved_geo_coords",
            _fake_estimate,
        ),
    ):
        m = await visualization_service.get_map_data(NOVEL, 1, 3)

    assert m["layout_mode"] == "geographic"
    assert m["geo_coords"] == _PERSISTED_COORDS  # 解析值 + unresolved 估计合并

    # 回填: 下一次冷进程应直接命中 artifacts
    art = await world_structure_store.load_geo_artifacts(NOVEL, "overworld", CH_HASH)
    assert art is not None
    assert art["geo_coords"] == _PERSISTED_COORDS

    visualization_service._map_cache.clear()

    async def _boom(*_a, **_kw):
        raise AssertionError("backfilled geo_coords must skip geo_auto_resolve")

    with patch(
        "src.services.visualization_service.geo_auto_resolve",
        AsyncMock(side_effect=_boom),
    ):
        m2 = await visualization_service.get_map_data(NOVEL, 1, 3)
    assert m2["geo_coords"] == _PERSISTED_COORDS
