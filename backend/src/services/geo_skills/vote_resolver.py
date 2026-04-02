"""VoteResolver — GeoSkill that resolves votes into parent assignments.

Extracted from WorldStructureAgent._resolve_parents(). Key logic:
- Frequency-based tiering: micro-locations use simplified resolution
- Uber-root avoidance for micro-locations (fallback to chapter setting)
- Intermediate layer protection
- Sibling detection and promotion
- Direction validation via suffix rank
"""

from __future__ import annotations

import logging
from collections import Counter

from src.services.geo_skills.base import GeoSkill
from src.services.geo_skills.snapshot import HierarchySnapshot, SkillResult
from src.services.world_structure_agent import (
    TIER_ORDER,
    _NAME_SUFFIX_TIER,
    _get_suffix_rank,
)

logger = logging.getLogger(__name__)

_MIN_MICRO_VOTES = 3
_MICRO_FREQ_THRESHOLD = 2


def _find_uber_root(parents: dict[str, str]) -> str | None:
    if not parents:
        return None
    children = set(parents.keys())
    counts: Counter = Counter()
    for p in parents.values():
        if p not in children:
            counts[p] += 1
    return counts.most_common(1)[0][0] if counts else None


def _find_common_parent(
    a: str, b: str,
    votes: dict[str, Counter],
    known: set[str],
) -> str | None:
    """Find a common parent for two locations from their votes."""
    a_parents = set(votes.get(a, {}).keys())
    b_parents = set(votes.get(b, {}).keys())
    common = (a_parents & b_parents) - {a, b}
    if not common:
        return None
    # Pick the one with highest combined votes
    best, best_score = None, 0
    for p in common:
        if known and p not in known:
            continue
        score = votes.get(a, Counter()).get(p, 0) + votes.get(b, Counter()).get(p, 0)
        if score > best_score:
            best, best_score = p, score
    return best


