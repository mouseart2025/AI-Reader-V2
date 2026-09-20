"""Tests for spatial quality metrics — per-level golden report, structural
constraints, and revisit consistency. All in-memory, no DB required."""

from src.utils.spatial_quality import (
    check_spatial_constraints,
    compute_per_level_metrics,
    compute_revisit_consistency,
)

# ── 1. Per-level metrics ────────────────────────────────────────────


def _golden() -> list[dict]:
    return [
        {"name": "天下", "correct_parent": None, "tier": "world"},
        {"name": "东胜神洲", "correct_parent": "天下", "tier": "continent"},
        {"name": "傲来国", "correct_parent": "东胜神洲", "tier": "kingdom"},
        {"name": "花果山", "correct_parent": "傲来国", "tier": "region"},
        {"name": "水帘洞", "correct_parent": "花果山", "tier": "site"},
        {"name": "旧地名", "correct_parent": "某处", "tier": "DELETE"},
    ]


class TestPerLevelMetrics:
    def test_grouping_and_precision(self):
        predicted = {
            "东胜神洲": "天下",      # correct
            "傲来国": "花果山",      # wrong
            "水帘洞": "花果山",      # correct
            # 花果山 not predicted → region tier has support 0
        }
        result = compute_per_level_metrics(predicted, _golden())
        levels = result["levels"]

        assert levels["continent"] == {
            "parent_precision": 1.0, "support": 1, "correct": 1}
        assert levels["kingdom"] == {
            "parent_precision": 0.0, "support": 1, "correct": 0}
        assert levels["region"] == {
            "parent_precision": None, "support": 0, "correct": 0}
        assert levels["site"] == {
            "parent_precision": 1.0, "support": 1, "correct": 1}

    def test_macro_precision_excludes_zero_support_tiers(self):
        predicted = {"东胜神洲": "天下", "傲来国": "花果山", "水帘洞": "花果山"}
        result = compute_per_level_metrics(predicted, _golden())
        # mean of continent 1.0, kingdom 0.0, site 1.0 (region excluded)
        assert result["macro_precision"] == round((1.0 + 0.0 + 1.0) / 3, 4)

    def test_delete_tier_and_golden_roots_skipped(self):
        predicted = {"旧地名": "某处", "天下": "某处"}
        result = compute_per_level_metrics(predicted, _golden())
        assert "DELETE" not in result["levels"]
        # 天下 is a golden root (no correct_parent) → never counted
        assert all(
            "天下" not in str(level) for level in result["levels"].values())

    def test_empty_golden(self):
        result = compute_per_level_metrics({"a": "b"}, [])
        assert result == {"levels": {}, "macro_precision": None}


# ── 2. Structural constraints ───────────────────────────────────────


def _codes(violations: list[dict]) -> list[str]:
    return [v["code"] for v in violations]


class TestCycle:
    def test_cycle_reported_with_path(self):
        violations = check_spatial_constraints({"a": "b", "b": "c", "c": "a"})
        cycles = [v for v in violations if v["code"] == "CYCLE"]
        assert len(cycles) == 1
        assert cycles[0]["severity"] == "error"
        # cycle path starts and ends on the same node, covering all three
        assert len(cycles[0]["nodes"]) == 4
        assert cycles[0]["nodes"][0] == cycles[0]["nodes"][-1]
        assert set(cycles[0]["nodes"]) == {"a", "b", "c"}

    def test_self_loop_is_cycle(self):
        violations = check_spatial_constraints({"a": "a"})
        assert "CYCLE" in _codes(violations)

    def test_acyclic_chain_clean(self):
        assert check_spatial_constraints({"a": "b", "b": "c"}) == []


class TestTierInversion:
    def test_inversion_is_error(self):
        # 东胜神洲 (rank 1, continent) under 花果山 (rank 3, region)
        violations = check_spatial_constraints({"东胜神洲": "花果山"})
        inv = [v for v in violations if v["code"] == "TIER_INVERSION"]
        assert len(inv) == 1
        assert inv[0]["severity"] == "error"
        assert inv[0]["nodes"] == ["东胜神洲", "花果山"]

    def test_normal_order_clean(self):
        # 花果山 (rank 3) under 东胜神洲 (rank 1): diff 2, no skip either
        assert check_spatial_constraints({"花果山": "东胜神洲"}) == []


class TestScaleSkip:
    def test_skip_is_warning(self):
        # 怡红院 (rank 6, building) directly under 东胜神洲 (rank 1, continent)
        violations = check_spatial_constraints({"怡红院": "东胜神洲"})
        skips = [v for v in violations if v["code"] == "SCALE_SKIP"]
        assert len(skips) == 1
        assert skips[0]["severity"] == "warning"

    def test_within_two_levels_clean(self):
        # 水帘洞 (rank 5) under 梁山县 (rank 3): diff exactly 2
        assert check_spatial_constraints({"水帘洞": "梁山县"}) == []

    def test_unranked_name_skipped(self):
        # 无名地 has no recognizable suffix → no rank check at all
        assert check_spatial_constraints({"无名之地": "东胜神洲"}) == []


