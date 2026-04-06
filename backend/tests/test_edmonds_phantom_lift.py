"""Tests for EdmondsResolver._lift_phantom_parent_children (Phase 1b)."""
from collections import Counter

from src.services.geo_skills.edmonds_resolver import EdmondsResolver


class TestPhantomLift:
    def test_lifts_zero_mc_children_from_phantom_parent(self):
        """phantom=低mc父节点 with 10+ zero-evidence children → lift mc=0 to grandparent."""
        parents = {"phantom": "root"}
        # phantom has 11 children, all mc=0
        for i in range(11):
            parents[f"child_{i}"] = "phantom"

        freq = Counter({"phantom": 1, "root": 100})  # phantom is low-mc
        # children all mc=0

        new_parents, lifted = EdmondsResolver._lift_phantom_parent_children(
            parents, freq, uber_root="root"
        )
        # Should lift at least 2 to get from 11 to 9
        assert lifted >= 2
        # Lifted children should point to grandparent (root)
        lifted_children = [c for c, p in new_parents.items() if p == "root" and c.startswith("child_")]
        assert len(lifted_children) >= 2

    def test_preserves_children_with_evidence(self):
        """Children with mc>=1 should not be lifted."""
        parents = {"phantom": "root"}
        for i in range(15):
            parents[f"child_{i}"] = "phantom"
        freq = Counter({"phantom": 1, "root": 100})
        for i in range(10):
            freq[f"child_{i}"] = 1  # has evidence
        # child_10..14 are mc=0

        new_parents, lifted = EdmondsResolver._lift_phantom_parent_children(
            parents, freq, uber_root="root"
        )
        # 15→9 needs 6 lifts, but only 5 mc=0 children available
        assert lifted == 5
        # mc>=1 children should all still be under phantom
        for i in range(10):
            assert new_parents[f"child_{i}"] == "phantom"

    def test_no_lift_for_non_phantom(self):
        """Node with mc>2 is not a phantom, no lift applied."""
        parents = {"real_parent": "root"}
        for i in range(12):
            parents[f"child_{i}"] = "real_parent"
        freq = Counter({"real_parent": 10, "root": 100})  # mc=10, not phantom

        new_parents, lifted = EdmondsResolver._lift_phantom_parent_children(
            parents, freq, uber_root="root"
        )
        assert lifted == 0
        assert new_parents == parents

    def test_no_lift_for_small_child_count(self):
        """Phantom with <10 children is fine."""
        parents = {"phantom": "root"}
        for i in range(5):
            parents[f"child_{i}"] = "phantom"
        freq = Counter({"phantom": 1, "root": 100})

        new_parents, lifted = EdmondsResolver._lift_phantom_parent_children(
            parents, freq, uber_root="root"
        )
        assert lifted == 0

    def test_uber_root_never_lifted(self):
        """uber_root is allowed to have many children."""
        parents = {}
        for i in range(50):
            parents[f"loc_{i}"] = "root"
        freq = Counter({"root": 0})  # root might have mc=0

        new_parents, lifted = EdmondsResolver._lift_phantom_parent_children(
            parents, freq, uber_root="root"
        )
        assert lifted == 0
