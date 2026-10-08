"""Tests for the post-layout plausibility audit (src/services/map_plausibility.py).

Each check maps to a failure observed on the live 西游记 map; the fixtures
here are miniature versions of those failures.
"""

from src.services.map_plausibility import audit_map_layout

W, H = 8000.0, 4500.0
SQUARE_LANDMASS = [{
    "id": "lm0",
    # (1000,1000)-(7000,3500) — the middle of the canvas is land
    "coastline": [[1000, 1000], [7000, 1000], [7000, 3500], [1000, 3500], [1000, 1000]],
    "holes": [],
}]


def _loc(name, parent=None, tier="site", icon="generic", type_="地点"):
    return {"name": name, "parent": parent, "tier": tier, "icon": icon, "type": type_}


def _run(locations, layout, constraints, landmasses=None):
    return audit_map_layout(
        locations, layout, constraints, landmasses or [], W, H,
    )


def test_clean_map_passes():
    locations = [
        _loc("天下", None, "world"),
        _loc("东胜神洲", "天下", "continent"),
        _loc("西牛贺洲", "天下", "continent"),
        _loc("南赡部洲", "天下", "continent"),
        _loc("北俱芦洲", "天下", "continent"),
        _loc("南海", "天下", "region", "water", "region"),
    ]
    layout = [
        {"name": "东胜神洲", "x": 6500, "y": 2250},
        {"name": "西牛贺洲", "x": 1500, "y": 2250},
        {"name": "南赡部洲", "x": 4000, "y": 3500},
        {"name": "北俱芦洲", "x": 4000, "y": 1000},
        {"name": "南海", "x": 4000, "y": 4000},  # in the sea (off the landmass)
    ]
    report = _run(locations, layout, [], SQUARE_LANDMASS)
    assert report["ok"], report["summary"]
    assert report["violation_count"] == 0


def test_cardinal_violation_north_continent_in_south():
    locations = [_loc("天下", None, "world"), _loc("北俱芦洲", "天下", "continent")]
    layout = [{"name": "北俱芦洲", "x": 6200, "y": 3000}]  # south-east, not north
    report = _run(locations, layout, [])
    assert not report["ok"]
    assert report["cardinal_violations"][0]["name"] == "北俱芦洲"
    assert report["cardinal_violations"][0]["expected"] == "北"


def test_cardinal_ignores_non_suffix_names():
    # 东土大唐's 东 is relative to 天竺, not the canvas — must not be checked.
    locations = [_loc("天下", None, "world"), _loc("东土大唐", "天下", "kingdom")]
    layout = [{"name": "东土大唐", "x": 1000, "y": 2250}]  # west half, fine
    report = _run(locations, layout, [])
    assert report["cardinal_violations"] == []


def test_sibling_spread_continent_pile():
    locations = [_loc("天下", None, "world")] + [
        _loc(n, "天下", "continent") for n in ("东胜神洲", "南赡部洲", "北俱芦洲")
    ]
    # The 西游记 failure: three continents 54-75 px apart at one point.
    layout = [
        {"name": "东胜神洲", "x": 6185, "y": 3026},
        {"name": "南赡部洲", "x": 6254, "y": 2998},
        {"name": "北俱芦洲", "x": 6218, "y": 2959},
    ]
    report = _run(locations, layout, [])
    assert not report["ok"]
    v = report["sibling_spread_violations"][0]
    assert v["parent"] == "天下" and v["tier"] == "continent"
    assert v["distance"] < 100


def test_containment_violation_kingdom_far_from_capital():
    locations = [
        _loc("大唐国", None, "kingdom"),
        _loc("长安城", "大唐国", "city"),
    ]
    # 大唐国 contains 长安城 (high) but they ended up 3000 px apart.
    layout = [
        {"name": "大唐国", "x": 6294, "y": 2998},
        {"name": "长安城", "x": 3402, "y": 4064},
    ]
    constraints = [{
        "source": "大唐国", "target": "长安城",
        "relation_type": "contains", "value": "长安城是大唐国首都",
        "confidence": "high",
    }]
    report = _run(locations, layout, constraints)
    assert not report["ok"]
    v = report["containment_violations"][0]
    assert v["source"] == "大唐国" and v["target"] == "长安城"
    assert v["distance"] > 2800


def test_containment_medium_confidence_ignored():
    locations = [_loc("A", None, "kingdom"), _loc("B", "A", "city")]
    layout = [{"name": "A", "x": 6000, "y": 3000}, {"name": "B", "x": 1000, "y": 1000}]
    constraints = [{
        "source": "A", "target": "B", "relation_type": "contains",
        "value": "", "confidence": "medium",
    }]
    report = _run(locations, layout, constraints)
    assert report["containment_violations"] == []


def test_direction_violation_backwards_constraint():
    locations = [_loc("南赡部洲", None, "continent"), _loc("西牛贺洲", None, "continent")]
    # Constraint says 南赡部洲 is WEST of 西牛贺洲; the layout has it east.
    layout = [
        {"name": "南赡部洲", "x": 6254, "y": 2998},
        {"name": "西牛贺洲", "x": 1349, "y": 2433},
    ]
    constraints = [{
        "source": "南赡部洲", "target": "西牛贺洲",
        "relation_type": "direction", "value": "west_of", "confidence": "high",
    }]
    report = _run(locations, layout, constraints)
    assert not report["ok"]
    v = report["direction_violations"][0]
    assert v["source"] == "南赡部洲"
    assert v["angular_error"] > 135


def test_direction_satisfied_within_tolerance():
    locations = [_loc("A", None, "continent"), _loc("B", None, "continent")]
    layout = [{"name": "A", "x": 6000, "y": 2400}, {"name": "B", "x": 2000, "y": 2100}]
    # A is ESE of B — within one 45° step of east_of, must pass.
    constraints = [{
        "source": "A", "target": "B", "relation_type": "direction",
        "value": "east_of", "confidence": "high",
    }]
    report = _run(locations, layout, constraints)
    assert report["direction_violations"] == []


def test_sea_on_land_regression_alarm():
    locations = [
        _loc("南海", None, "region", "water", "region"),  # type drifted, name suffix saves it
    ]
    layout = [{"name": "南海", "x": 4000, "y": 2250}]  # dead centre of the landmass
    report = _run(locations, layout, [], SQUARE_LANDMASS)
    assert not report["ok"]
    assert report["sea_on_land"][0]["name"] == "南海"


def test_sea_in_water_passes():
    locations = [_loc("南海", None, "region", "water", "region")]
    layout = [{"name": "南海", "x": 4000, "y": 4000}]  # below the landmass = sea
    report = _run(locations, layout, [], SQUARE_LANDMASS)
    assert report["sea_on_land"] == []
