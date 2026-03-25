"""Tests for topology quality metrics — parent precision/recall, chain accuracy, root count, orphan rate."""

import json
from pathlib import Path

import pytest

from src.utils.topology_metrics import compute_topology_metrics

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ── Helper: load golden standard ─────────────────────────────────


def _load_golden(name: str) -> list[dict]:
    path = FIXTURES_DIR / name
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["locations"]


# ── Golden standard data format tests ────────────────────────────


class TestGoldenStandardFormat:
    """Validate golden standard JSON files are well-formed."""

    @pytest.mark.parametrize("filename", [
        "golden_standard_journey_to_west.json",
        "golden_standard_dream_of_red_chamber.json",
    ])
    def test_file_loadable(self, filename):
        locs = _load_golden(filename)
        assert len(locs) >= 50, f"Expected ≥50 locations, got {len(locs)}"

    @pytest.mark.parametrize("filename", [
        "golden_standard_journey_to_west.json",
        "golden_standard_dream_of_red_chamber.json",
    ])
    def test_required_fields(self, filename):
        for loc in _load_golden(filename):
            assert "name" in loc, f"Missing 'name' in {loc}"
            assert "correct_parent" in loc, f"Missing 'correct_parent' in {loc}"
            assert "tier" in loc, f"Missing 'tier' in {loc}"

    @pytest.mark.parametrize("filename", [
        "golden_standard_journey_to_west.json",
        "golden_standard_dream_of_red_chamber.json",
    ])
    def test_no_excessive_roots(self, filename):
        locs = _load_golden(filename)
        roots = [l for l in locs if l["correct_parent"] is None]
        assert len(roots) <= 10, f"Too many roots ({len(roots)}), expected ≤10"

    @pytest.mark.parametrize("filename", [
        "golden_standard_journey_to_west.json",
        "golden_standard_dream_of_red_chamber.json",
    ])
    def test_no_cycles(self, filename):
        locs = _load_golden(filename)
        parents = {l["name"]: l["correct_parent"] for l in locs if l.get("correct_parent")}
        for start in parents:
            visited = set()
            node = start
            while node in parents:
                if node in visited:
                    pytest.fail(f"Cycle detected involving {node}")
                visited.add(node)
                node = parents[node]

    @pytest.mark.parametrize("filename", [
        "golden_standard_journey_to_west.json",
        "golden_standard_dream_of_red_chamber.json",
    ])
    def test_tier_coverage(self, filename):
        """At least 4 different tiers should be represented."""
        locs = _load_golden(filename)
        tiers = {l["tier"] for l in locs if l.get("tier")}
        assert len(tiers) >= 4, f"Expected ≥4 tier levels, got {tiers}"


# ── Metric computation tests ─────────────────────────────────────


