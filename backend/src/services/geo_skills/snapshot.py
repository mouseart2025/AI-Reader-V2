"""HierarchySnapshot — immutable hierarchy state for Geographic Agent Skills.

Each GeoSkill consumes a snapshot and produces a SkillResult. The orchestrator
applies results to create new snapshots, forming a version chain:

    snapshot_v0 → VoteBuilder → snapshot_v1 → Skeleton → snapshot_v2 → ...

Snapshots are never modified in place. This guarantees:
- Rollback to any version
- A/B comparison between versions (paper metrics)
- LLM failure isolation (failed skill → snapshot unchanged)
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field


@dataclass(frozen=True)
class HierarchySnapshot:
    """Immutable hierarchy state."""

    location_parents: dict[str, str]         # child → parent
    location_tiers: dict[str, str]           # location → tier
    parent_votes: dict[str, Counter]         # child → Counter({parent: weight})
    location_frequencies: Counter             # location → mention count
    chapter_settings: dict[int, str]          # chapter_id → primary setting
    location_chapters: dict[str, list[int]]   # location → [chapter_ids]

    # Metadata
    version: int = 0
    source: str = ""       # which skill produced this snapshot
    timestamp: float = 0.0
    novel_genre_hint: str = ""

    def apply(self, result: SkillResult) -> HierarchySnapshot:
        """Create a new snapshot by applying a SkillResult.

        Merges votes additively, applies parent overrides, merges tier updates.
        Returns a NEW snapshot (self is unchanged).
        """
        # Merge votes (additive)
        merged_votes = {k: Counter(v) for k, v in self.parent_votes.items()}
        for child, counter in result.new_votes.items():
            if child not in merged_votes:
                merged_votes[child] = Counter()
            merged_votes[child] += counter

        # Merge parent overrides
        merged_parents = dict(self.location_parents)
        for child, parent in result.parent_overrides.items():
            if parent is None:
                merged_parents.pop(child, None)
            else:
                merged_parents[child] = parent

        # Merge tier updates
        merged_tiers = dict(self.location_tiers)
        merged_tiers.update(result.tier_updates)

        return HierarchySnapshot(
            location_parents=merged_parents,
            location_tiers=merged_tiers,
            parent_votes=merged_votes,
            location_frequencies=self.location_frequencies,
            chapter_settings=self.chapter_settings,
            location_chapters=self.location_chapters,
            version=self.version + 1,
            source=result.skill_name,
            timestamp=time.time(),
            novel_genre_hint=self.novel_genre_hint,
        )


@dataclass
class SkillResult:
    """Output of a GeoSkill execution."""

    skill_name: str
    new_votes: dict[str, Counter] = field(default_factory=dict)
    parent_overrides: dict[str, str | None] = field(default_factory=dict)
    tier_updates: dict[str, str] = field(default_factory=dict)
    synonym_pairs: list[tuple[str, str]] = field(default_factory=list)
    direction_constraints: list[dict] = field(default_factory=list)

    # Execution metadata
    success: bool = True
    error_message: str = ""
    duration_ms: int = 0
    llm_calls: int = 0
    logs: list[str] = field(default_factory=list)  # sub-step progress messages

    # Paper metrics delta (computed by evaluator)
    metrics_before: dict | None = None
    metrics_after: dict | None = None

    @staticmethod
    def empty(skill_name: str, error: str = "") -> SkillResult:
        """Create an empty result for failed skills."""
        return SkillResult(
            skill_name=skill_name,
            success=not bool(error),
            error_message=error,
        )


@dataclass
class HierarchyMetrics:
    """Paper-relevant hierarchy quality metrics."""

    avg_depth: float = 0.0
    max_depth: int = 0
    root_count: int = 0
    max_children: int = 0
    max_children_node: str = ""
    total_locations: int = 0
    total_parents: int = 0
    depth_distribution: dict[int, int] = field(default_factory=dict)

    # Frequency tier breakdown
    core_count: int = 0      # freq ≥ 10
    regular_count: int = 0   # freq 3-9
    micro_count: int = 0     # freq ≤ 2

    # Golden standard (if available)
    golden_precision: float | None = None
    golden_recall: float | None = None

    @staticmethod
    def compute(snapshot: HierarchySnapshot) -> HierarchyMetrics:
        """Compute all metrics from a snapshot."""
        parents = snapshot.location_parents
        tiers = snapshot.location_tiers
        freq = snapshot.location_frequencies

        # Depth computation
        def _depth(loc: str, visited: set | None = None) -> int:
            if visited is None:
                visited = set()
            if loc in visited:
                return 0
            visited.add(loc)
            p = parents.get(loc)
            if not p:
                return 0
            return 1 + _depth(p, visited)

        depths = {loc: _depth(loc) for loc in tiers}
        avg_d = sum(depths.values()) / len(depths) if depths else 0.0
        max_d = max(depths.values()) if depths else 0

        # Children count
        children_count: Counter = Counter()
        for child, parent in parents.items():
            children_count[parent] += 1
        top = children_count.most_common(1)
        max_ch = top[0][1] if top else 0
        max_ch_node = top[0][0] if top else ""

        roots = [loc for loc in tiers if loc not in parents]
        depth_dist = dict(Counter(depths.values()))

        # Frequency tiers
        n_core = sum(1 for c in freq.values() if c >= 10)
        n_regular = sum(1 for c in freq.values() if 3 <= c <= 9)
        n_micro = sum(1 for c in freq.values() if c <= 2)

        return HierarchyMetrics(
            avg_depth=round(avg_d, 2),
            max_depth=max_d,
            root_count=len(roots),
            max_children=max_ch,
            max_children_node=max_ch_node,
            total_locations=len(tiers),
            total_parents=len(parents),
            depth_distribution=depth_dist,
            core_count=n_core,
            regular_count=n_regular,
            micro_count=n_micro,
        )

    def summary(self) -> str:
        """One-line summary for logging."""
        return (
            f"depth={self.avg_depth:.2f}/{self.max_depth} "
            f"roots={self.root_count} "
            f"max_ch={self.max_children}({self.max_children_node}) "
            f"locs={self.total_locations}"
        )
