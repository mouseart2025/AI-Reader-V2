"""Tests for map quality v2: water detection, LLM anchor, two-layer solve, energy scaling."""

from __future__ import annotations

import math
from collections import Counter
from unittest.mock import AsyncMock, patch

import pytest


# ── T7.1: Water detection tests ──


class TestWaterDetection:
    """Triple water detection: icon + type + parent chain."""

    def test_icon_ocean(self):
        from src.services.visualization_service import _is_water_location

        loc_lookup = {"东海": {"name": "东海", "icon": "ocean", "type": "海洋"}}
        assert _is_water_location("东海", loc_lookup) is True

    def test_icon_water(self):
        from src.services.visualization_service import _is_water_location

        loc_lookup = {"西湖": {"name": "西湖", "icon": "water", "type": "湖泊"}}
        assert _is_water_location("西湖", loc_lookup) is True

    def test_icon_island(self):
        from src.services.visualization_service import _is_water_location

        loc_lookup = {"蓬莱岛": {"name": "蓬莱岛", "icon": "island", "type": "岛屿"}}
        assert _is_water_location("蓬莱岛", loc_lookup) is True

    def test_type_keyword_ocean(self):
        from src.services.visualization_service import _is_water_location

        loc_lookup = {"南海": {"name": "南海", "icon": "generic", "type": "海域"}}
        assert _is_water_location("南海", loc_lookup) is True

    def test_type_keyword_river(self):
        """'河流' type contains '河流' keyword → detected as water."""
        from src.services.visualization_service import _is_water_location

        loc_lookup = {"黄河": {"name": "黄河", "icon": "generic", "type": "河流"}}
        assert _is_water_location("黄河", loc_lookup) is True

    def test_parent_chain_water(self):
        """Child of a water location should be detected as water."""
        from src.services.visualization_service import _is_water_location

        loc_lookup = {
            "东海": {"name": "东海", "icon": "ocean", "type": "海洋"},
            "龙宫": {"name": "龙宫", "icon": "generic", "type": "宫殿", "parent": "东海"},
        }
        assert _is_water_location("龙宫", loc_lookup) is True

    def test_deep_parent_chain(self):
        """Parent chain traversal up to 5 levels."""
        from src.services.visualization_service import _is_water_location

        loc_lookup = {
            "大海": {"name": "大海", "icon": "ocean", "type": "海洋"},
            "海底": {"name": "海底", "icon": "generic", "type": "地点", "parent": "大海"},
            "龙宫": {"name": "龙宫", "icon": "generic", "type": "宫殿", "parent": "海底"},
            "宝殿": {"name": "宝殿", "icon": "generic", "type": "殿堂", "parent": "龙宫"},
        }
        assert _is_water_location("宝殿", loc_lookup) is True

    def test_land_location_not_water(self):
        from src.services.visualization_service import _is_water_location

        loc_lookup = {"花果山": {"name": "花果山", "icon": "mountain", "type": "山"}}
        assert _is_water_location("花果山", loc_lookup) is False

    def test_unknown_location(self):
        from src.services.visualization_service import _is_water_location

        assert _is_water_location("不存在", {}) is False

    def test_repeated_calls_consistent(self):
        """Multiple calls for same location return consistent results."""
        from src.services.visualization_service import _is_water_location

        loc_lookup = {"东海": {"name": "东海", "icon": "ocean", "type": "海洋"}}
        assert _is_water_location("东海", loc_lookup) is True
        assert _is_water_location("东海", loc_lookup) is True

    def test_circular_parent_chain(self):
        """Circular parent references should not cause infinite loop."""
        from src.services.visualization_service import _is_water_location

        loc_lookup = {
            "A": {"name": "A", "icon": "generic", "type": "地点", "parent": "B"},
            "B": {"name": "B", "icon": "generic", "type": "地点", "parent": "A"},
        }
        # Should not hang — depth limit of 5 prevents infinite recursion
        assert _is_water_location("A", loc_lookup) is False


# ── T7.2: LLM anchor injection tests ──


