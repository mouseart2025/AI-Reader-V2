"""Spatial quality metrics for the self-evolution evaluation loop.

Pure-computation metrics (no LLM calls, no DB access):

- compute_per_level_metrics: golden-standard parent precision grouped by tier
  (macro-averaged so large tiers do not drown out small ones).
- check_spatial_constraints: executable structural constraints over a location
  hierarchy (cycles, tier inversions, scale skips, noise roots, direction
  conflicts), each violation reported as a structured dict.
- compute_revisit_consistency: closed-loop revisit protocol — the same place
  mentioned across chapters should yield consistent extraction results.
"""

from __future__ import annotations

from collections import defaultdict

from src.models.chapter_fact import (
    HIERARCHY_SPATIAL_RELATIONS,
    normalize_spatial_relation_type,
)

# Opposite direction pairs (same semantics as conflict_detector /
# map_layout_service): "A north_of B" contradicts "A south_of B" and
# "B north_of A".
OPPOSITE_DIRECTIONS: dict[str, str] = {
    "north_of": "south_of", "south_of": "north_of",
    "east_of": "west_of", "west_of": "east_of",
    "northeast_of": "southwest_of", "southwest_of": "northeast_of",
    "northwest_of": "southeast_of", "southeast_of": "northwest_of",
}

# Tiers allowed to act as a real root (mirrors topology_metrics health check).
_OK_ROOT_TIERS = {"world", "continent", "region"}

_SEVERITY_ORDER = {"error": 0, "warning": 1}


# ── 1. Per-level golden metrics ─────────────────────────────────────


def compute_per_level_metrics(
    predicted: dict[str, str],
    golden_locations: list[dict],
) -> dict:
    """Golden parent precision grouped by the golden ``tier`` field.

    Args:
        predicted: System-produced {child: parent} mapping.
        golden_locations: List of dicts with keys: name, correct_parent, tier.
            Entries with tier "DELETE" are skipped (same convention as
            compute_topology_metrics); golden roots (null parent) carry no
            parent assignment and are skipped too.

    Returns:
        {"levels": {tier: {"parent_precision", "support", "correct"}},
         "macro_precision": equal-weight mean over tiers with support > 0}.
        Precision values are None when a tier has no predicted parents.
    """
    levels: dict[str, dict[str, int]] = {}
    for loc in golden_locations:
        name = loc.get("name", "")
        parent = loc.get("correct_parent")
        tier = loc.get("tier") or "unknown"
        if not name or tier == "DELETE" or not parent:
            continue
        bucket = levels.setdefault(tier, {"support": 0, "correct": 0})
        if name in predicted:
            bucket["support"] += 1
            if predicted[name] == parent:
                bucket["correct"] += 1

    out_levels: dict[str, dict] = {}
    precisions: list[float] = []
    for tier in sorted(levels):
        bucket = levels[tier]
        support, correct = bucket["support"], bucket["correct"]
        precision = round(correct / support, 4) if support > 0 else None
        out_levels[tier] = {
            "parent_precision": precision,
            "support": support,
            "correct": correct,
        }
        if precision is not None:
            precisions.append(precision)

    macro = round(sum(precisions) / len(precisions), 4) if precisions else None
    return {"levels": out_levels, "macro_precision": macro}


# ── 1b. Virtual-aware topology metrics ──────────────────────────────


def resolve_virtual_parents(
    predicted: dict[str, str],
    virtual_nodes: set[str] | frozenset[str] | None,
) -> dict[str, str]:
    """沿父链穿透虚拟节点(与 WorldStructure.semantic_parent 同语义)。

    图层分组根(主世界/天界…)与工程 uber_root 是渲染脚手架,不是知识
    声明:京畿→主世界(virtual)→天下 在语义上即 京畿→天下。
    返回穿透后的 {child: parent} 新表;virtual 为空时逐边原样返回。
    """
    if not virtual_nodes:
        return dict(predicted)
    resolved: dict[str, str] = {}
    for child, parent in predicted.items():
        node = parent
        seen = {child}
        while node in virtual_nodes and node in predicted and node not in seen:
            seen.add(node)
            node = predicted[node]
        resolved[child] = node
    return resolved


def compute_topology_metrics_virtual_aware(
    predicted: dict[str, str],
    golden_locations: list[dict],
    virtual_nodes: set[str] | frozenset[str] | None,
) -> dict:
    """virtual 穿透后的 topology 指标(applied 口径的诚实测量)。

    评分前先把 predicted 中指向虚拟渲染脚手架的边穿透到其语义 parent,
    再委托 frozen 的 compute_topology_metrics——PP 与 chain 都按
    穿透后的边计算。virtual_nodes 为空时与原函数逐边一致。
    """
    from src.utils.topology_metrics import compute_topology_metrics

    return compute_topology_metrics(
        resolve_virtual_parents(predicted, virtual_nodes), golden_locations,
    )


# ── 2. Executable structural constraints ────────────────────────────


