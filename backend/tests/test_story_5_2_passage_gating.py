"""Story 5.2 acceptance tests — passage-like nodes gated out of the hierarchy.

A passage-like node (road / corridor / stairs / intersection / transit form,
see ``is_passage_like``) is a *topology* edge in the spatial graph, not a
*containment* container. Story 5.2 enforces two gates:

- Parent-side (AC1): a passage-like node must never become a parent
  ("道路下辖学校" is forbidden). Enforced in vote_builder / world_structure_agent
  and re-gated at the EdmondsResolver input-edge set.
- Child-side against world root (AC3): a passage-like child whose only candidate
  parent is uber_root / world root must not be hung under 天下 — it stays an
  orphan/topology node. Enforced in EdmondsResolver + WorldStructureAgent.

AC2 (topology-only evidence → no parent edge) is already covered by the
classify_spatial_relation gate from Story 5.1.
"""
from collections import Counter

from src.services.geo_skills.edmonds_resolver import EdmondsResolver
from src.services.geo_skills.snapshot import HierarchySnapshot
from src.utils.location_names import is_passage_like


def _resolve(parent_votes, tiers, location_parents=None):
    snap = HierarchySnapshot(
        location_parents=location_parents or {},
        location_tiers=tiers,
        parent_votes=parent_votes,
        location_frequencies=Counter(),
        chapter_settings={},
        location_chapters={},
    )
    import asyncio

    result = asyncio.run(EdmondsResolver().execute(snap))
    return result.parent_overrides


# ── Predicate (drives both gates) ───────────────────────────────────────
def test_is_passage_like_lexicon():
    # Explicit passage forms
    assert is_passage_like("走廊")
    assert is_passage_like("甬道")
    assert is_passage_like("过道")
    assert is_passage_like("楼梯")
    assert is_passage_like("台阶")
    assert is_passage_like("长街")
    assert is_passage_like("长安街")      # suffix 街
    assert is_passage_like("山路")        # suffix 路
    assert is_passage_like("大道")        # suffix 道
    assert is_passage_like("交叉口")
    assert is_passage_like("三岔口")
    # Non-passage (must stay in hierarchy)
    assert not is_passage_like("学校")
    assert not is_passage_like("长安")
    assert not is_passage_like("天下")
    assert not is_passage_like("荣国府")
    assert not is_passage_like("大荒山")
    # Empty / single-char standalone nouns must not false-positive
    assert not is_passage_like("")
    assert not is_passage_like("道")
    assert not is_passage_like("街")


# ── AC1: passage-like node can never be a parent ───────────────────────
def test_ac1_passage_like_never_a_parent():
    """长街 (passage-like) must not appear as a parent value in the resolved tree."""
    tiers = {"天下": "world", "学校": "site", "长街": "street"}
    parents = _resolve(parent_votes={"学校": Counter({"长街": 5})}, tiers=tiers)
    assert "长街" not in set(parents.values()), (
        f"长街 must not be a parent, resolved={parents}"
    )
    # 学校 is still placed (under world root fallback), just not under 长街
    assert parents.get("学校") != "长街"


# ── AC3: passage-like child orphaned, not hung under world root ────────
def test_ac3_passage_like_child_orphaned_legacy_edge():
    """走廊 with a legacy 走廊→天下 edge must be orphaned (legacy dropped)."""
    tiers = {"天下": "world", "走廊": "site"}
    parents = _resolve(
        parent_votes={"走廊": Counter({"天下": 1})},
        tiers=tiers,
        location_parents={"走廊": "天下"},
    )
    assert "走廊" not in parents, (
        f"走廊 must be orphaned, got parent {parents.get('走廊')}"
    )


def test_ac3_passage_like_child_orphaned_explicit_world_root_vote():
    """走廊 whose only vote candidate is 天下 must not be attached to 天下."""
    tiers = {"天下": "world", "走廊": "site"}
    parents = _resolve(parent_votes={"走廊": Counter({"天下": 3})}, tiers=tiers)
    assert "走廊" not in parents, (
        f"走廊 must be orphaned even with an explicit 天下 vote, got {parents.get('走廊')}"
    )


# ── Positive control: passage-like child with a real parent is kept ────
def test_passage_like_child_keeps_real_parent():
    """长安街 with an explicit 长安→长安街 vote stays under 长安 (real parent)."""
    tiers = {"天下": "world", "长安": "city", "长安街": "site"}
    parents = _resolve(parent_votes={"长安街": Counter({"长安": 5})}, tiers=tiers)
    assert parents.get("长安街") == "长安", (
        f"长安街 should keep real parent 长安, got {parents.get('长安街')}"
    )
