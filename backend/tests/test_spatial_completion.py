"""Tests for spatial completion: constraint enhancement, gap detection, contradiction filtering."""

from collections import Counter

from src.services.spatial_completion_agent import SpatialCompletionAgent
from src.services.visualization_service import (
    _enhance_constraints,
)

# ── _enhance_constraints tests ──


class TestTrajectoryConstraints:
    """travel_sequence constraints from character trajectories."""

    def _make_locations(self, names, tier="city", parent=None):
        return [
            {"name": n, "tier": tier, "parent": parent, "level": 0}
            for n in names
        ]

    def test_basic_travel_sequence(self):
        """Consecutive chapter movement generates travel_sequence constraint."""
        locations = self._make_locations(["花果山", "傲来国"])
        trajectories = {
            "孙悟空": [
                {"location": "花果山", "chapter": 1},
                {"location": "傲来国", "chapter": 2},
            ]
        }
        result = _enhance_constraints([], trajectories, locations)
        travel = [c for c in result if c["relation_type"] == "travel_sequence"]
        assert len(travel) == 1
        assert {travel[0]["source"], travel[0]["target"]} == {"花果山", "傲来国"}

    def test_skip_non_consecutive_chapters(self):
        """Chapter gap > 1 should not generate constraint (teleportation)."""
        locations = self._make_locations(["花果山", "灵台山"])
        trajectories = {
            "孙悟空": [
                {"location": "花果山", "chapter": 1},
                {"location": "灵台山", "chapter": 5},
            ]
        }
        result = _enhance_constraints([], trajectories, locations)
        travel = [c for c in result if c["relation_type"] == "travel_sequence"]
        assert len(travel) == 0

    def test_skip_same_location(self):
        """Same location in consecutive chapters should not generate constraint."""
        locations = self._make_locations(["花果山"])
        trajectories = {
            "孙悟空": [
                {"location": "花果山", "chapter": 1},
                {"location": "花果山", "chapter": 2},
            ]
        }
        result = _enhance_constraints([], trajectories, locations)
        assert len(result) == 0

    def test_skip_tier_gap_too_large(self):
        """Tier gap > 1 should not generate constraint."""
        locations = [
            {"name": "东胜神洲", "tier": "continent", "parent": None, "level": 0},
            {"name": "陈家庄", "tier": "site", "parent": None, "level": 0},
        ]
        trajectories = {
            "唐僧": [
                {"location": "东胜神洲", "chapter": 1},
                {"location": "陈家庄", "chapter": 2},
            ]
        }
        result = _enhance_constraints([], trajectories, locations)
        travel = [c for c in result if c["relation_type"] == "travel_sequence"]
        assert len(travel) == 0

    def test_no_overwrite_existing(self):
        """Existing constraints should not be overwritten."""
        locations = self._make_locations(["花果山", "傲来国"])
        trajectories = {
            "孙悟空": [
                {"location": "花果山", "chapter": 1},
                {"location": "傲来国", "chapter": 2},
            ]
        }
        existing = [{
            "source": "花果山", "target": "傲来国",
            "relation_type": "direction", "value": "east_of",
            "confidence": "high",
        }]
        result = _enhance_constraints(existing, trajectories, locations)
        travel = [c for c in result if c["relation_type"] == "travel_sequence"]
        assert len(travel) == 1  # adds travel_sequence since it's a different relation_type