def check_spatial_constraints(
    location_parents: dict[str, str],
    location_tiers: dict[str, str] | None = None,
    virtual_roots: set[str] | None = None,
    spatial_facts: list[dict] | None = None,
    scale_skip_macro_exempt: bool = False,
) -> list[dict]:
    """Check structural constraints over a location hierarchy.

    Returns a list of violations, each {"code", "severity", "message",
    "nodes"}, sorted with errors first, then by code. Codes:

    - CYCLE (error): a parent chain loops back on itself; nodes = cycle path.
    - TIER_INVERSION (error): child suffix rank < parent suffix rank
      (child is a larger geographic entity than its parent).
    - SCALE_SKIP (warning): child rank - parent rank > 2 (e.g. a building
      directly under a continent). Skipped when either side has no rank.
      With ``scale_skip_macro_exempt=True``, only reported when the parent
      is world/continent-scale (rank ≤ 1): a site/building hanging directly
      off a macro root (怡红院→东胜神洲) is a true skip, while direct
      containment under kingdom/region containers (五台山僧堂→五台山,
      酒店→阳谷县) is the historical norm, not a violation. Default False
      preserves the original behavior for existing callers.
    - NOISE_ROOT (warning): a root whose tier is not world/continent/region
      and which is not in ``virtual_roots`` (engineering containers such as
      a novel's uber-root are exempt; real 天下-type roots are not).
    - DIRECTION_CONFLICT (warning): only when ``spatial_facts`` is given —
      the same (A, B) pair carries contradictory direction assertions.
    """
    violations: list[dict] = []
    violations.extend(_check_cycles(location_parents))
    violations.extend(_check_ranks(
        location_parents, scale_skip_macro_exempt=scale_skip_macro_exempt))
    violations.extend(_check_noise_roots(
        location_parents, location_tiers, virtual_roots))
    if spatial_facts:
        for conflict in find_direction_conflicts(spatial_facts):
            pair = conflict["pair"]
            detail = "; ".join(
                f"{a['source']} {a['direction']} {a['target']}"
                for a in conflict["assertions"]
            )
            violations.append({
                "code": "DIRECTION_CONFLICT",
                "severity": "warning",
                "message": f"contradictory direction assertions on "
                           f"({pair[0]}, {pair[1]}): {detail}",
                "nodes": pair,
            })
    violations.sort(key=lambda v: (
        _SEVERITY_ORDER.get(v["severity"], 2), v["code"]))
    return violations


