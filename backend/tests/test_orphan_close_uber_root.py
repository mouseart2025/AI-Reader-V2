"""Regression guard: _inject_layer_roots must connect all orphans to uber_root.

The MWA formulation in paper §3.3 claims single-root acyclic trees. Post-Edmonds
consolidation historically left some nodes without a recorded parent, producing
multiple disjoint roots in location_parents (observed: 西游记 has 泾河 floating;
封神演义 has 属天界 and 朝歌或商朝 floating). The Phase 0 orphan-close pass
inside _inject_layer_roots ensures the paper claim holds.
"""

from types import SimpleNamespace

from src.services.geo_skills.orchestrator import GeoOrchestrator


def _make_ws(parents, tiers, layers=None):
    """Minimal WorldStructure-like stub for _inject_layer_roots."""
    return SimpleNamespace(
        location_parents=dict(parents),
        location_tiers=dict(tiers),
        location_layer_map={k: "overworld" for k in tiers},
        layers=layers or [],
    )


def _roots(ws):
    lp = ws.location_parents
    all_nodes = set(lp.keys()) | {p for p in lp.values() if p}
    return {n for n in all_nodes if n not in lp or not lp.get(n)}


def _reaches_root(ws, node, root):
    """True if node is (transitively) a descendant of root via parents chain."""
    lp = ws.location_parents
    seen = set()
    cur = node
    while cur in lp and cur not in seen:
        seen.add(cur)
        cur = lp[cur]
        if cur == root:
            return True
    return False


def test_orphan_in_tiers_reaches_uber_root():
    """Node present in tiers but missing from parents → must be reachable from uber_root.

    The exact path may go through an intermediate virtual layer root (e.g.,
    泾河 → overworld → 天下), which is still single-rooted.
    """
    ws = _make_ws(
        parents={"泾河湾头": "泾河", "泾河水府": "泾河", "长安城": "大唐国"},
        tiers={"天下": "world", "泾河": "river", "泾河湾头": "site",
               "泾河水府": "site", "大唐国": "kingdom", "长安城": "city"},
    )
    assert "泾河" in _roots(ws)  # before fix: floating

    GeoOrchestrator._inject_layer_roots(ws)

    assert _roots(ws) == {"天下"}, "single root guarantee"
    assert _reaches_root(ws, "泾河", "天下"), "泾河 reaches uber_root"


def test_orphan_only_in_parent_values_reaches_uber_root():
    """Node appearing only as a parent value (not in tiers) must also close."""
    ws = _make_ws(
        parents={"商都遗址": "朝歌或商朝", "北海": "属天界"},
        tiers={"天下": "world", "商都遗址": "site", "北海": "sea"},
    )
    roots_before = _roots(ws)
    assert "朝歌或商朝" in roots_before and "属天界" in roots_before

    GeoOrchestrator._inject_layer_roots(ws)

    assert _roots(ws) == {"天下"}, "single root guarantee"
    assert _reaches_root(ws, "朝歌或商朝", "天下")
    assert _reaches_root(ws, "属天界", "天下")


def test_single_root_case_unchanged():
    """Already well-formed tree must not be mutated into a wrong shape."""
    ws = _make_ws(
        parents={"大荒山": "天下", "青埂峰": "大荒山"},
        tiers={"天下": "world", "大荒山": "mountain", "青埂峰": "peak"},
    )
    GeoOrchestrator._inject_layer_roots(ws)
    assert ws.location_parents == {"大荒山": "天下", "青埂峰": "大荒山"}
    assert _roots(ws) == {"天下"}


def test_no_uber_root_detected_returns_early():
    """If no world-tier node exists, orphan close is skipped (safe no-op)."""
    ws = _make_ws(
        parents={"a": "b"},
        tiers={"a": "city", "b": "region"},  # no "world" tier
    )
    GeoOrchestrator._inject_layer_roots(ws)
    # Should not crash; parents unchanged or only fallback-closed
    assert "a" in ws.location_parents