class TestTransitiveDirectionInference:
    """Transitive direction inference: A→B→C ⇒ A→C."""

    def _make_locations(self, names):
        return [{"name": n, "tier": "city", "parent": None, "level": 0} for n in names]

    def test_basic_transitive(self):
        """A north B, B north C → A north C."""
        locations = self._make_locations(["A", "B", "C"])
        constraints = [
            {"source": "A", "target": "B", "relation_type": "direction",
             "value": "north_of", "confidence": "high"},
            {"source": "B", "target": "C", "relation_type": "direction",
             "value": "north_of", "confidence": "high"},
        ]
        result = _enhance_constraints(constraints, {}, locations)
        inferred = [c for c in result if c.get("source_type") == "transitive"]
        assert len(inferred) == 1
        assert inferred[0]["source"] == "A"
        assert inferred[0]["target"] == "C"
        assert inferred[0]["value"] == "north_of"
        assert inferred[0]["confidence"] == "medium"  # decayed from high

    def test_confidence_decay(self):
        """Medium confidence sources → low confidence inference."""
        locations = self._make_locations(["A", "B", "C"])
        constraints = [
            {"source": "A", "target": "B", "relation_type": "direction",
             "value": "east_of", "confidence": "medium"},
            {"source": "B", "target": "C", "relation_type": "direction",
             "value": "east_of", "confidence": "medium"},
        ]
        result = _enhance_constraints(constraints, {}, locations)
        inferred = [c for c in result if c.get("source_type") == "transitive"]
        assert len(inferred) == 1
        assert inferred[0]["confidence"] == "low"

    def test_skip_low_confidence_source(self):
        """Low confidence source should not produce transitive inference."""
        locations = self._make_locations(["A", "B", "C"])
        constraints = [
            {"source": "A", "target": "B", "relation_type": "direction",
             "value": "west_of", "confidence": "low"},
            {"source": "B", "target": "C", "relation_type": "direction",
             "value": "west_of", "confidence": "low"},
        ]
        result = _enhance_constraints(constraints, {}, locations)
        inferred = [c for c in result if c.get("source_type") == "transitive"]
        assert len(inferred) == 0

    def test_skip_diagonal_directions(self):
        """Non-cardinal directions should not produce transitive inference."""
        locations = self._make_locations(["A", "B", "C"])
        constraints = [
            {"source": "A", "target": "B", "relation_type": "direction",
             "value": "northeast_of", "confidence": "high"},
            {"source": "B", "target": "C", "relation_type": "direction",
             "value": "northeast_of", "confidence": "high"},
        ]
        result = _enhance_constraints(constraints, {}, locations)
        inferred = [c for c in result if c.get("source_type") == "transitive"]
        assert len(inferred) == 0

    def test_no_overwrite_existing_direction(self):
        """Don't infer if direct relation already exists."""
        locations = self._make_locations(["A", "B", "C"])
        constraints = [
            {"source": "A", "target": "B", "relation_type": "direction",
             "value": "north_of", "confidence": "high"},
            {"source": "B", "target": "C", "relation_type": "direction",
             "value": "north_of", "confidence": "high"},
            {"source": "A", "target": "C", "relation_type": "direction",
             "value": "south_of", "confidence": "medium"},  # already exists
        ]
        result = _enhance_constraints(constraints, {}, locations)
        inferred = [c for c in result if c.get("source_type") == "transitive"]
        assert len(inferred) == 0


class TestCompletedRelationsInjection:
    """Inject completed_spatial_relations from WorldStructure."""

    def test_inject_completed(self):
        """Completed relations should be injected into constraints."""
        locations = [{"name": "A", "tier": "city", "parent": None, "level": 0}]
        completed = [
            {"source": "A", "target": "B", "relation_type": "direction",
             "value": "north_of", "confidence": "medium"},
        ]
        result = _enhance_constraints([], {}, locations, completed_relations=completed)
        assert len(result) == 1
        assert result[0]["value"] == "north_of"

    def test_no_overwrite_existing(self):
        """Completed relations should not overwrite chapter-extracted ones."""
        locations = [{"name": "A", "tier": "city", "parent": None, "level": 0}]
        existing = [
            {"source": "A", "target": "B", "relation_type": "direction",
             "value": "south_of", "confidence": "high"},
        ]
        completed = [
            {"source": "A", "target": "B", "relation_type": "direction",
             "value": "north_of", "confidence": "medium"},
        ]
        result = _enhance_constraints(existing, {}, locations, completed_relations=completed)
        # Should keep existing south_of, not add the completed north_of
        directions = [c for c in result if c["relation_type"] == "direction"]
        assert len(directions) == 1
        assert directions[0]["value"] == "south_of"


class TestConstraintMergePriority:
    """Constraint merge priority: existing > completed > inferred."""

    def test_priority_order(self):
        """Existing chapter-extracted > completed Phase B > Phase A inferred."""
        locations = [
            {"name": "A", "tier": "city", "parent": None, "level": 0},
            {"name": "B", "tier": "city", "parent": None, "level": 0},
            {"name": "C", "tier": "city", "parent": None, "level": 0},
        ]
        # Existing: A→B direction
        existing = [
            {"source": "A", "target": "B", "relation_type": "direction",
             "value": "north_of", "confidence": "high"},
        ]
        # Completed: A→B direction (should not overwrite)
        completed = [
            {"source": "A", "target": "B", "relation_type": "direction",
             "value": "south_of", "confidence": "medium"},
        ]
        result = _enhance_constraints(existing, {}, locations, completed_relations=completed)
        ab_dirs = [c for c in result if c["source"] == "A" and c["target"] == "B"
                   and c["relation_type"] == "direction"]
        assert len(ab_dirs) == 1
        assert ab_dirs[0]["value"] == "north_of"  # existing wins