def _check_cycles(location_parents: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    seen_cycles: set[frozenset[str]] = set()
    for start in location_parents:
        path: list[str] = []
        pos: dict[str, int] = {}
        node = start
        while node in location_parents and node not in pos:
            pos[node] = len(path)
            path.append(node)
            node = location_parents[node]
        if node not in pos:
            continue
        cycle = [*path[pos[node]:], node]
        key = frozenset(cycle)
        if key in seen_cycles:
            continue
        seen_cycles.add(key)
        out.append({
            "code": "CYCLE",
            "severity": "error",
            "message": f"parent chain forms a cycle: {' -> '.join(cycle)}",
            "nodes": cycle,
        })
    return out


def _check_ranks(location_parents: dict[str, str],
                 scale_skip_macro_exempt: bool = False) -> list[dict]:
    try:
        from src.services.world_structure_agent import _get_suffix_rank
    except ImportError:
        return []
    out: list[dict] = []
    for child, parent in sorted(location_parents.items()):
        cr = _get_suffix_rank(child)
        pr = _get_suffix_rank(parent)
        if cr is None or pr is None:
            continue
        if cr < pr:
            out.append({
                "code": "TIER_INVERSION",
                "severity": "error",
                "message": f"{child!r} (rank {cr}) is a larger entity than "
                           f"its parent {parent!r} (rank {pr})",
                "nodes": [child, parent],
            })
        elif cr - pr > 2:
            # 宏观容器豁免(可选):parent 为 kingdom(2)/region(3) 及以下时,
            # site/building 直接挂载是常态(僧堂→五台山、酒店→阳谷县),
            # 只有挂到 world(0)/continent(1) 级宏观根才算真跨级。
            if scale_skip_macro_exempt and pr >= 2:
                continue
            out.append({
                "code": "SCALE_SKIP",
                "severity": "warning",
                "message": f"{child!r} (rank {cr}) skips scale levels under "
                           f"parent {parent!r} (rank {pr})",
                "nodes": [child, parent],
            })
    return out


def _check_noise_roots(
    location_parents: dict[str, str],
    location_tiers: dict[str, str] | None,
    virtual_roots: set[str] | None,
) -> list[dict]:
    children = set(location_parents.keys())
    roots = (children | set(location_parents.values())) - children
    exempt = set(virtual_roots or ())
    out: list[dict] = []
    for root in sorted(roots):
        if root in exempt:
            continue
        tier = (location_tiers or {}).get(root, "")
        if tier and tier not in _OK_ROOT_TIERS:
            out.append({
                "code": "NOISE_ROOT",
                "severity": "warning",
                "message": f"root {root!r} has tier {tier!r}, expected one of "
                           f"{sorted(_OK_ROOT_TIERS)} (and not a virtual root)",
                "nodes": [root],
            })
    return out


# ── Shared direction-conflict detection ─────────────────────────────


def _direction_value(rel: dict) -> str | None:
    """Canonical direction token of a spatial-relation dict, or None."""
    raw_type = str(rel.get("relation_type", "") or "")
    if normalize_spatial_relation_type(raw_type) != "direction":
        return None
    value = str(rel.get("value", "") or "").strip()
    if value in OPPOSITE_DIRECTIONS:
        return value
    # Legacy form: direction carried in relation_type itself ("north_of").
    return raw_type if raw_type in OPPOSITE_DIRECTIONS else None


def find_direction_conflicts(spatial_facts: list[dict]) -> list[dict]:
    """Contradictory direction assertions per unordered location pair.

    Each fact is a dict with source/target/relation_type/value and an
    optional chapter/chapter_id. Returns one record per conflicting pair:
    {"pair": [a, b], "assertions": [{"chapter", "source", "target",
    "direction"}, ...]}.
    """
    assertions: dict[tuple[str, str], list[dict]] = defaultdict(list)
    canon_dirs: dict[tuple[str, str], set[str]] = defaultdict(set)
    for fact in spatial_facts:
        direction = _direction_value(fact)
        if direction is None:
            continue
        a = str(fact.get("source", "") or "")
        b = str(fact.get("target", "") or "")
        if not a or not b or a == b:
            continue
        pair = (a, b) if a <= b else (b, a)
        canon = direction if a <= b else OPPOSITE_DIRECTIONS[direction]
        canon_dirs[pair].add(canon)
        assertions[pair].append({
            "chapter": fact.get("chapter", fact.get("chapter_id")),
            "source": a,
            "target": b,
            "direction": direction,
        })

    conflicts: list[dict] = []
    for pair in sorted(assertions):
        if len(canon_dirs[pair]) > 1:
            conflicts.append({"pair": list(pair),
                              "assertions": assertions[pair]})
    return conflicts


# ── 3. Revisit consistency (no golden standard needed) ──────────────


def compute_revisit_consistency(
    chapter_facts: list[dict],
    alias_map: dict[str, str] | None = None,
) -> dict:
    """Closed-loop revisit consistency over chapter-level facts.

    The same location mentioned across chapters should extract consistently:
    - hierarchy relations (contains/located_in) vote a child's parent;
      a child with >1 distinct parents across chapters is a parent conflict;
    - direction relations are checked for opposite-pair contradictions.

    Returns {"parent_conflicts", "children_multi_asserted",
    "parent_consistency", "direction_conflicts", "cases"} where cases lists
    every conflict with its chapters and assertions for manual review.

    ``alias_map``(可选,默认 None = 行为不变):地名别名→canonical 映射
    (LOCATION_ALIAS_MAP),source/target 先归一再统计——同一城市的异名
    (水浒 东京/京师/汴梁城)不再各自计为不同 parent。
    """
    def _canon(name: str) -> str:
        return alias_map.get(name, name) if alias_map else name

    parent_asserts: dict[str, set[tuple]] = defaultdict(set)
    spatial_facts: list[dict] = []

    for fact in chapter_facts:
        chapter = fact.get("chapter_id", fact.get("chapter"))
        for sr in fact.get("spatial_relationships") or []:
            rel_type = normalize_spatial_relation_type(
                str(sr.get("relation_type", "") or ""))
            src = _canon(str(sr.get("source", "") or ""))
            tgt = _canon(str(sr.get("target", "") or ""))
            if not src or not tgt or src == tgt:
                continue
            if rel_type in HIERARCHY_SPATIAL_RELATIONS:
                # contains: source contains target → target's parent is source
                # located_in: source is inside target → source's parent is target
                child, parent = (tgt, src) if rel_type == "contains" \
                    else (src, tgt)
                parent_asserts[child].add((chapter, parent))
            else:
                spatial_facts.append({
                    "chapter": chapter,
                    "source": src,
                    "target": tgt,
                    "relation_type": sr.get("relation_type", ""),
                    "value": sr.get("value", ""),
                })

    cases: list[dict] = []
    parent_conflicts = 0
    children_multi_asserted = 0
    for child in sorted(parent_asserts):
        entries = parent_asserts[child]
        if len(entries) < 2:
            continue
        children_multi_asserted += 1
        if len({parent for _, parent in entries}) > 1:
            parent_conflicts += 1
            cases.append({
                "type": "parent_conflict",
                "child": child,
                "assertions": [
                    {"chapter": ch, "parent": p}
                    for ch, p in sorted(entries, key=lambda e: (str(e[0]), e[1]))
                ],
            })

    dir_conflicts = find_direction_conflicts(spatial_facts)
    for conflict in dir_conflicts:
        cases.append({
            "type": "direction_conflict",
            "pair": conflict["pair"],
            "assertions": conflict["assertions"],
        })

    consistency = (1.0 - parent_conflicts / children_multi_asserted
                   if children_multi_asserted else 1.0)
    return {
        "parent_conflicts": parent_conflicts,
        "children_multi_asserted": children_multi_asserted,
        "parent_consistency": round(consistency, 4),
        "direction_conflicts": len(dir_conflicts),
        "cases": cases,
    }
