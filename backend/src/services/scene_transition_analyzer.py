"""SceneTransitionAnalyzer: extract location spatial signals from scene sequences.

Pure algorithmic analysis — zero LLM cost. Builds a transition graph from
ordered scenes, then infers containment relationships and sibling groups
to enhance location_parents voting.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class _Edge:
    """Directed edge in the scene transition graph."""

    count: int = 0
    event_types: set[str] = field(default_factory=set)


class SceneTransitionAnalyzer:
    """Extract location spatial relationship signals from scene sequences."""

    def analyze(
        self, all_scenes: list[dict]
    ) -> tuple[dict[str, Counter], dict]:
        """Analyze scene transitions to produce parent votes and auxiliary data.

        Args:
            all_scenes: Scenes ordered by (chapter, index). Each dict must
                        have at least ``chapter``, ``index``, ``location``.
                        ``event_type`` is optional.

        Returns:
            (parent_votes, scene_analysis) where
            - parent_votes: ``{child: Counter({parent: weight})}``
            - scene_analysis: ``{"sibling_groups": [...], "hub_nodes": {...}}``
        """
        # Filter scenes that have a valid location
        scenes = [s for s in all_scenes if s.get("location")]

        if len(scenes) < 2:
            return {}, {"sibling_groups": [], "hub_nodes": {}}

        # Sort by chapter then index
        scenes.sort(key=lambda s: (s.get("chapter", 0), s.get("index", 0)))

        transition_graph = self._build_transition_graph(scenes)
        votes, sibling_groups, hub_nodes = self._infer_containment(
            transition_graph
        )

        scene_analysis = {
            "sibling_groups": [list(g) for g in sibling_groups],
            "hub_nodes": {k: list(v) for k, v in hub_nodes.items()},
        }

        logger.info(
            "Scene transition analysis: %d edges, %d votes, %d sibling groups, %d hubs",
            sum(
                len(targets)
                for targets in transition_graph.values()
            ),
            sum(len(c) for c in votes.values()),
            len(sibling_groups),
            len(hub_nodes),
        )

        return votes, scene_analysis

    # ── Step 1: Build transition graph ──────────────────────────────

    @staticmethod
    def _build_transition_graph(
        scenes: list[dict],
    ) -> dict[str, dict[str, _Edge]]:
        """Build directed transition graph from ordered scene list.

        Only connects consecutive scenes *within the same chapter*.
        """
        graph: dict[str, dict[str, _Edge]] = defaultdict(dict)

        for i in range(len(scenes) - 1):
            s_curr = scenes[i]
            s_next = scenes[i + 1]

            # Only connect scenes within the same chapter
            if s_curr.get("chapter") != s_next.get("chapter"):
                continue

            loc_a = s_curr["location"]
            loc_b = s_next["location"]

            if loc_a == loc_b:
                continue

            if loc_b not in graph[loc_a]:
                graph[loc_a][loc_b] = _Edge()

            edge = graph[loc_a][loc_b]
            edge.count += 1
            evt = s_next.get("event_type")
            if evt:
                edge.event_types.add(evt)

        return graph

    # ── Step 2: Infer containment relationships ─────────────────────

    def _infer_containment(
        self,
        graph: dict[str, dict[str, _Edge]],
    ) -> tuple[dict[str, Counter], list[set[str]], dict[str, set[str]]]:
        """Derive parent votes, sibling groups, and hub nodes from the graph.

        Returns:
            (parent_votes, sibling_groups, hub_nodes)
        """
        votes: dict[str, Counter] = {}
        sibling_groups: list[set[str]] = []
        hub_nodes: dict[str, set[str]] = {}

        # Pre-compute bidirectional pairs
        bidirectional: dict[tuple[str, str], int] = {}
        for a, targets in graph.items():
            for b, edge_ab in targets.items():
                edge_ba = graph.get(b, {}).get(a)
                if edge_ba is not None:
                    pair = (min(a, b), max(a, b))
                    if pair not in bidirectional:
                        total = edge_ab.count + edge_ba.count
                        combined_events = edge_ab.event_types | edge_ba.event_types
                        bidirectional[pair] = total

        # Rule 1: High-frequency bidirectional non-travel transitions → siblings
        sibling_pairs: list[tuple[str, str]] = []
        for (a, b), total in bidirectional.items():
            if total >= 3:
                # Check event_types for travel
                events_ab = graph.get(a, {}).get(b, _Edge()).event_types
                events_ba = graph.get(b, {}).get(a, _Edge()).event_types
                all_events = events_ab | events_ba
                if "旅行" not in all_events and "travel" not in all_events:
                    sibling_pairs.append((a, b))

        # Merge sibling pairs into groups (union-find style)
        sibling_groups = self._merge_sibling_groups(sibling_pairs)

        # Rule 2: Name containment + transition → parent vote
        for a, targets in graph.items():
            for b, edge in targets.items():
                # B.name starts with A.name → B is child of A
                if b.startswith(a) and len(b) > len(a):
                    votes.setdefault(b, Counter())[a] += 2
                # A.name starts with B.name → A is child of B
                elif a.startswith(b) and len(a) > len(b):
                    votes.setdefault(a, Counter())[b] += 2

        # Rule 3: Hub node detection
        # A node with bidirectional edges to >=4 distinct other nodes is a hub
        neighbor_counts: dict[str, set[str]] = defaultdict(set)
        for (a, b), _ in bidirectional.items():
            neighbor_counts[a].add(b)
            neighbor_counts[b].add(a)

        for node, neighbors in neighbor_counts.items():
            if len(neighbors) >= 4:
                hub_nodes[node] = neighbors
                # Weak signal: hub's neighbors might be siblings under hub
                for neighbor in neighbors:
                    votes.setdefault(neighbor, Counter())[node] += 1

        return votes, sibling_groups, hub_nodes

    @staticmethod
    def _merge_sibling_groups(
        pairs: list[tuple[str, str]],
    ) -> list[set[str]]:
        """Merge pairs into connected groups using union-find."""
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            while parent.get(x, x) != x:
                parent[x] = parent.get(parent[x], parent[x])
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for a, b in pairs:
            union(a, b)

        groups: dict[str, set[str]] = defaultdict(set)
        all_nodes = {n for pair in pairs for n in pair}
        for node in all_nodes:
            groups[find(node)].add(node)

        return [g for g in groups.values() if len(g) >= 2]
