"""Tests for spatial scale detection — 9-level scale, per-layer scale, backward compatibility."""

import pytest
from collections import Counter

from src.models.world_structure import (
    LayerType,
    MapLayer,
    SpatialScale,
    WorldStructure,
)
from src.services.world_structure_agent import WorldStructureAgent
from src.services.map_layout_service import SPATIAL_SCALE_CANVAS


# ── SpatialScale enum tests ──


class TestSpatialScaleEnum:
    """9-level SpatialScale enum completeness."""

    def test_nine_levels(self):
        assert len(SpatialScale) == 9

    def test_all_levels_present(self):
        expected = {"room", "building", "district", "city", "national",
                    "continental", "planetary", "cosmic", "interstellar"}
        actual = {s.value for s in SpatialScale}
        assert actual == expected

    def test_canvas_mapping_covers_all(self):
        """Every SpatialScale value has a canvas size mapping."""
        for scale in SpatialScale:
            assert scale.value in SPATIAL_SCALE_CANVAS, f"Missing canvas mapping for {scale.value}"

    def test_canvas_16_9_ratio(self):
        """All canvas sizes should maintain 16:9 aspect ratio."""
        for scale_name, (w, h) in SPATIAL_SCALE_CANVAS.items():
            if scale_name in ("urban", "local"):
                continue  # legacy aliases
            ratio = w / h
            assert abs(ratio - 16 / 9) < 0.01, f"{scale_name}: {w}x{h} ratio={ratio:.3f}"


# ── SPATIAL_SCALE_CANVAS tests ──


class TestCanvasMapping:
    """Canvas size mapping covers all scales."""

    def test_ascending_sizes(self):
        """Canvas sizes should increase with scale."""
        ordered = ["room", "building", "district", "city", "national",
                   "continental", "planetary", "cosmic", "interstellar"]
        for i in range(len(ordered) - 1):
            w1 = SPATIAL_SCALE_CANVAS[ordered[i]][0]
            w2 = SPATIAL_SCALE_CANVAS[ordered[i + 1]][0]
            assert w1 < w2, f"{ordered[i]}({w1}) should be smaller than {ordered[i+1]}({w2})"

    def test_legacy_aliases(self):
        """Legacy 'urban' and 'local' aliases map to same sizes as new names."""
        assert SPATIAL_SCALE_CANVAS["urban"] == SPATIAL_SCALE_CANVAS["district"]
        assert SPATIAL_SCALE_CANVAS["local"] == SPATIAL_SCALE_CANVAS["room"]


# ── _detect_spatial_scale tests ──


def _make_agent(
    genre: str | None = None,
    tiers: dict | None = None,
    layers: list | None = None,
) -> WorldStructureAgent:
    """Create a WorldStructureAgent with minimal WorldStructure for testing."""
    ws = WorldStructure(novel_id="test")
    ws.novel_genre_hint = genre
    ws.location_tiers = tiers or {}
    if layers:
        ws.layers = layers
    else:
        ws.layers = [MapLayer(layer_id="overworld", name="主世界", layer_type=LayerType.overworld)]
    agent = WorldStructureAgent.__new__(WorldStructureAgent)
    agent.structure = ws
    return agent


