"""EdmondsResolver — GeoSkill that finds globally optimal parent tree.

Uses Chu-Liu/Edmonds' algorithm (networkx.maximum_spanning_arborescence)
to find the maximum weight directed spanning tree from accumulated votes.

Mathematical formulation:
    Given directed graph G=(V, E, w) where w(parent→child) = vote weight,
    find arborescence T* rooted at uber_root that maximizes ∑w(e) for e∈T*.

Key advantages over voting method:
- Global optimality: guaranteed best tree under vote weights (not greedy)
- Structural guarantee: result is always a valid tree (no cycles, connected)
- Deterministic: no LLM dependency, millisecond execution

Based on: McDonald et al. (2005) "Non-Projective Dependency Parsing
using Spanning Tree Algorithms" — same mathematical structure applied
to NLP dependency parsing.
"""

from __future__ import annotations

import logging
from collections import Counter

import networkx as nx

from src.services.geo_skills.base import GeoSkill
from src.services.geo_skills.snapshot import HierarchySnapshot, SkillResult

logger = logging.getLogger(__name__)


class EdmondsResolver(GeoSkill):
    """Resolve votes into optimal parent tree via Edmonds' algorithm."""

    @property
    def name(self) -> str:
        return "EdmondsResolver"

    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        votes = snapshot.parent_votes
        tiers = snapshot.location_tiers
        freq = snapshot.location_frequencies

        if not votes:
            return SkillResult.empty(self.name, "No votes to resolve")

        # Find uber_root
        uber_root = self._find_uber_root(snapshot.location_parents)
        if not uber_root:
            # Fallback: find the "world" tier location
            for loc, tier in tiers.items():
                if tier == "world":
                    uber_root = loc
                    break
        if not uber_root:
            uber_root = "天下"  # last resort

        # ── Build directed graph ──
        # Edge direction: parent → child (Edmonds convention for arborescence)
        # Weight: accumulated votes for this parent-child pair
        G = nx.DiGraph()
        all_locs: set[str] = set(tiers.keys())
        all_locs.update(votes.keys())
        all_locs.add(uber_root)

        from src.services.world_structure_agent import TIER_ORDER, _get_suffix_rank

        for child, vote_counter in votes.items():
            for parent, weight in vote_counter.items():
                if not parent or parent == child:
                    continue
                if parent not in all_locs:
                    continue
                w = float(weight)
                if w <= 0:
                    continue

                # ── Tier soft constraint ──
                # When BOTH have recognizable suffixes and parent is clearly
                # smaller than child, halve the weight (discourage but don't block).
                # Blocking too aggressively reduces depth by removing valid deep edges.
                p_suf = _get_suffix_rank(parent)
                c_suf = _get_suffix_rank(child)
                if p_suf is not None and c_suf is not None and p_suf > c_suf:
                    w *= 0.1  # heavy penalty but not blocked

                # Edge: parent → child
                if G.has_edge(parent, child):
                    G[parent][child]["weight"] = max(
                        G[parent][child]["weight"], w
                    )
                else:
                    G.add_edge(parent, child, weight=w)

        # Ensure all locations are nodes
        for loc in all_locs:
            if loc not in G:
                G.add_node(loc)

        # Ensure uber_root can reach all nodes: add tiny-weight fallback edges
        # These are "last resort" connections — Edmonds will prefer real votes
        _FALLBACK_WEIGHT = 0.001
        for node in G.nodes():
            if node != uber_root and not G.has_edge(uber_root, node):
                G.add_edge(uber_root, node, weight=_FALLBACK_WEIGHT)

        logger.info(
            "EdmondsResolver: graph %d nodes, %d edges, root=%s",
            G.number_of_nodes(), G.number_of_edges(), uber_root,
        )

        # ── Run Edmonds' algorithm ──
        try:
            T = nx.maximum_spanning_arborescence(G, attr="weight")
        except nx.NetworkXException as e:
            logger.error("Edmonds algorithm failed: %s", e)
            return SkillResult.empty(self.name, f"Edmonds failed: {e}")

        # ── Extract parent assignments ──
        parents: dict[str, str] = {}
        for u, v in T.edges():
            # u → v means u is parent of v
            parents[v] = u

        # ── Phase 2: Degree balancing ──
        # If any node has >MAX_CHILDREN children, redistribute excess
        # to intermediate grouping nodes (virtual or existing)
        _MAX_CHILDREN = 30
        parents = self._balance_degrees(parents, tiers, _MAX_CHILDREN)

        result = SkillResult(
            skill_name=self.name,
            parent_overrides=parents,
        )

        # Stats
        ch_count = Counter(parents.values())
        top = ch_count.most_common(1)
        max_ch = top[0][1] if top else 0
        logger.info(
            "EdmondsResolver: %d parents, max_children=%d(%s)",
            len(parents), max_ch, top[0][0] if top else "?",
        )
        return result

    @staticmethod
    def _find_uber_root(parents: dict[str, str]) -> str | None:
        if not parents:
            return None
        children = set(parents.keys())
        counts: Counter = Counter()
        for p in parents.values():
            if p not in children:
                counts[p] += 1
        return counts.most_common(1)[0][0] if counts else None

    @staticmethod
    def _balance_degrees(
        parents: dict[str, str],
        tiers: dict[str, str],
        max_children: int,
    ) -> dict[str, str]:
        """Redistribute children when a node exceeds max_children.

        Two-phase strategy:
        Phase 1: Redistribute leaf children to existing intermediate nodes
        Phase 2: For remaining overflows, redistribute to ANY smaller-tier
                 child (not just intermediates) — creating new intermediate layers
        """
        from src.services.world_structure_agent import TIER_ORDER

        def _rebuild_children_map():
            cm: dict[str, list[str]] = {}
            for child, parent in parents.items():
                cm.setdefault(parent, []).append(child)
            return cm

        for iteration in range(10):
            children_map = _rebuild_children_map()
            any_change = False

            for node in list(children_map.keys()):
                kids = children_map.get(node, [])
                if len(kids) <= max_children:
                    continue

                node_rank = TIER_ORDER.get(tiers.get(node, "world"), 0)

                # Sort kids: non-leaf first (intermediates), then by tier rank desc
                kid_has_children = {
                    k: len(children_map.get(k, [])) for k in kids
                }
                # Candidates to absorb: kids with lower tier rank than leaves
                absorbers = sorted(
                    [k for k in kids if kid_has_children.get(k, 0) > 0],
                    key=lambda k: kid_has_children.get(k, 0),
                    reverse=True,
                )
                # If no absorbers, use any kid that has a bigger tier than others
                if not absorbers:
                    absorbers = sorted(
                        kids,
                        key=lambda k: TIER_ORDER.get(tiers.get(k, "site"), 5),
                    )
                    # Only use kids that are at least one tier bigger than the smallest
                    if absorbers:
                        min_rank = TIER_ORDER.get(
                            tiers.get(absorbers[-1], "site"), 5
                        )
                        absorbers = [
                            k for k in absorbers
                            if TIER_ORDER.get(tiers.get(k, "site"), 5) < min_rank
                        ]

                if not absorbers:
                    continue

                # Leaves to redistribute (smallest tier first)
                leaves = sorted(
                    [k for k in kids if k not in absorbers],
                    key=lambda k: TIER_ORDER.get(tiers.get(k, "site"), 5),
                    reverse=True,
                )

                redistributed = 0
                for leaf in leaves:
                    if len(kids) <= max_children:
                        break
                    leaf_rank = TIER_ORDER.get(tiers.get(leaf, "site"), 5)

                    # Find best absorber: bigger tier + fewest current children
                    best = None
                    best_score = -1
                    for ab in absorbers:
                        ab_rank = TIER_ORDER.get(tiers.get(ab, "city"), 4)
                        if ab_rank >= leaf_rank:
                            continue  # absorber must be bigger tier
                        ab_children = len(children_map.get(ab, []))
                        if ab_children >= max_children:
                            continue  # don't overflow absorber
                        score = max_children - ab_children
                        if score > best_score:
                            best = ab
                            best_score = score

                    if best:
                        parents[leaf] = best
                        kids.remove(leaf)
                        children_map.setdefault(best, []).append(leaf)
                        redistributed += 1
                        any_change = True

                if redistributed:
                    logger.debug(
                        "Degree balance: %s %d→%d children",
                        node, len(kids) + redistributed, len(kids),
                    )

            if not any_change:
                break

        return parents
