"""GraphRAG-style community-based hierarchy construction baseline.

Compares community-detection-driven hierarchy (à la Microsoft GraphRAG)
against our Maximum Weight Arborescence (MWA) formulation.

Method:
1. Load our per-chapter extracted locations from ChapterFact DB.
2. Build weighted undirected graph: nodes = locations,
   edge weight = co-occurrence count across chapters.
3. Run Louvain (hierarchical) community detection to find communities
   at multiple resolution levels.
4. Derive an instance-of hierarchy: for each location, its parent is
   the most-central node of its containing community at the coarser
   resolution (recursively).
5. Output parent map + structural metrics (depth, max_ch, cycles, roots).

This is a faithful algorithmic baseline for what GraphRAG does at its
core (entity co-occurrence → Leiden/Louvain → community hierarchy),
controlling the LLM extraction step (we reuse our pipeline's extraction
so LLM quality doesn't confound the comparison).

Usage:
    cd backend && uv run python scripts/graphrag_style_baseline.py --novel xiyouji
    cd backend && uv run python scripts/graphrag_style_baseline.py --novel honglou
    cd backend && uv run python scripts/graphrag_style_baseline.py --both

Output: paper/evaluation/v071/baselines/graphrag_style/<slug>.json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = os.path.expanduser("~/.ai-reader-v2/data.db")
OUTPUT_ROOT = Path(
    os.environ.get(
        "BASELINE_OUTPUT_DIR",
        str(Path.home() / "Baiduyun/AISoul/ai-reader-internal/paper/evaluation/v071/baselines/graphrag_style"),
    )
)

NOVELS: dict[str, dict] = {
    "xiyouji": {
        "title": "西游记",
        "novel_id": "3b2ef56c-1a55-466a-a7d1-34272446a198",
        "gold_file": "tests/fixtures/golden_standard_journey_to_west.json",
    },
    "honglou": {
        "title": "红楼梦",
        "novel_id": "c384901a-8b71-437a-af35-b5ec1c56c696",
        "gold_file": "tests/fixtures/golden_standard_dream_of_red_chamber.json",
    },
}


# =============================================================================
# Build co-occurrence graph from chapter_facts
# =============================================================================

def build_cooccurrence_graph(novel_id: str):
    """Return (G, mention_count) where G is an undirected weighted NetworkX graph."""
    import networkx as nx

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id=? ORDER BY chapter_id",
        (novel_id,),
    ).fetchall()
    conn.close()

    mention_count: Counter[str] = Counter()
    cooccur: dict[tuple[str, str], int] = defaultdict(int)

    for (fact_json_text,) in rows:
        try:
            fact = json.loads(fact_json_text)
        except Exception:
            continue
        locs_this_chapter = set()
        for loc in fact.get("locations") or []:
            name = (loc.get("name") or "").strip()
            if name:
                locs_this_chapter.add(name)
                mention_count[name] += 1
        # All pairs co-occur
        locs = sorted(locs_this_chapter)
        for i, a in enumerate(locs):
            for b in locs[i + 1:]:
                cooccur[(a, b)] += 1

    G = nx.Graph()
    for name, mc in mention_count.items():
        G.add_node(name, mention_count=mc)
    for (a, b), w in cooccur.items():
        G.add_edge(a, b, weight=w)

    return G, mention_count


# =============================================================================
# Hierarchical community detection (Louvain at multiple resolutions)
# =============================================================================

def hierarchical_communities(G) -> list[dict[str, int]]:
    """Run Louvain at 4 resolutions to approximate GraphRAG's hierarchical
    community levels. Returns list of {node: community_id} dicts, from
    coarsest (few clusters) to finest (many clusters)."""
    import community as community_louvain  # python-louvain

    resolutions = [0.5, 1.0, 2.0, 4.0]  # higher resolution → more communities
    partitions: list[dict[str, int]] = []
    for res in resolutions:
        if G.number_of_edges() == 0:
            partitions.append({n: 0 for n in G.nodes})
            continue
        part = community_louvain.best_partition(G, resolution=res, random_state=42, weight="weight")
        partitions.append(part)
    return partitions


# =============================================================================
# Derive instance-of hierarchy from hierarchical communities
# =============================================================================

def communities_to_hierarchy(partitions: list[dict[str, int]], mention_count: Counter,
                              G) -> tuple[dict[str, str], dict]:
    """For each location, pick parent as the most-central node in its
    containing coarser-level community. Root = most-central node of
    top-level community.

    Returns (parent_map, debug_info).
    """
    import networkx as nx

    # For each resolution level, compute community → best representative
    # representative = node with highest weighted degree within its community
    # (GraphRAG does this to pick a "community summary" anchor)

    levels = partitions  # coarsest first
    num_levels = len(levels)

    # weighted_degree cache
    weighted_degree = {n: sum(d["weight"] for _, _, d in G.edges(n, data=True))
                        for n in G.nodes}

    def community_representative(partition: dict[str, int], members: set) -> str:
        """Node within `members` with highest weighted degree."""
        best = max(members, key=lambda n: (weighted_degree.get(n, 0), mention_count.get(n, 0)))
        return best

    # For each node, traverse from finest → coarsest:
    # parent = representative of the same coarser-level community
    # root = representative of coarsest-level community containing node

    finest = levels[-1]  # most fine-grained partition
    parent_map: dict[str, str] = {}

    # Pre-compute communities at each level: {level_index: {community_id: set(nodes)}}
    level_communities = []
    for part in levels:
        comm_to_nodes: dict[int, set] = defaultdict(set)
        for node, cid in part.items():
            comm_to_nodes[cid].add(node)
        level_communities.append(comm_to_nodes)

    # For each node, walk from fine level up. Parent = representative of the
    # community at the NEXT COARSER level containing this node.
    for node in G.nodes:
        # Walk coarsest → finest: find a level where node has a parent
        # representative that is NOT itself
        current = node
        for lvl_idx in range(num_levels - 1, 0, -1):
            part = levels[lvl_idx]
            if node not in part:
                continue
            # Find node's community at this level
            cid = part[node]
            # Get the coarser level's community containing node
            coarser = levels[lvl_idx - 1]
            coarser_cid = coarser.get(node)
            if coarser_cid is None:
                continue
            coarser_members = level_communities[lvl_idx - 1][coarser_cid]
            rep = community_representative(coarser, coarser_members)
            if rep != node:
                parent_map[node] = rep
                break
        # If we never found a different parent, check coarsest level
        if node not in parent_map:
            coarsest_cid = levels[0].get(node)
            if coarsest_cid is not None:
                coarsest_members = level_communities[0][coarsest_cid]
                rep = community_representative(levels[0], coarsest_members)
                if rep != node:
                    parent_map[node] = rep

    return parent_map, {
        "num_levels": num_levels,
        "communities_per_level": [len(lc) for lc in level_communities],
    }


# =============================================================================
# Structural metrics + gold comparison
# =============================================================================

def compute_metrics(parent_map: dict[str, str], gold_locs: list[dict]) -> dict:
    # Structural
    all_nodes = set(parent_map.keys()) | set(parent_map.values())
    roots = all_nodes - set(parent_map.keys())
    max_children = max(Counter(parent_map.values()).values(), default=0)

    # Cycle detect
    def has_cycle() -> tuple[bool, list]:
        visiting, visited = set(), set()
        cycles = []
        for start in list(parent_map.keys()):
            if start in visited:
                continue
            path = []
            cur = start
            while cur:
                if cur in path:
                    cycles.append(path[path.index(cur):] + [cur])
                    break
                if cur in visited:
                    break
                path.append(cur)
                cur = parent_map.get(cur)
            visited.update(path)
        return bool(cycles), cycles

    cycle, cycle_list = has_cycle()

    # Depth
    def depth(n: str, seen: set) -> int:
        if n in seen:
            return 0
        seen = seen | {n}
        p = parent_map.get(n)
        if not p:
            return 0
        return 1 + depth(p, seen)
    depths = [depth(n, set()) for n in parent_map]
    avg_depth = round(sum(depths) / len(depths), 2) if depths else 0.0

    metrics = {
        "total_nodes": len(all_nodes),
        "parent_assignments": len(parent_map),
        "roots": sorted(roots),
        "root_count": len(roots),
        "max_children": max_children,
        "avg_depth": avg_depth,
        "has_cycle": cycle,
        "cycle_sample": cycle_list[:2] if cycle_list else [],
        "valid_tree": (not cycle) and len(roots) == 1,
    }

    # Gold comparison
    from src.utils.topology_metrics import compute_topology_metrics
    try:
        topo = compute_topology_metrics(parent_map, gold_locs)
        metrics["topology_vs_gold"] = topo
    except Exception as e:
        metrics["topology_error"] = str(e)

    return metrics


# =============================================================================
# Orchestration
# =============================================================================

def run_for_novel(slug: str) -> dict:
    meta = NOVELS[slug]
    print(f"\n=== {meta['title']} ({slug}) ===")
    print("  Building co-occurrence graph from chapter_facts...")
    G, mention_count = build_cooccurrence_graph(meta["novel_id"])
    print(f"  Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    print("  Running hierarchical Louvain community detection (4 resolutions)...")
    partitions = hierarchical_communities(G)
    for i, p in enumerate(partitions):
        n_comms = len(set(p.values()))
        print(f"    Level {i} (res={[0.5, 1.0, 2.0, 4.0][i]}): {n_comms} communities")

    print("  Deriving instance-of hierarchy from communities...")
    parent_map, debug = communities_to_hierarchy(partitions, mention_count, G)
    print(f"  Parent assignments: {len(parent_map)}")

    # Load gold
    gold_path = Path(__file__).parent.parent / meta["gold_file"]
    gold_locs = []
    if gold_path.exists():
        gold_locs = json.loads(gold_path.read_text()).get("locations", [])

    metrics = compute_metrics(parent_map, gold_locs)

    result = {
        "slug": slug,
        "title": meta["title"],
        "method": "GraphRAG-style (Louvain hierarchical community detection on co-occurrence graph)",
        "graph": {"nodes": G.number_of_nodes(), "edges": G.number_of_edges()},
        "debug": debug,
        "metrics": metrics,
        "parent_map": parent_map,
    }

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_ROOT / f"{slug}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(f"  Saved: {out}")

    # Summary print
    m = metrics
    print(f"  ─ Structure: roots={m['root_count']}, max_ch={m['max_children']},"
          f" depth={m['avg_depth']}, cycle={m['has_cycle']}, valid_tree={m['valid_tree']}")
    if "topology_vs_gold" in m:
        t = m["topology_vs_gold"]
        print(f"  ─ vs gold: parent_P={t.get('parent_precision', 0):.3f},"
              f" recall={t.get('parent_recall', 0):.3f},"
              f" chain_acc={t.get('chain_accuracy', 0):.3f}")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--novel", choices=list(NOVELS.keys()))
    ap.add_argument("--both", action="store_true")
    args = ap.parse_args()

    targets = list(NOVELS.keys()) if args.both else ([args.novel] if args.novel else [])
    if not targets:
        ap.error("--novel xiyouji|honglou or --both")

    for slug in targets:
        run_for_novel(slug)


if __name__ == "__main__":
    main()
