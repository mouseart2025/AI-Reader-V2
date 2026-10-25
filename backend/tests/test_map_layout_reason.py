"""Map layout reason and explicit "地理 / 层级" override.

The map used to fall back silently: a realistic novel could get a layered map
because of a realm layer, low geo coverage, or a geo failure, and the UI had no
way to explain or override it. `get_map_data(force_layout=...)` now supports an
explicit choice and returns `layout_reason` / `geo_coverage`.
"""

import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest

from src.db import world_structure_store
from src.models.world_structure import LayerType, MapLayer, WorldStructure
from src.services import visualization_service


class _NonClosing:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


@contextmanager
def _patched(db):
    async def _factory():
        return _NonClosing(db)

    def _fake_layered(_ws, locations, *_a, **_kw):
        return {
            "overworld": [
                {"name": loc["name"], "x": 100.0 + i * 20.0, "y": 200.0}
                for i, loc in enumerate(locations)
            ],
            "underwater": [],
        }

    with (
        patch("src.services.alias_resolver.get_connection", _factory),
        patch("src.db.entity_override_store.get_connection", _factory),
        patch("src.db.chapter_fact_store.get_connection", _factory),
        patch("src.db.chapter_store.get_connection", _factory),
        patch("src.db.world_structure_store.get_connection", _factory),
        patch("src.services.visualization_service.get_connection", _factory),
        patch(
            "src.services.visualization_service.compute_layered_layout",
            _fake_layered,
        ),
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
        yield


LAND = [f"地点{i}" for i in range(20)]
SEA = [f"海底{i}" for i in range(6)]


async def _seed(db, novel_id: str, genre: str, with_sea: bool) -> None:
    await db.execute("INSERT INTO novels (id, title) VALUES (?, ?)", (novel_id, novel_id))
    locations = [{"name": n, "type": "城市"} for n in LAND]
    locations += [{"name": n, "type": "海底"} for n in SEA] if with_sea else []
    for ch in (1, 2, 3):
        cur = await db.execute(
            "INSERT INTO chapters (novel_id, chapter_num, title, content) "
            "VALUES (?, ?, ?, ?)",
            (novel_id, ch, f"第{ch}章", "正文"),
        )
        fact = {
            "chapter_id": ch,
            "novel_id": novel_id,
            "locations": locations,
            "characters": [],
            "spatial_relationships": [],
            "events": [],
        }
        await db.execute(
            "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json) "
            "VALUES (?, ?, ?)",
            (novel_id, cur.lastrowid, json.dumps(fact, ensure_ascii=False)),
        )
    layers = [
        MapLayer(layer_id="overworld", name="主世界", layer_type=LayerType.overworld),
    ]
    if with_sea:
        layers.append(
            MapLayer(layer_id="underwater", name="海底/龙宫", layer_type=LayerType.underwater)
        )
    ws = WorldStructure(
        novel_id=novel_id,
        layers=layers,
        location_layer_map={
            **{n: "overworld" for n in LAND},
            **({n: "underwater" for n in SEA} if with_sea else {}),
        },
        novel_genre_hint=genre,
        geo_type="mixed" if with_sea else "realistic",
    )
    async def _factory():
        return _NonClosing(db)

    with patch("src.db.world_structure_store.get_connection", _factory):
        await world_structure_store.save(novel_id, ws)
    await db.commit()


class _FakeResolver:
    def __init__(self, names):
        self.names = list(names)

    def project_to_canvas(self, resolved, locations, _cw, _ch):
        return [
            {"name": name, "x": 100.0 + i * 30.0, "y": 200.0}
            for i, name in enumerate(resolved)
        ]


@pytest.mark.asyncio
async def test_auto_realm_layers_reason(memory_db):
    await _seed(memory_db, "reason-realm", "historical", with_sea=True)
    visualization_service._map_cache.clear()
    with _patched(memory_db):
        m = await visualization_service.get_map_data("reason-realm", 1, 3)
    assert m["layout_mode"] in ("layered", "hierarchy")
    assert m["layout_reason"] == "realm_layers"
    assert m["layout_choice"] == "auto"


@pytest.mark.asyncio
async def test_force_geographic_bypasses_realm_layers(memory_db):
    await _seed(memory_db, "reason-force-geo", "historical", with_sea=True)
    visualization_service._map_cache.clear()
    resolved = {name: (30.0, 110.0) for name in LAND[:10]}

    async def _fake_resolve(*_a, **_kw):
        return ("cn", "mixed", _FakeResolver(LAND[:10]), resolved)

    with (
        _patched(memory_db),
        patch(
            "src.services.visualization_service.geo_auto_resolve",
            AsyncMock(side_effect=_fake_resolve),
        ),
    ):
        m = await visualization_service.get_map_data(
            "reason-force-geo", 1, 3, force_layout="geographic",
        )

    assert m["layout_mode"] == "geographic"
    assert m["layout_reason"] == "manual_geographic"
    assert m["layout_choice"] == "manual"
    assert m["geo_coverage"]["resolved"] == 10


@pytest.mark.asyncio
async def test_force_layered_skips_geo(memory_db):
    await _seed(memory_db, "reason-force-layered", "realistic", with_sea=False)
    visualization_service._map_cache.clear()

    async def _boom(*_a, **_kw):
        raise AssertionError("force_layout=layered must not call geo resolver")

    with (
        _patched(memory_db),
        patch(
            "src.services.visualization_service.geo_auto_resolve",
            AsyncMock(side_effect=_boom),
        ),
    ):
        m = await visualization_service.get_map_data(
            "reason-force-layered", 1, 3, force_layout="layered",
        )

    assert m["layout_mode"] in ("layered", "hierarchy")
    assert m["layout_reason"] == "manual_layered"
    assert m["layout_choice"] == "manual"


@pytest.mark.asyncio
async def test_low_geo_coverage_reason(memory_db):
    await _seed(memory_db, "reason-low", "realistic", with_sea=False)
    visualization_service._map_cache.clear()
    resolved = {LAND[0]: (30.0, 110.0)}

    async def _fake_resolve(*_a, **_kw):
        return ("cn", "mixed", _FakeResolver(LAND), resolved)

    with (
        _patched(memory_db),
        patch(
            "src.services.visualization_service.geo_auto_resolve",
            AsyncMock(side_effect=_fake_resolve),
        ),
    ):
        m = await visualization_service.get_map_data("reason-low", 1, 3)

    assert m["layout_mode"] in ("layered", "hierarchy")
    assert m["layout_reason"] == "low_geo_coverage"
    assert m["geo_coverage"]["ratio"] < 0.1


@pytest.mark.asyncio
async def test_no_resolved_coords_reason(memory_db):
    await _seed(memory_db, "reason-none", "realistic", with_sea=False)
    visualization_service._map_cache.clear()

    async def _empty_resolve(*_a, **_kw):
        return ("cn", "realistic", None, {})

    with (
        _patched(memory_db),
        patch(
            "src.services.visualization_service.geo_auto_resolve",
            AsyncMock(side_effect=_empty_resolve),
        ),
    ):
        m = await visualization_service.get_map_data("reason-none", 1, 3)

    assert m["layout_mode"] in ("layered", "hierarchy")
    assert m["layout_reason"] == "geo_unavailable"
