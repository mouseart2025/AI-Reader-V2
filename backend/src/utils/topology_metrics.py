"""Topology quality metrics for location hierarchy evaluation.

Compares a predicted location_parents dict against a golden standard dataset
to produce 5 quality indicators: parent_precision, parent_recall,
chain_accuracy, root_count, orphan_rate.
"""

from __future__ import annotations


def compute_topology_metrics(
    predicted: dict[str, str],
    golden_locations: list[dict],
) -> dict[str, float | int]:
    """Compute topology quality metrics.

    Args:
        predicted: System-produced {child: parent} mapping.
        golden_locations: List of dicts with keys: name, correct_parent, tier.
            Entries where correct_parent is null are roots in the golden standard.

    Returns:
        Dict with keys: parent_precision, parent_recall, chain_accuracy,
        root_count, orphan_rate.
    """
    # Build golden {child: parent} mapping (skip roots where parent is None)
    golden_parents: dict[str, str] = {}
    golden_roots: set[str] = set()
    for loc in golden_locations:
        name = loc.get("name", "")
        parent = loc.get("correct_parent")
        if not name:
            continue
        if parent:
            golden_parents[name] = parent
        else:
            golden_roots.add(name)

    # --- Parent Precision ---
    # Of all predicted parent assignments, how many match the golden standard?
    precision_correct = 0
    precision_total = 0
    for child, pred_parent in predicted.items():
        if child in golden_parents:
            precision_total += 1
            if golden_parents[child] == pred_parent:
                precision_correct += 1

    parent_precision = (
        precision_correct / precision_total if precision_total > 0 else 0.0
    )

    # --- Parent Recall ---
    # Of all golden parent assignments, how many are covered by predicted?
    recall_correct = 0
    recall_total = len(golden_parents)
    for child, gold_parent in golden_parents.items():
        if predicted.get(child) == gold_parent:
            recall_correct += 1

    parent_recall = recall_correct / recall_total if recall_total > 0 else 0.0

    # --- Chain Accuracy ---
    # For each golden chain (root → ... → leaf), measure longest correct prefix ratio.
    chain_accuracy = _compute_chain_accuracy(predicted, golden_locations)

    # --- Root Count ---
    # Number of locations in predicted that have no parent.
    all_children = set(predicted.keys())
    all_locations = all_children | set(predicted.values())
    roots = all_locations - all_children
    root_count = len(roots)

    # --- Orphan Rate ---
    # Non-root locations that exist in golden but have no parent in predicted.
    golden_names = {loc["name"] for loc in golden_locations if loc.get("name")}
    golden_non_roots = golden_names - golden_roots
    orphans = 0
    for name in golden_non_roots:
        if name not in predicted:
            orphans += 1
    orphan_rate = orphans / len(golden_names) if golden_names else 0.0

    return {
        "parent_precision": round(parent_precision, 4),
        "parent_recall": round(parent_recall, 4),
        "chain_accuracy": round(chain_accuracy, 4),
        "root_count": root_count,
        "orphan_rate": round(orphan_rate, 4),
    }