class TestDetectSpatialScale:
    """Enhanced _detect_spatial_scale with 9 levels."""

    def test_fantasy_genre_cosmic(self):
        """Fantasy genre with enough locations should default to cosmic."""
        tiers = {f"地点{i}": "city" for i in range(25)}
        agent = _make_agent(genre="fantasy", tiers=tiers)
        assert agent._detect_spatial_scale() == "cosmic"

    def test_wuxia_genre_national(self):
        """Wuxia genre with enough locations should be at least national."""
        tiers = {f"地点{i}": "city" for i in range(25)}
        agent = _make_agent(genre="wuxia", tiers=tiers)
        assert agent._detect_spatial_scale() == "national"

    def test_fantasy_few_locations_capped(self):
        """Fantasy genre with ≤5 locations should cap at building (count overrides genre)."""
        agent = _make_agent(genre="fantasy", tiers={"A": "city", "B": "city"})
        assert agent._detect_spatial_scale() == "building"

    def test_urban_genre_ceiling(self):
        """Urban genre should cap at city."""
        tiers = {f"地点{i}": "city" for i in range(25)}
        tiers["大区"] = "continent"
        agent = _make_agent(genre="urban", tiers=tiers)
        assert agent._detect_spatial_scale() == "city"

    def test_continent_tier_continental(self):
        """Continent tier present → at least continental."""
        tiers = {f"城市{i}": "city" for i in range(25)}
        tiers["东胜神洲"] = "continent"
        agent = _make_agent(tiers=tiers)
        assert agent._detect_spatial_scale() == "continental"

    def test_continent_with_sky_layer_cosmic(self):
        """Continent tier + sky layer → cosmic."""
        tiers = {f"城市{i}": "city" for i in range(25)}
        tiers["东胜神洲"] = "continent"
        agent = _make_agent(
            tiers=tiers,
            layers=[
                MapLayer(layer_id="overworld", name="主世界", layer_type=LayerType.overworld),
                MapLayer(layer_id="celestial", name="天界", layer_type=LayerType.sky),
            ],
        )
        assert agent._detect_spatial_scale() == "cosmic"

    def test_few_locations_building(self):
        """≤5 locations should be building scale."""
        agent = _make_agent(tiers={f"房间{i}": "building" for i in range(3)})
        assert agent._detect_spatial_scale() == "building"

    def test_few_locations_district(self):
        """6-15 locations should be at most district scale."""
        agent = _make_agent(tiers={f"地点{i}": "site" for i in range(10)})
        result = agent._detect_spatial_scale()
        assert result in ("building", "district")

    def test_twenty_locations_max_city(self):
        """≤20 locations should cap at city (not continental/cosmic)."""
        agent = _make_agent(tiers={f"地点{i}": "city" for i in range(18)})
        result = agent._detect_spatial_scale()
        assert result == "city"

    def test_highest_tier_building(self):
        """All building-tier locations → building scale."""
        agent = _make_agent(tiers={
            "大厅": "building", "书房": "building", "卧室": "building",
            "厨房": "building", "花园": "building", "后院": "building",
        })
        assert agent._detect_spatial_scale() == "building"


# ── _detect_layer_scale tests ──


class TestDetectLayerScale:
    """Per-layer scale detection based on location count."""

    def test_few_locations_building(self):
        assert WorldStructureAgent._detect_layer_scale(3) == "building"

    def test_medium_locations_district(self):
        assert WorldStructureAgent._detect_layer_scale(10) == "district"

    def test_many_locations_city(self):
        assert WorldStructureAgent._detect_layer_scale(30) == "city"

    def test_large_national(self):
        assert WorldStructureAgent._detect_layer_scale(100) == "national"

    def test_very_large_continental(self):
        assert WorldStructureAgent._detect_layer_scale(200) == "continental"

    def test_boundary_5(self):
        assert WorldStructureAgent._detect_layer_scale(5) == "building"

    def test_boundary_6(self):
        assert WorldStructureAgent._detect_layer_scale(6) == "district"

    def test_boundary_15(self):
        assert WorldStructureAgent._detect_layer_scale(15) == "district"

    def test_boundary_16(self):
        assert WorldStructureAgent._detect_layer_scale(16) == "city"


# ── LayerType enum tests ──


class TestLayerTypeEnum:
    """LayerType enum includes underwater."""

    def test_underwater_exists(self):
        assert LayerType.underwater == "underwater"

    def test_all_types(self):
        expected = {"overworld", "underground", "sky", "sea", "pocket", "spirit", "underwater"}
        actual = {lt.value for lt in LayerType}
        assert actual == expected


# ── WorldStructure model tests ──


class TestWorldStructureModel:
    """WorldStructure model has new fields."""

    def test_completed_spatial_relations_default(self):
        ws = WorldStructure(novel_id="test")
        assert ws.completed_spatial_relations == []

    def test_layer_spatial_scales_default(self):
        ws = WorldStructure(novel_id="test")
        assert ws.layer_spatial_scales == {}

    def test_completed_spatial_relations_serialization(self):
        ws = WorldStructure(novel_id="test")
        ws.completed_spatial_relations = [
            {"source": "A", "target": "B", "relation_type": "direction",
             "value": "north_of", "confidence": "high", "evidence_chapters": [1, 5]},
        ]
        dumped = ws.model_dump()
        assert len(dumped["completed_spatial_relations"]) == 1
        assert dumped["completed_spatial_relations"][0]["source"] == "A"

    def test_layer_spatial_scales_serialization(self):
        ws = WorldStructure(novel_id="test")
        ws.layer_spatial_scales = {"celestial": "building", "underworld": "district"}
        dumped = ws.model_dump()
        assert dumped["layer_spatial_scales"]["celestial"] == "building"