class TestNoiseRoot:
    def test_noise_root_is_warning(self):
        violations = check_spatial_constraints(
            {"水帘洞": "花果山"}, location_tiers={"花果山": "site"})
        roots = [v for v in violations if v["code"] == "NOISE_ROOT"]
        assert len(roots) == 1
        assert roots[0]["severity"] == "warning"
        assert roots[0]["nodes"] == ["花果山"]

    def test_ok_root_tiers_clean(self):
        for tier in ("world", "continent", "region"):
            assert check_spatial_constraints(
                {"水帘洞": "花果山"},
                location_tiers={"花果山": tier}) == []

    def test_virtual_root_exempt(self):
        violations = check_spatial_constraints(
            {"水帘洞": "花果山"},
            location_tiers={"花果山": "site"},
            virtual_roots={"花果山"})
        assert "NOISE_ROOT" not in _codes(violations)


class TestDirectionConflictConstraint:
    def test_conflict_is_warning(self):
        facts = [
            {"source": "花果山", "target": "傲来国",
             "relation_type": "direction", "value": "west_of"},
            {"source": "花果山", "target": "傲来国",
             "relation_type": "direction", "value": "east_of"},
        ]
        violations = check_spatial_constraints({}, spatial_facts=facts)
        conflicts = [v for v in violations
                     if v["code"] == "DIRECTION_CONFLICT"]
        assert len(conflicts) == 1
        assert conflicts[0]["severity"] == "warning"
        assert conflicts[0]["nodes"] == ["傲来国", "花果山"]

    def test_consistent_directions_clean(self):
        # A west_of B and B east_of A say the same thing → no conflict
        facts = [
            {"source": "花果山", "target": "傲来国",
             "relation_type": "direction", "value": "west_of"},
            {"source": "傲来国", "target": "花果山",
             "relation_type": "direction", "value": "east_of"},
        ]
        assert check_spatial_constraints({}, spatial_facts=facts) == []

    def test_disabled_without_spatial_facts(self):
        assert check_spatial_constraints({}) == []


class TestViolationOrdering:
    def test_errors_before_warnings_then_code(self):
        violations = check_spatial_constraints(
            {"a": "b", "b": "a", "水帘洞": "花果山"},
            location_tiers={"花果山": "site"})
        assert _codes(violations) == ["CYCLE", "NOISE_ROOT"]
        assert violations[0]["severity"] == "error"
        assert violations[1]["severity"] == "warning"


# ── 3. Revisit consistency ──────────────────────────────────────────


def _fact(chapter: int, relations: list[dict]) -> dict:
    return {"chapter_id": chapter, "spatial_relationships": relations}


class TestRevisitParentConflicts:
    def test_distinct_parents_across_chapters(self):
        facts = [
            _fact(1, [{"source": "傲来国", "target": "花果山",
                       "relation_type": "contains"}]),
            _fact(5, [{"source": "花果山", "target": "东胜神洲",
                       "relation_type": "located_in"}]),
        ]
        result = compute_revisit_consistency(facts)
        assert result["parent_conflicts"] == 1
        assert result["children_multi_asserted"] == 1
        assert result["parent_consistency"] == 0.0

        case = result["cases"][0]
        assert case["type"] == "parent_conflict"
        assert case["child"] == "花果山"
        assert case["assertions"] == [
            {"chapter": 1, "parent": "傲来国"},
            {"chapter": 5, "parent": "东胜神洲"},
        ]

    def test_same_parent_twice_is_consistent(self):
        facts = [
            _fact(1, [{"source": "傲来国", "target": "花果山",
                       "relation_type": "contains"}]),
            _fact(3, [{"source": "傲来国", "target": "花果山",
                       "relation_type": "contains"}]),
        ]
        result = compute_revisit_consistency(facts)
        assert result["children_multi_asserted"] == 1
        assert result["parent_conflicts"] == 0
        assert result["parent_consistency"] == 1.0
        assert result["cases"] == []

    def test_single_assertion_gives_perfect_consistency(self):
        facts = [_fact(1, [{"source": "傲来国", "target": "花果山",
                            "relation_type": "contains"}])]
        result = compute_revisit_consistency(facts)
        assert result["children_multi_asserted"] == 0
        assert result["parent_consistency"] == 1.0

    def test_empty_facts(self):
        result = compute_revisit_consistency([])
        assert result["parent_consistency"] == 1.0
        assert result["cases"] == []


class TestRevisitDirectionConflicts:
    def test_opposite_assertions_across_chapters(self):
        # B north_of A ≡ A south_of B, contradicting A north_of B
        facts = [
            _fact(2, [{"source": "花果山", "target": "傲来国",
                       "relation_type": "direction", "value": "north_of"}]),
            _fact(9, [{"source": "傲来国", "target": "花果山",
                       "relation_type": "direction", "value": "north_of"}]),
        ]
        result = compute_revisit_consistency(facts)
        assert result["direction_conflicts"] == 1

        case = result["cases"][0]
        assert case["type"] == "direction_conflict"
        assert case["pair"] == ["傲来国", "花果山"]
        assert len(case["assertions"]) == 2
        assert {a["chapter"] for a in case["assertions"]} == {2, 9}

    def test_same_direction_repeated_clean(self):
        facts = [
            _fact(2, [{"source": "花果山", "target": "傲来国",
                       "relation_type": "direction", "value": "west_of"}]),
            _fact(9, [{"source": "花果山", "target": "傲来国",
                       "relation_type": "direction", "value": "west_of"}]),
        ]
        result = compute_revisit_consistency(facts)
        assert result["direction_conflicts"] == 0
        assert result["cases"] == []
