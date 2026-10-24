"""现实题材的「海底/龙宫」层不应强制分层幻想地图。

《海底两万里》的 underwater 层有 11 个地点(11/411 = 2.7%),旧判定按
「realm 层 >=5 且 >=1%」把整本书踢出 GeoNames 地理布局,画成一张没有
地理意义的幻想地图:南极在顶部、北冰洋在亚洲下方、太平洋挤在欧洲旁边。

修复后:
- realistic/adventure 世界里 underwater/sea 层视为同一颗地球的海底,
  不再触发 fantasy 布局;sky/underground/spirit 仍然触发。
- 旧数据留下的 geo_failed=1 + layered 缓存,在判定不再偏好幻想层时
  放行一次地理布局重试,成功后清掉失败标记。
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

NOVEL = "test-sea-realm-geo"
CH_HASH = compute_chapter_hash(1, 3, CANVAS_WIDTH, CANVAS_HEIGHT)

SEA_NAMES = ["海底", "海底森林", "海底洞穴", "细沙平原", "海底岩石", "海草华盖"]
LAND_NAMES = [
    "巴黎", "伦敦", "纽约", "太平洋", "大西洋", "地中海",
    "红海", "印度洋", "悉尼", "东京", "上海", "开普敦",
]
# 没有地理坐标的对象：船内舱室 / 抽象世界 / 经纬线标记。
NON_GEO_LOCATIONS = [
    ("客厅", "船舱"),
    ("中央楼梯", "船构件"),
    ("世界", "界域"),
    ("赤道线", "地理标记"),
]


def _world(genre: str, geo_type: str, sky: bool = False) -> WorldStructure:
    layers = [
        MapLayer(layer_id="overworld", name="主世界", layer_type=LayerType.overworld),
        MapLayer(layer_id="underwater", name="海底/龙宫", layer_type=LayerType.underwater),
    ]
    if sky:
        layers.append(
            MapLayer(layer_id="celestial", name="天界", layer_type=LayerType.sky)
        )
    return WorldStructure(
        novel_id=NOVEL,
        layers=layers,
        location_layer_map={
            **{name: "overworld" for name in LAND_NAMES},
            **{name: "underwater" for name in SEA_NAMES},
        },
        novel_genre_hint=genre,
        geo_type=geo_type,
    )


def test_realistic_sea_realm_does_not_force_fantasy():
    """现实/冒险题材:海底层的 6/18 地点不再触发分层幻想地图。"""
    ws = _world("realistic", "realistic")
    assert visualization_service._realm_layer_location_count(ws) == (6, 18)
    assert visualization_service._prefers_fantasy_layers(ws) is False


def test_historical_sea_realm_still_prefers_layers():
    """历史/混合(封神演义类)的 underwater 龙宫仍是独立幻想空间。"""
    ws = _world("historical", "mixed")
    assert visualization_service._prefers_fantasy_layers(ws) is True


def test_realistic_sky_realm_still_prefers_layers():
    """只有海底/海域被豁免;天界仍需要分层地图。"""
    ws = _world("realistic", "realistic", sky=True)
    ws.location_layer_map.update({name: "celestial" for name in SEA_NAMES})
    assert visualization_service._prefers_fantasy_layers(ws) is True


def test_fewer_than_five_sea_locations_never_flips():
    ws = _world("realistic", "realistic")
    for name in SEA_NAMES[4:]:
        ws.location_layer_map[name] = "overworld"
    assert visualization_service._prefers_fantasy_layers(ws) is False


def test_geo_layout_skips_non_place_locations():
    """船舱/构件/界域/经纬线不进入 Leaflet 底图;真实地点保留。"""
    keep = [
        {"name": "巴黎", "type": "城市"},
        {"name": "太平洋", "type": "海洋"},
    ]
    drop = [
        {"name": "诺第留斯号", "type": "潜水船"},
        {"name": "林肯号", "type": "船只"},
        {"name": "客厅", "type": "船舱"},
        {"name": "中央楼梯", "type": "船构件"},
        {"name": "诺第留斯号·走廊", "type": "通道"},
        {"name": "平台", "type": "甲板"},
        {"name": "世界", "type": "界域"},
        {"name": "地球", "type": "区域"},
        {"name": "赤道线", "type": "地理标记"},
        {"name": "西经50度", "type": "经线"},
    ]
    for loc in keep:
        assert visualization_service._is_geographic_map_location(loc) is True
    for loc in drop:
        assert visualization_service._is_geographic_map_location(loc) is False


async def _seed_novel(db) -> None:
    await db.execute("INSERT INTO novels (id, title) VALUES (?, ?)", (NOVEL, "海底测试"))
    locations = [
        {"name": name, "type": "海洋" if name in ("太平洋", "大西洋", "印度洋") else "城市"}
        for name in LAND_NAMES
    ] + [{"name": name, "type": "海底"} for name in SEA_NAMES]
    locations += [{"name": name, "type": typ} for name, typ in NON_GEO_LOCATIONS]
    for ch in (1, 2, 3):
        cur = await db.execute(
            "INSERT INTO chapters (novel_id, chapter_num, title, content) "
            "VALUES (?, ?, ?, ?)",
            (NOVEL, ch, f"第{ch}章", "正文……"),
        )
        fact = {
            "characters": [{"name": "阿龙纳斯", "locations_in_chapter": ["巴黎"]}],
            "locations": locations,
            "events": [],
        }
        await db.execute(
            "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json) "
            "VALUES (?, ?, ?)",
            (NOVEL, cur.lastrowid, json.dumps(fact, ensure_ascii=False)),
        )
    await db.commit()


async def _seed_ws_and_stuck_cache() -> None:
    """写入 realistic world + 旧版留下的 layered 缓存 / geo_failed 标记。"""
    await world_structure_store.save(NOVEL, _world("realistic", "realistic"))
    layout = [
        {"name": name, "x": 100.0 + i * 120.0, "y": 300.0}
        for i, name in enumerate(LAND_NAMES)
    ]
    await world_structure_store.save_layer_layout(
        NOVEL, "overworld", CH_HASH, json.dumps(layout, ensure_ascii=False), "layered",
    )
    await world_structure_store.set_geo_failed(NOVEL)


class _FakeResolver:
    def project_to_canvas(self, resolved, locations, canvas_w, canvas_h):
        return [
            {"name": name, "x": 200.0 + i * 60.0, "y": 400.0}
            for i, name in enumerate(resolved)
        ]


@pytest_asyncio.fixture
async def sea_db(memory_db):
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
    visualization_service._GEO_RETRY_DONE.discard(NOVEL)
    with (
        patch("src.services.alias_resolver.get_connection", factory),
        patch("src.db.entity_override_store.get_connection", factory),
        patch("src.db.chapter_fact_store.get_connection", factory),
        patch("src.db.chapter_store.get_connection", factory),
        patch("src.db.world_structure_store.get_connection", factory),
        patch("src.services.visualization_service.get_connection", factory),
        patch("src.services.visualization_service.generate_terrain", lambda *_a, **_kw: None),
        patch(
            "src.services.visualization_service.generate_landmasses",
            lambda *_a, **_kw: {"landmasses": [], "shelves": []},
        ),
        patch("src.services.visualization_service.generate_rivers", lambda *_a, **_kw: []),
        patch("src.services.visualization_service.generate_roads", lambda *_a, **_kw: []),
        patch(
            "src.services.visualization_service.place_unresolved_near_neighbors",
            lambda *_a, **_kw: [],
        ),
        patch(
            "src.services.visualization_service.place_unresolved_geo_coords",
            lambda *_a, **_kw: {},
        ),
    ):
        await _seed_ws_and_stuck_cache()
        yield memory_db
        visualization_service._map_cache.clear()
        visualization_service._GEO_RETRY_DONE.discard(NOVEL)


@pytest.mark.asyncio
async def test_stuck_layered_cache_retries_geo_once(sea_db):
    """旧 geo_failed + layered 缓存:判定修复后重试并切到 geographic。"""
    resolved = {name: (48.85, 2.35) for name in LAND_NAMES}
    captured: dict[str, list[str]] = {}

    async def _fake_resolve(_genre, all_names, _major, _parents, **_kw):
        captured["names"] = list(all_names)
        return ("world", "realistic", _FakeResolver(), resolved)

    with patch(
        "src.services.visualization_service.geo_auto_resolve",
        AsyncMock(side_effect=_fake_resolve),
    ):
        m = await visualization_service.get_map_data(NOVEL, 1, 3)

    assert m["layout_mode"] == "geographic"
    assert set(m["geo_coords"]) == set(LAND_NAMES)
    assert not await world_structure_store.get_geo_failed(NOVEL)
    # 舱室/界域/经纬线不进入地理解析,但仍保留在响应的 locations 里
    assert not (set(captured["names"]) & {name for name, _ in NON_GEO_LOCATIONS})
    response_names = {loc["name"] for loc in m["locations"]}
    assert {name for name, _ in NON_GEO_LOCATIONS} <= response_names


@pytest.mark.asyncio
async def test_retry_failure_does_not_loop(sea_db):
    """重试仍失败:只重算一次分层布局,后续请求命中缓存,不再反复重试。"""
    layered_calls: list[int] = []

    def _fake_layered(_ws_dict, locations, *_a, **_kw):
        layered_calls.append(1)
        items = [
            {"name": loc["name"], "x": 100.0 + i * 80.0, "y": 300.0}
            for i, loc in enumerate(locations)
        ]
        return {"overworld": items, "underwater": []}

    async def _no_geo(*_a, **_kw):
        return ("world", "realistic", None, {})

    with (
        patch(
            "src.services.visualization_service.geo_auto_resolve",
            AsyncMock(side_effect=_no_geo),
        ),
        patch(
            "src.services.visualization_service.compute_layered_layout",
            _fake_layered,
        ),
    ):
        m1 = await visualization_service.get_map_data(NOVEL, 1, 3)
        assert m1["layout_mode"] == "layered"
        assert len(layered_calls) == 1
        assert await world_structure_store.get_geo_failed(NOVEL)

        # 冷进程语义:只清内存响应缓存,DB 里的 layered + geo_failed 仍在
        visualization_service._map_cache.clear()
        m2 = await visualization_service.get_map_data(NOVEL, 1, 3)
        assert m2["layout_mode"] == "layered"
        assert len(layered_calls) == 1  # 没有第二次求解


@pytest.mark.asyncio
async def test_historical_sea_world_keeps_cached_layered(sea_db):
    """历史/混合世界仍偏好幻想分层,不触发 geo 重试。"""
    ws = await world_structure_store.load(NOVEL)
    ws.novel_genre_hint = "historical"
    ws.geo_type = "mixed"
    await world_structure_store.save(NOVEL, ws)

    async def _boom(*_a, **_kw):
        raise AssertionError("historical sea realm must not retry geo")

    with patch(
        "src.services.visualization_service.geo_auto_resolve",
        AsyncMock(side_effect=_boom),
    ):
        m = await visualization_service.get_map_data(NOVEL, 1, 3)

    assert m["layout_mode"] == "layered"
    assert await world_structure_store.get_geo_failed(NOVEL)