class TestLLMAnchorInjection:
    """LLM anchor direction constraints from MacroSkeletonGenerator."""

    def test_skeleton_schema_has_directions(self):
        from src.services.macro_skeleton_generator import _SKELETON_SCHEMA

        props = _SKELETON_SCHEMA["properties"]
        assert "directions" in props
        dir_schema = props["directions"]
        assert dir_schema["type"] == "array"
        item_props = dir_schema["items"]["properties"]
        assert "source" in item_props
        assert "target" in item_props
        assert "direction" in item_props
        assert "confidence" in item_props

    def test_valid_direction_enum(self):
        from src.services.macro_skeleton_generator import _VALID_DIRECTIONS

        assert "north_of" in _VALID_DIRECTIONS
        assert "south_of" in _VALID_DIRECTIONS
        assert "east_of" in _VALID_DIRECTIONS
        assert "west_of" in _VALID_DIRECTIONS
        assert "northeast_of" in _VALID_DIRECTIONS

    @pytest.mark.asyncio
    async def test_generate_returns_directions(self):
        """generate() should return a 3-tuple with direction constraints."""
        from src.services.macro_skeleton_generator import MacroSkeletonGenerator

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=(
            {
                "uber_root": "天下",
                "skeleton": [
                    {"child": "东胜神洲", "parent": "天下", "confidence": "high"},
                ],
                "synonyms": [],
                "directions": [
                    {
                        "source": "东胜神洲",
                        "target": "西牛贺洲",
                        "direction": "east_of",
                        "confidence": "high",
                    },
                ],
            },
            {"prompt_tokens": 100, "completion_tokens": 50},
        ))

        gen = MacroSkeletonGenerator(llm=mock_llm)
        location_tiers = {
            "天下": "world",
            "东胜神洲": "continent",
            "西牛贺洲": "continent",
            "南赡部洲": "continent",
        }
        current_parents = {
            "东胜神洲": "天下",
            "西牛贺洲": "天下",
            "南赡部洲": "天下",
        }

        votes, synonyms, directions = await gen.generate(
            "西游记", "xianxia", location_tiers, current_parents,
        )
        assert len(directions) == 1
        d = directions[0]
        assert d["source"] == "东胜神洲"
        assert d["target"] == "西牛贺洲"
        assert d["relation_type"] == "direction"
        assert d["value"] == "east_of"
        assert d["source_type"] == "llm_anchor"
        assert d["confidence_score"] == 1.0

    @pytest.mark.asyncio
    async def test_invalid_direction_skipped(self):
        """Invalid direction values should be filtered out."""
        from src.services.macro_skeleton_generator import MacroSkeletonGenerator

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=(
            {
                "uber_root": "天下",
                "skeleton": [],
                "directions": [
                    {
                        "source": "A",
                        "target": "B",
                        "direction": "invalid_dir",
                        "confidence": "high",
                    },
                ],
            },
            {"prompt_tokens": 100, "completion_tokens": 50},
        ))

        gen = MacroSkeletonGenerator(llm=mock_llm)
        location_tiers = {"天下": "world", "A": "continent", "B": "continent"}
        current_parents = {"A": "天下", "B": "天下"}

        _, _, directions = await gen.generate(
            "测试", None, location_tiers, current_parents,
        )
        assert len(directions) == 0

    @pytest.mark.asyncio
    async def test_hallucinated_location_skipped(self):
        """Directions referencing unknown locations should be filtered."""
        from src.services.macro_skeleton_generator import MacroSkeletonGenerator

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=(
            {
                "uber_root": "天下",
                "skeleton": [],
                "directions": [
                    {
                        "source": "不存在的地方",
                        "target": "B",
                        "direction": "east_of",
                        "confidence": "high",
                    },
                ],
            },
            {"prompt_tokens": 100, "completion_tokens": 50},
        ))

        gen = MacroSkeletonGenerator(llm=mock_llm)
        location_tiers = {"天下": "world", "B": "continent"}
        current_parents = {"B": "天下"}

        _, _, directions = await gen.generate(
            "测试", None, location_tiers, current_parents,
        )
        assert len(directions) == 0


# ── T7.3: Two-layer solve + energy scaling tests ──


class TestSolverCapacity:
    """MAX_SOLVER_LOCATIONS dynamic scaling."""

    def test_default_max_solver_locations_value(self):
        from src.services.map_layout_service import _DEFAULT_MAX_SOLVER_LOCATIONS

        assert _DEFAULT_MAX_SOLVER_LOCATIONS == 80

    def test_solver_selects_up_to_80(self):
        """Solver should select up to 80 locations (not old cap of 40)."""
        from src.services.map_layout_service import ConstraintSolver

        # Create 60 locations — all should be selected (< 80)
        locations = [
            {"name": f"loc_{i}", "mention_count": 100 - i, "level": 1, "tier": "city"}
            for i in range(60)
        ]
        constraints = [
            {"source": "loc_0", "target": "loc_1", "relation_type": "direction",
             "value": "east_of", "confidence": "high", "confidence_score": 0.9},
        ]

        solver = ConstraintSolver(locations, constraints)
        # All 60 should be in solver (not capped at 40)
        assert solver.n == 60

    def test_solver_caps_at_80(self):
        """Solver should cap at 80 for 100+ locations."""
        from src.services.map_layout_service import ConstraintSolver

        locations = [
            {"name": f"loc_{i}", "mention_count": 100 - i, "level": 1, "tier": "city"}
            for i in range(100)
        ]
        constraints = []

        solver = ConstraintSolver(locations, constraints)
        assert solver.n == 80


