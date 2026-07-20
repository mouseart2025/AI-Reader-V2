"""Regression tests for EdmondsResolver._break_cycles_fixpoint.

Bug found via cross-LLM replication (DeepSeek-extracted 西游记): the old
single-pass cycle repair replaced the weakest edge with Edmonds' choice, but
when Edmonds' choice was the other edge of the same cycle (a name-containment
edge with no vote weight), the replacement was a no-op and the cycle
(黑水河 ↔ 黑水河水府) survived into the final output.
"""
from collections import Counter

from src.services.geo_skills.edmonds_resolver import EdmondsResolver


def _count_cycles(parents: dict[str, str]) -> int:
    cycles = 0
    seen_in_cycle: set[str] = set()
    for start in parents:
        if start in seen_in_cycle:
            continue
        visited: set[str] = set()
        node = start
        while node and node in parents and node not in visited:
            visited.add(node)
            node = parents.get(node)
        if node and node in visited:
            cycles += 1
            cur = node
            cycle_path: set[str] = set()
            while cur not in cycle_path:
                cycle_path.add(cur)
                cur = parents.get(cur, "")
            seen_in_cycle.update(cycle_path)
    return cycles


class TestBreakCyclesFixpoint:
    def test_noop_when_acyclic(self):
        parents = {"a": "root", "b": "a", "c": "b"}
        out, broken = EdmondsResolver._break_cycles_fixpoint(
            parents, {}, {}, uber_root="root"
        )
        assert broken == 0
        assert out == parents

    def test_breaks_2cycle_when_edmonds_points_into_cycle(self):
        """The exact failure mode: Edmonds' choice is the other cycle edge."""
        # 黑水河 -> 黑水河水府 (vote w=3), 黑水河水府 -> 黑水河 (no votes,
        # name-containment edge). Edmonds agrees with the containment edge.
        parents = {"黑水河": "黑水河水府", "黑水河水府": "黑水河"}
        votes = {"黑水河": Counter({"黑水河水府": 3})}
        edmonds_parents = {"黑水河": "天下", "黑水河水府": "黑水河"}

        out, broken = EdmondsResolver._break_cycles_fixpoint(
            parents, votes, edmonds_parents, uber_root="天下"
        )
        assert _count_cycles(out) == 0
        assert broken >= 1
        # Weakest edge (黑水河水府 -> 黑水河, weight 0) must be rewired to
        # uber_root since Edmonds' choice 黑水河 is inside the cycle.
        assert out["黑水河水府"] == "天下"
        # The vote-backed edge is preserved.
        assert out["黑水河"] == "黑水河水府"

    def test_uses_edmonds_choice_when_it_exits_cycle(self):
        parents = {"a": "b", "b": "a"}
        votes = {"a": Counter({"b": 5})}
        edmonds_parents = {"b": "root"}  # b's Edmonds parent is outside

        out, broken = EdmondsResolver._break_cycles_fixpoint(
            parents, votes, edmonds_parents, uber_root="root"
        )
        assert _count_cycles(out) == 0
        # weakest is b -> a (weight 0); Edmonds says b -> root, accepted.
        assert out["b"] == "root"
        assert out["a"] == "b"

    def test_breaks_longer_cycle_and_feeders_dont_recount(self):
        # 3-cycle a->b->c->a plus a feeder f->a
        parents = {"f": "a", "a": "b", "b": "c", "c": "a"}
        votes = {"a": Counter({"b": 2}), "b": Counter({"c": 1})}
        out, broken = EdmondsResolver._break_cycles_fixpoint(
            parents, votes, {}, uber_root="root"
        )
        assert _count_cycles(out) == 0
        assert broken == 1  # one underlying cycle, broken exactly once
        # weakest edge c -> a (weight 0) rewired to uber_root
        assert out["c"] == "root"
        assert out["f"] == "a"

    def test_self_loop_deleted_as_last_resort(self):
        # uber_root can't help if the cycle contains it
        parents = {"root": "root"}
        out, broken = EdmondsResolver._break_cycles_fixpoint(
            parents, {}, {}, uber_root="root"
        )
        assert _count_cycles(out) == 0
        assert "root" not in out