def _compute_chain_accuracy(
    predicted: dict[str, str],
    golden_locations: list[dict],
) -> float:
    """Compute chain accuracy: average longest-correct-prefix ratio across all chains.

    A chain is a path from a leaf to a root in the golden standard.
    For each chain, we walk from leaf upward and count how many consecutive
    parent links match the predicted hierarchy.
    """
    # Build golden parent map and find leaves
    golden_parents: dict[str, str] = {}
    all_children: set[str] = set()
    all_names: set[str] = set()
    for loc in golden_locations:
        name = loc.get("name", "")
        parent = loc.get("correct_parent")
        if not name:
            continue
        all_names.add(name)
        if parent:
            golden_parents[name] = parent
            all_children.add(name)

    # Leaves: locations that are never a parent of another location
    parents_set = set(golden_parents.values())
    leaves = all_children - (parents_set & all_children)
    # Also include children that have no children themselves
    if not leaves:
        leaves = all_children

    if not leaves:
        return 1.0  # No chains to evaluate

    total_ratio = 0.0
    chain_count = 0

    for leaf in leaves:
        # Build golden chain: leaf → parent → grandparent → ... → root
        chain: list[str] = [leaf]
        node = leaf
        visited: set[str] = {leaf}
        while node in golden_parents:
            parent = golden_parents[node]
            if parent in visited:
                break  # Cycle protection
            chain.append(parent)
            visited.add(parent)
            node = parent

        if len(chain) < 2:
            continue

        # Count longest correct prefix from leaf upward
        correct = 0
        for i in range(len(chain) - 1):
            child = chain[i]
            expected_parent = chain[i + 1]
            actual_parent = predicted.get(child)
            if actual_parent == expected_parent:
                correct += 1
            else:
                break  # First mismatch breaks the chain

        chain_links = len(chain) - 1
        total_ratio += correct / chain_links if chain_links > 0 else 1.0
        chain_count += 1

    return total_ratio / chain_count if chain_count > 0 else 1.0


def compute_hierarchy_health(
    location_parents: dict[str, str],
    location_tiers: dict[str, str] | None = None,
) -> dict[str, float | int | str]:
    """Compute full hierarchy health metrics (no golden standard needed).

    Returns metrics for monitoring overall hierarchy quality:
    - avg_depth: average chain depth from leaf to root
    - max_depth: deepest chain
    - max_children: largest number of children for any single parent
    - max_children_location: which location has the most children
    - tier_inversions: number of parent-child pairs where child tier > parent tier
    - noise_roots: non-world/continent locations acting as roots
    - orphan_count: locations in tiers but not in parents (as child or parent)
    - total_parents: number of parent-child relationships
    """
    if not location_parents:
        return {"avg_depth": 0, "max_depth": 0, "max_children": 0,
                "max_children_location": "", "tier_inversions": 0,
                "noise_roots": 0, "total_parents": 0}

    children_set = set(location_parents.keys())
    parents_set = set(location_parents.values())
    all_locs = children_set | parents_set
    roots = parents_set - children_set

    # Children map
    children_map: dict[str, list[str]] = {}
    for child, parent in location_parents.items():
        children_map.setdefault(parent, []).append(child)

    # Depth calculation
    def _get_depth(loc: str) -> int:
        d = 0
        current = loc
        seen = {loc}
        while current in location_parents:
            current = location_parents[current]
            if current in seen:
                break
            seen.add(current)
            d += 1
        return d

    depths = [_get_depth(c) for c in children_set]
    avg_depth = sum(depths) / len(depths) if depths else 0
    max_depth = max(depths) if depths else 0

    # Max children
    max_children = 0
    max_children_loc = ""
    for parent, kids in children_map.items():
        if len(kids) > max_children:
            max_children = len(kids)
            max_children_loc = parent

    # Tier inversions (requires suffix rank)
    tier_inversions = 0
    if location_tiers:
        try:
            from src.services.world_structure_agent import _get_suffix_rank
            for child, parent in location_parents.items():
                cr = _get_suffix_rank(child)
                pr = _get_suffix_rank(parent)
                if cr is not None and pr is not None and cr < pr:
                    tier_inversions += 1
        except ImportError:
            pass

    # Noise roots (non-world/continent)
    noise_roots = 0
    _OK_ROOT_TIERS = {"world", "continent", "region"}
    for r in roots:
        tier = (location_tiers or {}).get(r, "")
        if tier and tier not in _OK_ROOT_TIERS:
            noise_roots += 1

    return {
        "avg_depth": round(avg_depth, 2),
        "max_depth": max_depth,
        "max_children": max_children,
        "max_children_location": max_children_loc,
        "tier_inversions": tier_inversions,
        "noise_roots": noise_roots,
        "root_count": len(roots),
        "roots": sorted(roots),
        "total_parents": len(location_parents),
        "total_locations": len(all_locs),
    }