# ── Gap detection tests (unit-level, no DB/LLM) ──


class TestGapDetection:
    """SpatialCompletionAgent gap detection stages."""

    def _make_agent(self):
        return SpatialCompletionAgent("test-novel")

    def test_trajectory_gaps(self):
        """B1: Trajectory gaps detected for consecutive movements without existing relations."""
        agent = self._make_agent()

        loc_context = {
            "locations": {"花果山", "傲来国", "灵台山"},
            "loc_chapters": {
                "花果山": {1, 2, 3},
                "傲来国": {2, 3},
                "灵台山": {4, 5},
            },
            "loc_descriptions": {},
            "loc_cooccurrence": Counter({("傲来国", "花果山"): 2, ("傲来国", "灵台山"): 1}),
            "trajectories": {
                "孙悟空": [
                    {"location": "花果山", "chapter": 1},
                    {"location": "傲来国", "chapter": 2},
                    {"location": "灵台山", "chapter": 4},
                ]
            },
            "tiers": {"花果山": "city", "傲来国": "city", "灵台山": "city"},
            "parents": {},
        }

        gaps = agent._detect_gaps([], loc_context, {}, None)
        traj_gaps = [g for g in gaps if g["gap_type"] == "trajectory"]
        # Phase B detects ALL trajectory pairs (not just consecutive chapters)
        assert len(traj_gaps) >= 1
        pair_sets = [{g["source"], g["target"]} for g in traj_gaps]
        assert {"傲来国", "花果山"} in pair_sets

    def test_hierarchy_gaps(self):
        """B2: Siblings with co-occurrence ≥ 2 detected as gaps."""
        agent = self._make_agent()
        loc_context = {
            "locations": {"宁国府", "荣国府", "贾母院"},
            "loc_chapters": {
                "宁国府": {1, 2, 3, 4},
                "荣国府": {1, 2, 3, 4, 5},
                "贾母院": {2, 3},
            },
            "loc_descriptions": {},
            "loc_cooccurrence": Counter({("宁国府", "荣国府"): 4, ("宁国府", "贾母院"): 2}),
            "trajectories": {},
            "tiers": {"宁国府": "site", "荣国府": "site", "贾母院": "site"},
            "parents": {"宁国府": "贾府", "荣国府": "贾府", "贾母院": "荣国府"},
        }
        gaps = agent._detect_gaps([], loc_context, {}, None)
        hier_gaps = [g for g in gaps if g["gap_type"] == "hierarchy"]
        # 宁国府 and 荣国府 are siblings under 贾府 with co-occurrence=4
        assert len(hier_gaps) >= 1
        sibling_pair = {hier_gaps[0]["source"], hier_gaps[0]["target"]}
        assert sibling_pair == {"宁国府", "荣国府"}


class TestContradictionDetection:
    """Filter contradictory spatial relations."""

    def test_direction_contradiction(self):
        """A north B conflicts with B north A (should be B south A)."""
        agent = SpatialCompletionAgent("test")
        existing = {
            ("A", "B", "direction"): {
                "source": "A", "target": "B",
                "relation_type": "direction", "value": "north_of",
                "confidence": "high",
            }
        }
        new_relations = [
            {"source": "B", "target": "A", "relation_type": "direction",
             "value": "north_of", "confidence": "medium", "reason": "test"},
        ]
        filtered = agent._filter_relations(new_relations, existing)
        # B north A contradicts A north B (should be B south A)
        assert len(filtered) == 0

    def test_consistent_direction_passes(self):
        """Non-contradicting directions should pass."""
        agent = SpatialCompletionAgent("test")
        new_relations = [
            {"source": "A", "target": "B", "relation_type": "direction",
             "value": "east_of", "confidence": "high", "reason": "test"},
        ]
        filtered = agent._filter_relations(new_relations, {})
        assert len(filtered) == 1

    def test_low_confidence_filtered(self):
        """Low confidence relations should be filtered out."""
        agent = SpatialCompletionAgent("test")
        new_relations = [
            {"source": "A", "target": "B", "relation_type": "direction",
             "value": "east_of", "confidence": "low", "reason": "test"},
        ]
        filtered = agent._filter_relations(new_relations, {})
        assert len(filtered) == 0

    def test_unknown_value_filtered(self):
        """Unknown/无法推断 values should be filtered out."""
        agent = SpatialCompletionAgent("test")
        new_relations = [
            {"source": "A", "target": "B", "relation_type": "direction",
             "value": "unknown", "confidence": "medium", "reason": "test"},
            {"source": "C", "target": "D", "relation_type": "direction",
             "value": "无法推断", "confidence": "high", "reason": "test"},
        ]
        filtered = agent._filter_relations(new_relations, {})
        assert len(filtered) == 0


