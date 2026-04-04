"""VoteBuilder — GeoSkill that builds parent votes from chapter facts.

Extracted from WorldStructureAgent._rebuild_parent_votes().
Reads chapter_facts from DB and produces vote counts for each location.
Also builds frequency map, chapter settings, and location-chapter mapping.

This skill always succeeds (no LLM dependency).
"""

from __future__ import annotations

import json
import logging
from collections import Counter

from src.extraction.fact_validator import _is_generic_location
from src.services.geo_skills.base import GeoSkill
from src.services.geo_skills.snapshot import HierarchySnapshot, SkillResult
from src.services.world_structure_agent import TIER_ORDER, _get_suffix_rank

logger = logging.getLogger(__name__)


class VoteBuilder(GeoSkill):
    """Build parent votes from chapter facts."""

    def __init__(self, novel_id: str):
        self.novel_id = novel_id

    @property
    def name(self) -> str:
        return "投票构建"

    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        from src.db.sqlite_db import get_connection

        conn = await get_connection()
        try:
            cursor = await conn.execute(
                "SELECT fact_json FROM chapter_facts WHERE novel_id = ? ORDER BY chapter_id",
                (self.novel_id,),
            )
            rows = await cursor.fetchall()
        finally:
            await conn.close()

        if not rows:
            return SkillResult.empty(self.name, "No chapter facts")

        tiers = snapshot.location_tiers
        uber_root = self._find_uber_root(snapshot.location_parents)

        # ── Phase 1: Build frequency, chapter settings, location chapters ──
        loc_freq: Counter = Counter()
        chapter_settings: dict[int, str] = {}
        location_chapters: dict[str, list[int]] = {}

        for row in rows:
            data = json.loads(row["fact_json"])
            ch_id = data.get("chapter_id", 0)
            locations = data.get("locations", [])
            for loc in locations:
                name = loc.get("name", "")
                if name:
                    loc_freq[name] += 1
                    location_chapters.setdefault(name, []).append(ch_id)
            # Primary setting
            settings = [
                l for l in locations
                if l.get("role") == "setting" and l.get("name")
                and (not _is_generic_location(l["name"]) or l["name"] == uber_root)
            ]
            if settings:
                best_rank, best_name = 999, ""
                for loc in settings:
                    suf = _get_suffix_rank(loc["name"])
                    rank = suf if suf is not None else TIER_ORDER.get(
                        tiers.get(loc["name"], "city"), 4)
                    if rank < best_rank:
                        best_rank = rank
                        best_name = loc["name"]
                if best_name:
                    chapter_settings[ch_id] = best_name
            elif locations:
                for loc in locations:
                    ln = loc.get("name", "")
                    if ln and (not _is_generic_location(ln) or ln == uber_root):
                        chapter_settings[ch_id] = ln
                        break

        # ── Phase 2: Build votes from chapter facts ──
        votes: dict[str, Counter] = {}

        # Baseline injection (existing parents, weight=1)
        # Only inject when pair is supported by chapter facts
        cf_pairs: set[tuple[str, str]] = set()
        children_with_evidence: set[str] = set()
        for row in rows:
            data = json.loads(row["fact_json"])
            for loc in data.get("locations", []):
                name = loc.get("name", "")
                parent = loc.get("parent", "")
                if name and parent and parent != "None" and name != parent:
                    cf_pairs.add((name, parent))
                    children_with_evidence.add(name)
            for sr in data.get("spatial_relationships", []):
                if sr.get("relation_type") == "contains":
                    src, tgt = sr.get("source", ""), sr.get("target", "")
                    if src and tgt:
                        cf_pairs.add((tgt, src))
                        children_with_evidence.add(tgt)

        known_locs = set(tiers.keys())
        if snapshot.location_parents:
            baseline_injected = 0
            for child, parent in snapshot.location_parents.items():
                if parent not in known_locs and parent != uber_root:
                    continue
                if child in children_with_evidence and (child, parent) not in cf_pairs:
                    continue
                votes.setdefault(child, Counter())[parent] += 1
                baseline_injected += 1
            logger.info("Baseline: %d parents injected", baseline_injected)

        # Peer pairs
        peer_pairs: set[frozenset[str]] = set()
        for row in rows:
            data = json.loads(row["fact_json"])
            for loc in data.get("locations", []):
                peers = loc.get("peers")
                name = loc.get("name", "")
                if peers and name:
                    for peer in peers:
                        if peer and peer != name:
                            peer_pairs.add(frozenset({name, peer}))

        # Chapter fact votes
        total_chapters = max(len(rows), 1)
        spatial_neighbors: list[tuple[str, str]] = []

        for chapter_idx, row in enumerate(rows):
            data = json.loads(row["fact_json"])
            chapter_weight = 1.0 + 0.5 * (chapter_idx / total_chapters)

            for loc in data.get("locations", []):
                parent = loc.get("parent")
                name = loc.get("name", "")
                if parent and name and name != parent:
                    if (_is_generic_location(name) and name != uber_root) or \
                       (_is_generic_location(parent) and parent != uber_root):
                        continue
                    pair_key = frozenset({name, parent})
                    w = 0.33 if pair_key in peer_pairs else 1.0
                    votes.setdefault(name, Counter())[parent] += w * chapter_weight

            for sr in data.get("spatial_relationships", []):
                rel = sr.get("relation_type", "")
                src, tgt = sr.get("source", ""), sr.get("target", "")
                if not src or not tgt or src == tgt:
                    continue
                if (_is_generic_location(src) and src != uber_root) or \
                   (_is_generic_location(tgt) and tgt != uber_root):
                    continue
                if rel in ("adjacent", "direction", "in_between"):
                    spatial_neighbors.append((src, tgt))
                    continue
                if rel != "contains":
                    continue
                weight = {"high": 2, "medium": 1, "low": 1}.get(
                    sr.get("confidence", "low"), 1)
                # Direction validation
                s_suf = _get_suffix_rank(src)
                t_suf = _get_suffix_rank(tgt)
                s_rank = s_suf if s_suf is not None else TIER_ORDER.get(tiers.get(src, "city"), 4)
                t_rank = t_suf if t_suf is not None else TIER_ORDER.get(tiers.get(tgt, "city"), 4)
                if s_rank > t_rank:
                    src, tgt = tgt, src
                votes.setdefault(tgt, Counter())[src] += weight * chapter_weight

            # Primary setting inference
            locations = data.get("locations", [])
            setting_candidates = [
                l for l in locations
                if l.get("role") == "setting" and l.get("name")
                and (not _is_generic_location(l["name"]) or l["name"] == uber_root)
            ]
            primary = None
            if setting_candidates:
                best_rank = 999
                for loc in setting_candidates:
                    suf = _get_suffix_rank(loc["name"])
                    rank = suf if suf is not None else TIER_ORDER.get(
                        tiers.get(loc["name"], "city"), 4)
                    if rank < best_rank:
                        best_rank = rank
                        primary = loc["name"]
            elif locations:
                for loc in locations:
                    ln = loc.get("name", "")
                    if ln and (not _is_generic_location(ln) or ln == uber_root):
                        primary = ln
                        break

            if primary and not self._is_realm(primary):
                p_suf = _get_suffix_rank(primary)
                p_rank = p_suf if p_suf is not None else TIER_ORDER.get(
                    tiers.get(primary, "city"), 4)
                for loc in locations:
                    ln = loc.get("name", "")
                    if ln == primary or loc.get("parent"):
                        continue
                    if not ln or (_is_generic_location(ln) and ln != uber_root):
                        continue
                    if loc.get("role") in ("referenced", "boundary"):
                        continue
                    c_suf = _get_suffix_rank(ln)
                    c_rank = c_suf if c_suf is not None else TIER_ORDER.get(
                        tiers.get(ln, "city"), 4)
                    if c_rank <= p_rank:
                        continue
                    votes.setdefault(ln, Counter())[primary] += 2

        # Spatial neighbor propagation
        if spatial_neighbors:
            for _ in range(2):
                propagated = 0
                for a, b in spatial_neighbors:
                    for from_l, to_l in [(a, b), (b, a)]:
                        fv = votes.get(from_l)
                        if not fv:
                            continue
                        best_p, best_c = fv.most_common(1)[0]
                        if best_p and best_p != to_l and best_c >= 2:
                            if votes.get(to_l, Counter()).get(best_p, 0) == 0:
                                votes.setdefault(to_l, Counter())[best_p] += 1
                                propagated += 1
                if propagated == 0:
                    break

        # Uber-root vote capping
        if uber_root:
            for loc_name, counter in votes.items():
                if uber_root in counter and len(counter) > 1:
                    if counter[uber_root] > 2:
                        counter[uber_root] = 2

        n_core = sum(1 for c in loc_freq.values() if c >= 10)
        n_reg = sum(1 for c in loc_freq.values() if 3 <= c <= 9)
        n_micro = sum(1 for c in loc_freq.values() if c <= 2)
        logger.info(
            "VoteBuilder: %d votes, freq=%d core + %d regular + %d micro",
            len(votes), n_core, n_reg, n_micro,
        )

        # Return result — votes go into snapshot, freq/settings as metadata
        result = SkillResult(skill_name=self.name, new_votes=votes)
        # Store frequency data in a way the snapshot can use
        # We return a special snapshot instead of applying to existing
        result._extra = {
            "location_frequencies": loc_freq,
            "chapter_settings": chapter_settings,
            "location_chapters": location_chapters,
            "peer_pairs": peer_pairs,
        }
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
    def _is_realm(name: str) -> bool:
        return any(kw in name for kw in "幻梦仙灵冥虚魔")
