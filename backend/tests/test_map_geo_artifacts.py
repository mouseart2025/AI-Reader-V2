"""map_geo_artifacts 持久化 + 失效连带 + _point_in_polygon 向量化等价性测试。

覆盖:
- 冷路径写 / 热路径读往返: 第二次调用(清内存缓存)不再触发 generate_landmasses
- invalidate_map_response_cache 连带删除 DB artifacts 行
- _points_in_polygon(向量化)与 _point_in_polygon_scalar(原实现)逐点一致
"""

import json
from unittest.mock import patch

import numpy as np
import pytest
import pytest_asyncio

from src.services import visualization_service
from src.services.map_layout_service import (
    _point_in_polygon_scalar,
    _points_in_polygon,
)

NOVEL = "test-geo-artifacts"

_FAKE_LANDMASSES = [{
    "id": "landmass_0",
    "coastline": [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]],
    "holes": [],
    "area": 10000.0,
    "location_count": 3,
    "is_main": True,
}]
_FAKE_SHELVES = [[[0.0, 0.0], [120.0, 0.0], [120.0, 120.0]]]
_FAKE_RIVERS = [{"id": "river_0", "path": [[1.0, 1.0], [2.0, 2.0]]}]
_FAKE_ROADS = [{"from": "花果山", "to": "傲来国", "path": [[1.0, 1.0], [2.0, 2.0]]}]


def _fake_landmasses(*_a, **_kw):
    # 无 _land_mask → sea-orphan snap 段跳过
    return {"landmasses": _FAKE_LANDMASSES, "shelves": _FAKE_SHELVES}


def _fake_rivers(*_a, **_kw):
    return _FAKE_RIVERS


def _fake_roads(*_a, **_kw):
    return _FAKE_ROADS


def _boom(*_a, **_kw):
    raise RuntimeError("generation must not run on the artifacts read path")


async def _seed(db) -> None:
    await db.execute("INSERT INTO novels (id, title) VALUES (?, ?)", (NOVEL, "测试"))
    locations = [
        {"name": "花果山", "type": "山"},
        {"name": "傲来国", "type": "国家"},
        {"name": "水帘洞", "type": "洞穴", "parent": "花果山"},
    ]
    for ch in (1, 2, 3):
        cur = await db.execute(
            "INSERT INTO chapters (novel_id, chapter_num, title, content) "
            "VALUES (?, ?, ?, ?)",
            (NOVEL, ch, f"第{ch}回", "正文……"),
        )
        fact = {
            "characters": [{"name": "孙悟空", "locations_in_chapter": ["花果山"]}],
            "locations": locations,
            "events": [],
        }
        await db.execute(
            "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json) "
            "VALUES (?, ?, ?)",
            (NOVEL, cur.lastrowid, json.dumps(fact, ensure_ascii=False)),
        )
    await db.commit()


@pytest_asyncio.fixture
async def geo_db(memory_db):
    """In-memory DB wired into get_map_data + geo artifacts store."""

    class _NonClosing:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        async def close(self):
            pass

    async def factory():
        return _NonClosing(memory_db)

    async def _empty_layout(_novel_id, _hash, locations, *_a, **_kw):
        # 轻量布局桩:不跑约束求解器
        return (
            [
                {"name": loc["name"], "x": 100.0 + i * 200.0, "y": 300.0}
                for i, loc in enumerate(locations)
            ],
            "hierarchy",
            None,
            None,
        )

    await _seed(memory_db)
    visualization_service._map_cache.clear()
    with (
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
            "src.services.visualization_service.generate_landmasses",
            _fake_landmasses,
        ),
        patch("src.services.visualization_service.generate_rivers", _fake_rivers),
        patch("src.services.visualization_service.generate_roads", _fake_roads),
    ):
        yield memory_db
        visualization_service._map_cache.clear()