class TestComputeTopologyMetrics:
    """Test compute_topology_metrics with synthetic data."""

    def test_perfect_match(self):
        """Golden compared to itself should yield perfect scores."""
        golden = [
            {"name": "A", "correct_parent": None, "tier": "continent"},
            {"name": "B", "correct_parent": "A", "tier": "kingdom"},
            {"name": "C", "correct_parent": "B", "tier": "city"},
        ]
        predicted = {"B": "A", "C": "B"}
        result = compute_topology_metrics(predicted, golden)
        assert result["parent_precision"] == 1.0
        assert result["parent_recall"] == 1.0
        assert result["chain_accuracy"] == 1.0
        assert result["orphan_rate"] == 0.0

    def test_completely_wrong(self):
        """All parents wrong → precision=0, recall=0."""
        golden = [
            {"name": "A", "correct_parent": None, "tier": "continent"},
            {"name": "B", "correct_parent": "A", "tier": "kingdom"},
            {"name": "C", "correct_parent": "B", "tier": "city"},
        ]
        predicted = {"B": "C", "C": "A"}  # Both wrong
        result = compute_topology_metrics(predicted, golden)
        assert result["parent_precision"] == 0.0
        assert result["parent_recall"] == 0.0

    def test_partial_match(self):
        """One correct, one wrong → precision=0.5, recall=0.5."""
        golden = [
            {"name": "A", "correct_parent": None, "tier": "continent"},
            {"name": "B", "correct_parent": "A", "tier": "kingdom"},
            {"name": "C", "correct_parent": "B", "tier": "city"},
        ]
        predicted = {"B": "A", "C": "A"}  # B→A correct, C→A wrong (should be C→B)
        result = compute_topology_metrics(predicted, golden)
        assert result["parent_precision"] == 0.5
        assert result["parent_recall"] == 0.5

    def test_empty_predicted(self):
        """No predictions → precision=0, recall=0, all orphans."""
        golden = [
            {"name": "A", "correct_parent": None, "tier": "continent"},
            {"name": "B", "correct_parent": "A", "tier": "kingdom"},
        ]
        predicted = {}
        result = compute_topology_metrics(predicted, golden)
        assert result["parent_precision"] == 0.0
        assert result["parent_recall"] == 0.0
        assert result["root_count"] == 0
        assert result["orphan_rate"] > 0

    def test_empty_golden(self):
        """Empty golden standard → all zeros, no division errors."""
        predicted = {"B": "A"}
        result = compute_topology_metrics(predicted, [])
        assert result["parent_precision"] == 0.0
        assert result["parent_recall"] == 0.0
        assert result["orphan_rate"] == 0.0

    def test_root_count(self):
        """Count locations with no parent in predicted."""
        predicted = {"B": "A", "C": "A", "D": "B"}
        golden = [
            {"name": "A", "correct_parent": None, "tier": "continent"},
            {"name": "B", "correct_parent": "A", "tier": "kingdom"},
            {"name": "C", "correct_parent": "A", "tier": "kingdom"},
            {"name": "D", "correct_parent": "B", "tier": "city"},
        ]
        result = compute_topology_metrics(predicted, golden)
        assert result["root_count"] == 1  # Only A is root

    def test_orphan_rate(self):
        """Locations in golden without predicted parent are orphans."""
        golden = [
            {"name": "A", "correct_parent": None, "tier": "continent"},
            {"name": "B", "correct_parent": "A", "tier": "kingdom"},
            {"name": "C", "correct_parent": "A", "tier": "kingdom"},
            {"name": "D", "correct_parent": "B", "tier": "city"},
        ]
        predicted = {"B": "A"}  # C and D missing
        result = compute_topology_metrics(predicted, golden)
        # 4 total locations, 1 root (A), 3 non-roots (B,C,D), 2 orphans (C,D)
        assert result["orphan_rate"] == 0.5  # 2/4

    def test_chain_accuracy_full_chain(self):
        """Chain A→B→C→D: all correct → chain_accuracy=1.0."""
        golden = [
            {"name": "A", "correct_parent": None, "tier": "continent"},
            {"name": "B", "correct_parent": "A", "tier": "kingdom"},
            {"name": "C", "correct_parent": "B", "tier": "region"},
            {"name": "D", "correct_parent": "C", "tier": "city"},
        ]
        predicted = {"B": "A", "C": "B", "D": "C"}
        result = compute_topology_metrics(predicted, golden)
        assert result["chain_accuracy"] == 1.0

    def test_chain_accuracy_broken_middle(self):
        """Chain A→B→C→D: B→A wrong → prefix from D is 0/3, from C is 0/2, etc."""
        golden = [
            {"name": "A", "correct_parent": None, "tier": "continent"},
            {"name": "B", "correct_parent": "A", "tier": "kingdom"},
            {"name": "C", "correct_parent": "B", "tier": "region"},
            {"name": "D", "correct_parent": "C", "tier": "city"},
        ]
        predicted = {"B": "X", "C": "B", "D": "C"}
        result = compute_topology_metrics(predicted, golden)
        # D→C correct, C→B correct, B→A wrong. Prefix from D: D→C✓, C→B✓, B→A✗ = 2/3
        assert result["chain_accuracy"] > 0.5

    def test_extra_predicted_locations_ignored(self):
        """Locations in predicted but not in golden don't affect precision denominator."""
        golden = [
            {"name": "A", "correct_parent": None, "tier": "continent"},
            {"name": "B", "correct_parent": "A", "tier": "kingdom"},
        ]
        predicted = {"B": "A", "X": "Y", "Z": "W"}  # X,Z not in golden
        result = compute_topology_metrics(predicted, golden)
        assert result["parent_precision"] == 1.0  # B→A is the only one counted
        assert result["parent_recall"] == 1.0


# ── Integration with real golden standard files ──────────────────


class TestGoldenStandardIntegration:
    """Run metrics against golden standard datasets to verify they're well-formed."""

    @pytest.mark.parametrize("filename", [
        "golden_standard_journey_to_west.json",
        "golden_standard_dream_of_red_chamber.json",
    ])
    def test_self_comparison_perfect(self, filename):
        """Golden standard compared to itself must yield perfect scores."""
        locs = _load_golden(filename)
        predicted = {
            l["name"]: l["correct_parent"]
            for l in locs
            if l.get("correct_parent")
        }
        result = compute_topology_metrics(predicted, locs)
        assert result["parent_precision"] == 1.0
        assert result["parent_recall"] == 1.0
        assert result["chain_accuracy"] == 1.0
        assert result["orphan_rate"] == 0.0