# ── _inject_cardinal_name_priors tests ──

from src.services.visualization_service import (  # noqa: E402
    _inject_cardinal_name_priors,
)


class TestCardinalNamePriors:
    """Soft direction constraints injected from cardinal location names."""

    def _make_locations(self, specs):
        # specs: list of (name, parent)
        return [{"name": n, "parent": p, "tier": "continent", "level": 0}
                for n, p in specs]

    def test_injects_for_cardinal_continent(self):
        """东胜神洲 under 天下 gets east_of 天下 as a soft name_prior."""
        locations = self._make_locations([("天下", None), ("东胜神洲", "天下")])
        result = _inject_cardinal_name_priors([], locations)
        assert len(result) == 1
        c = result[0]
        assert c["source"] == "东胜神洲"
        assert c["target"] == "天下"
        assert c["relation_type"] == "direction"
        assert c["value"] == "east_of"
        assert c["confidence"] == "low"
        assert c["source_type"] == "name_prior"

    def test_four_continents_spread_into_quadrants(self):
        """All four 部洲 siblings get their own cardinal direction."""
        locations = self._make_locations([
            ("天下", None),
            ("东胜神洲", "天下"), ("西牛贺洲", "天下"),
            ("南赡部洲", "天下"), ("北俱芦洲", "天下"),
        ])
        result = _inject_cardinal_name_priors([], locations)
        got = {(c["source"], c["value"]) for c in result}
        assert got == {
            ("东胜神洲", "east_of"), ("西牛贺洲", "west_of"),
            ("南赡部洲", "south_of"), ("北俱芦洲", "north_of"),
        }

    def test_sea_and_island_suffixes(self):
        """海/洋/岛/海湾 suffixes all qualify."""
        locations = self._make_locations([
            ("天下", None),
            ("南海", "天下"), ("东洋大海", "天下"),
            ("西海鸠鹚岛", "天下"), ("北冥海湾", "天下"),
        ])
        result = _inject_cardinal_name_priors([], locations)
        got = {(c["source"], c["value"]) for c in result}
        assert got == {
            ("南海", "south_of"), ("东洋大海", "east_of"),
            ("西海鸠鹚岛", "west_of"), ("北冥海湾", "north_of"),
        }

    def test_skips_existing_direction_pair(self):
        """Text-extracted constraint for the same pair wins; no injection."""
        locations = self._make_locations([("天下", None), ("北俱芦洲", "天下")])
        existing = [{
            "source": "北俱芦洲", "target": "天下",
            "relation_type": "direction", "value": "south_of",
            "confidence": "high",
        }]
        result = _inject_cardinal_name_priors(existing, locations)
        assert len(result) == 1  # unchanged

    def test_skips_existing_reverse_direction_pair(self):
        """A reverse-direction constraint on the pair also blocks injection."""
        locations = self._make_locations([("天下", None), ("东胜神洲", "天下")])
        existing = [{
            "source": "天下", "target": "东胜神洲",
            "relation_type": "direction", "value": "west_of",
            "confidence": "medium",
        }]
        result = _inject_cardinal_name_priors(existing, locations)
        assert len(result) == 1

    def test_ignores_non_cardinal_names(self):
        """Names that merely contain a direction char don't qualify."""
        locations = self._make_locations([
            ("天下", None),
            ("南海普陀山", "天下"),   # ends with 山, not a geo suffix
            ("北天门", "天下"),       # ends with 门
            ("西山居", "天下"),       # ends with 居
            ("傲来国", "天下"),       # no cardinal char
        ])
        result = _inject_cardinal_name_priors([], locations)
        assert result == []

    def test_skips_missing_or_unknown_parent(self):
        """No parent (or parent not in the location set) → no injection."""
        locations = self._make_locations([
            ("东胜神洲", None), ("西牛贺洲", "不存在的父级"),
        ])
        result = _inject_cardinal_name_priors([], locations)
        assert result == []

    def test_non_direction_constraint_does_not_block(self):
        """A contains constraint on the pair must not block the prior."""
        locations = self._make_locations([("天下", None), ("南赡部洲", "天下")])
        existing = [{
            "source": "天下", "target": "南赡部洲",
            "relation_type": "contains", "value": "",
            "confidence": "high",
        }]
        result = _inject_cardinal_name_priors(existing, locations)
        assert len(result) == 2
        assert result[1]["value"] == "south_of"