class VoteResolver(GeoSkill):
    """Resolve accumulated votes into parent-child assignments."""

    @property
    def name(self) -> str:
        return "VoteResolver"

    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        votes = snapshot.parent_votes
        tiers = snapshot.location_tiers
        freq = snapshot.location_frequencies
        uber_root = _find_uber_root(snapshot.location_parents)

        # Build known locations set
        known_locs: set[str] = set(tiers.keys())
        known_locs.update(votes.keys())

        freq_active = bool(freq)

        from src.services.hierarchy_consolidator import _is_sub_location_name

        # ── Phase 1: Split by frequency tier ──
        raw: dict[str, str] = {}           # core + regular
        micro_mounted: dict[str, str] = {}  # micro auto-mount
        pruned = 0

        for child, vote_counter in votes.items():
            if not vote_counter:
                continue
            f = freq.get(child, 0)

            # Micro-location handling
            if freq_active and f <= _MICRO_FREQ_THRESHOLD:
                total = sum(vote_counter.values())
                if total < _MIN_MICRO_VOTES and _is_sub_location_name(child):
                    pruned += 1
                    continue
                # Pick non-uber_root winner, fallback to chapter setting
                best = None
                for winner, _ in vote_counter.most_common():
                    if winner and winner != child:
                        if not known_locs or winner in known_locs:
                            if winner != uber_root:
                                best = winner
                                break
                            elif best is None:
                                best = winner
                if best == uber_root and snapshot.location_chapters:
                    ch_ids = snapshot.location_chapters.get(child, [])
                    for ch_id in ch_ids:
                        setting = snapshot.chapter_settings.get(ch_id)
                        if (setting and setting != child
                                and setting != uber_root
                                and (not known_locs or setting in known_locs)):
                            best = setting
                            break
                if best:
                    micro_mounted[child] = best
                continue

            # Core + regular: full resolution
            for winner, _ in vote_counter.most_common():
                if winner and winner != child:
                    if not known_locs or winner in known_locs:
                        raw[child] = winner
                        break

        if pruned:
            logger.info("Pruned %d micro sub-locations", pruned)
        if micro_mounted:
            logger.info("Micro auto-mount: %d locations", len(micro_mounted))

        # ── Phase 2: Intermediate layer protection ──
        fixes = 0
        for child in list(raw):
            parent = raw.get(child)
            if not parent:
                continue
            c_rank = _get_suffix_rank(child)
            p_rank = _get_suffix_rank(parent)
            if c_rank is None or p_rank is None:
                continue
            if c_rank - p_rank < 2:
                continue
            for cand, _ in votes.get(child, Counter()).items():
                if cand == parent or cand == child:
                    continue
                cr = _get_suffix_rank(cand)
                if cr is None:
                    continue
                if p_rank < cr < c_rank:
                    cand_p = raw.get(cand)
                    if cand_p == parent or (
                        cand in votes and parent in votes.get(cand, Counter())
                    ):
                        raw[child] = cand
                        fixes += 1
                        break
        if fixes:
            logger.info("Intermediate layer fixes: %d", fixes)

        # ── Phase 3: Direction validation ──
        validated: dict[str, str] = {}
        for child, parent in raw.items():
            c_suf = _get_suffix_rank(child)
            p_suf = _get_suffix_rank(parent)
            c_eff = c_suf if c_suf is not None else TIER_ORDER.get(tiers.get(child, "city"), 4)
            p_eff = p_suf if p_suf is not None else TIER_ORDER.get(tiers.get(parent, "city"), 4)

            if p_eff > c_eff:
                # Flip
                if parent not in validated:
                    validated[parent] = child
                else:
                    validated[child] = parent
            else:
                validated[child] = parent

        # ── Phase 4: Same-tier sibling promotion ──
        _SIBLING_SUFFIXES = {"府", "城", "寨", "庄", "镇", "村", "国", "州"}
        _SIBLING_TIERS = {"kingdom", "region", "continent", "city"}
        promoted = 0
        for child in list(validated):
            parent = validated.get(child)
            if not parent:
                continue
            c_suf = _get_suffix_rank(child)
            p_suf = _get_suffix_rank(parent)

            is_sibling = False
            if c_suf is not None and p_suf is not None and c_suf == p_suf:
                suffix_char = None
                for suf, _ in _NAME_SUFFIX_TIER:
                    if child.endswith(suf):
                        suffix_char = suf
                        break
                if suffix_char in _SIBLING_SUFFIXES:
                    is_sibling = True
            elif c_suf is None and p_suf is None:
                ct = tiers.get(child, "city")
                pt = tiers.get(parent, "city")
                if ct == pt and ct in _SIBLING_TIERS:
                    is_sibling = True

            if is_sibling:
                common = _find_common_parent(child, parent, votes, known_locs)
                if common and common != child and common != parent:
                    validated[child] = common
                    validated[parent] = common
                    promoted += 1
        if promoted:
            logger.info("Sibling promotion: %d pairs", promoted)

        # ── Phase 5: Merge micro-mounted back ──
        for child, parent in micro_mounted.items():
            effective = validated.get(parent, parent)
            validated[child] = effective

        # ── Phase 6: Cycle detection ──
        for start in list(validated):
            visited: set[str] = set()
            node = start
            while node in validated and node not in visited:
                visited.add(node)
                node = validated[node]
            if node in visited:
                cycle_edges = []
                cur = node
                while True:
                    p = validated[cur]
                    w = votes.get(cur, Counter()).get(p, 0)
                    cycle_edges.append((cur, p, w))
                    cur = p
                    if cur == node:
                        break
                weakest = min(cycle_edges, key=lambda e: e[2])
                del validated[weakest[0]]

        result = SkillResult(
            skill_name=self.name,
            parent_overrides=validated,
        )
        logger.info(
            "VoteResolver: %d parents resolved (%d core+regular, %d micro)",
            len(validated), len(raw), len(micro_mounted),
        )
        return result