@pytest.mark.asyncio
async def test_artifacts_write_read_roundtrip(geo_db):
    """冷路径落库后,清内存缓存的第二次调用直接读库,不再重算。"""
    m1 = await visualization_service.get_map_data(NOVEL, 1, 3)
    assert m1["landmasses"] == _FAKE_LANDMASSES
    assert m1["shelves"] == _FAKE_SHELVES
    assert m1["rivers"] == _FAKE_RIVERS
    assert m1["roads"] == _FAKE_ROADS

    cur = await geo_db.execute(
        "SELECT layer_id, landmasses_json, shelves_json, rivers_json, roads_json "
        "FROM map_geo_artifacts WHERE novel_id = ?",
        (NOVEL,),
    )
    rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["layer_id"] == "overworld"
    assert json.loads(rows[0]["landmasses_json"]) == _FAKE_LANDMASSES

    # 新进程语义: 内存缓存清空,生成函数一旦被调用即抛异常
    visualization_service._map_cache.clear()
    with (
        patch("src.services.visualization_service.generate_landmasses", _boom),
        patch("src.services.visualization_service.generate_rivers", _boom),
        patch("src.services.visualization_service.generate_roads", _boom),
    ):
        m2 = await visualization_service.get_map_data(NOVEL, 1, 3)

    assert m2["landmasses"] == _FAKE_LANDMASSES
    assert m2["shelves"] == _FAKE_SHELVES
    assert m2["rivers"] == _FAKE_RIVERS
    assert m2["roads"] == _FAKE_ROADS


@pytest.mark.asyncio
async def test_invalidate_map_response_cache_deletes_artifacts(geo_db):
    """实体 override 失效连带: invalidate_map_response_cache 删除 DB artifacts 行。"""
    await visualization_service.get_map_data(NOVEL, 1, 3)
    cur = await geo_db.execute(
        "SELECT COUNT(*) FROM map_geo_artifacts WHERE novel_id = ?", (NOVEL,),
    )
    assert (await cur.fetchone())[0] == 1

    await visualization_service.invalidate_map_response_cache(NOVEL)

    cur = await geo_db.execute(
        "SELECT COUNT(*) FROM map_geo_artifacts WHERE novel_id = ?", (NOVEL,),
    )
    assert (await cur.fetchone())[0] == 0
    assert not any(k.startswith(f"{NOVEL}:") for k in visualization_service._map_cache)


# ── _point_in_polygon 向量化等价性 ──────────────────

_POLYGONS = [
    # 凸四边形
    [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)],
    # 三角形
    [(10.0, 10.0), (90.0, 20.0), (50.0, 95.0)],
    # L 形凹多边形
    [(0.0, 0.0), (100.0, 0.0), (100.0, 40.0), (40.0, 40.0), (40.0, 100.0), (0.0, 100.0)],
    # 五角星(深度凹陷)
    [
        (50.0, 0.0), (61.0, 35.0), (98.0, 35.0), (68.0, 57.0), (79.0, 91.0),
        (50.0, 70.0), (21.0, 91.0), (32.0, 57.0), (2.0, 35.0), (39.0, 35.0),
    ],
    # 大坐标 + 负坐标混合
    [(-500.0, -300.0), (800.0, -300.0), (800.0, 600.0), (-500.0, 600.0)],
]

_TEST_POINTS = (
    # 规则网格(覆盖内外与边界附近)
    [(x * 7.3, y * 6.1) for x in range(-2, 16) for y in range(-2, 18)]
    # 顶点/边/边界点
    + [
        (0.0, 0.0), (100.0, 100.0), (50.0, 0.0), (0.0, 50.0),
        (40.0, 40.0), (50.0, 35.0), (68.0, 57.0), (1e-9, 50.0),
        (-1e-9, 50.0), (100.0, 50.0), (99.9999999, 50.0),
        (-500.0, -300.0), (150.0, 150.0), (400.0, 0.0),
    ]
)


def test_points_in_polygon_matches_scalar():
    pts = np.array(_TEST_POINTS, dtype=np.float64)
    for poly in _POLYGONS:
        expected = [_point_in_polygon_scalar(x, y, poly) for x, y in _TEST_POINTS]
        actual = _points_in_polygon(pts, poly)
        assert actual.dtype == bool
        assert actual.tolist() == expected, f"mismatch for polygon {poly}"


def test_points_in_polygon_edge_cases():
    # 空点集
    assert _points_in_polygon(np.zeros((0, 2)), _POLYGONS[0]).tolist() == []
    # 退化成 <3 顶点的多边形 → 全 False(scalar 对 len<3 也恒为 False)
    seg = [(0.0, 0.0), (100.0, 100.0)]
    assert _points_in_polygon([[50.0, 50.0]], seg).tolist() == [False]
    assert _point_in_polygon_scalar(50.0, 50.0, seg) is False