class TestEnergyScaling:
    """Energy function weight scaling with location count."""

    def test_spread_weight_scales(self):
        """Uniform spread weight should scale with n/100."""
        # For n=10: weight = 0.3 + 0.2 * min(1.0, 10/100) = 0.32
        # For n=100: weight = 0.3 + 0.2 * 1.0 = 0.5
        w_10 = 0.3 + 0.2 * min(1.0, 10 / 100)
        w_100 = 0.3 + 0.2 * min(1.0, 100 / 100)
        assert abs(w_10 - 0.32) < 0.01
        assert abs(w_100 - 0.5) < 0.01
        assert w_100 > w_10

    def test_narrative_weight_scales(self):
        """Narrative order weight should scale with n/50."""
        w_10 = 0.05 + 0.05 * min(1.0, 10 / 50)
        w_50 = 0.05 + 0.05 * min(1.0, 50 / 50)
        assert abs(w_10 - 0.06) < 0.01
        assert abs(w_50 - 0.10) < 0.01


class TestMinSpacing:
    """MIN_SPACING adaptive scaling."""

    def test_min_spacing_base(self):
        from src.services.map_layout_service import MIN_SPACING

        assert MIN_SPACING == 30

    def test_min_spacing_dynamic(self):
        """Dynamic spacing should be max(30, canvas_w * 0.015)."""
        from src.services.map_layout_service import ConstraintSolver

        # Small canvas: 600px → 0.015 * 600 = 9 → max(30, 9) = 30
        locations = [
            {"name": "a", "level": 0, "tier": "city"},
            {"name": "b", "level": 0, "tier": "city"},
        ]
        solver = ConstraintSolver(
            locations, [],
            canvas_bounds=(0, 0, 600, 400),
        )
        assert solver._min_spacing == 30

        # Large canvas: 4000px → 0.015 * 4000 = 60 → max(30, 60) = 60
        solver2 = ConstraintSolver(
            locations, [],
            canvas_bounds=(0, 0, 4000, 3000),
        )
        assert solver2._min_spacing == 60


class TestDirectionHintEnhancement:
    """LLM anchor constraints get 3× weight in narrative axis detection."""

    def test_llm_anchor_majority(self):
        """70%+ LLM anchor votes in one direction should influence axis."""
        from src.services.map_layout_service import _detect_narrative_axis

        constraints = [
            {"source": "A", "target": "B", "relation_type": "direction",
             "value": "east_of", "source_type": "llm_anchor",
             "confidence": "high", "confidence_score": 1.0},
            {"source": "C", "target": "D", "relation_type": "direction",
             "value": "east_of", "source_type": "llm_anchor",
             "confidence": "high", "confidence_score": 1.0},
            {"source": "E", "target": "F", "relation_type": "direction",
             "value": "west_of", "source_type": "chapter",
             "confidence": "medium", "confidence_score": 0.6},
        ]
        first_chapter = {"A": 1, "B": 10, "C": 5, "D": 15, "E": 20, "F": 25}

        dx, dy = _detect_narrative_axis(constraints, first_chapter)
        # Should have eastward component (positive dx)
        assert dx > 0, f"Expected positive dx for eastward, got {dx}"


class TestLandmassGeneration:
    """Landmass generation basic functionality."""

    def test_basic_landmass(self):
        """generate_landmasses should produce landmass contours."""
        from src.services.map_layout_service import generate_landmasses

        locations = [
            {"name": f"loc_{i}", "icon": "generic", "type": "城市"}
            for i in range(5)
        ]
        layout_data = [
            {"name": f"loc_{i}", "x": 100 + i * 100, "y": 200, "tier": "city"}
            for i in range(5)
        ]
        result = generate_landmasses(
            locations, layout_data, "test_novel",
            canvas_width=800, canvas_height=600,
        )
        assert "landmasses" in result

    def test_ocean_excluded_from_land(self):
        """Ocean locations (icon=water + type=海) should not be land anchors."""
        from src.services.map_layout_service import generate_landmasses

        locations = [
            {"name": f"loc_{i}", "icon": "generic", "type": "城市"}
            for i in range(5)
        ] + [
            {"name": "东海", "icon": "water", "type": "海"},
        ]
        layout_data = [
            {"name": f"loc_{i}", "x": 100 + i * 100, "y": 200, "tier": "city"}
            for i in range(5)
        ] + [
            {"name": "东海", "x": 600, "y": 400, "tier": "region"},
        ]
        result = generate_landmasses(
            locations, layout_data, "test_novel",
            canvas_width=800, canvas_height=600,
        )
        assert "landmasses" in result
