"""Conflict detection engine — detect setting inconsistencies from ChapterFacts.

Scans all chapter facts and identifies:
1. Character ability conflicts (abilities appearing then vanishing)
2. Relationship logic conflicts (incompatible relation changes)
3. Location hierarchy conflicts (same location, different parents)
4. Character death continuity errors (dead characters reappearing)
5. Direction conflicts (contradictory spatial directions for same location pair)
6. Distance conflicts (contradictory distance classes for same location pair)

All detection is rule-based (no LLM needed).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from src.db import chapter_fact_store
from src.services.alias_resolver import build_alias_map

logger = logging.getLogger(__name__)


@dataclass
class Conflict:
    """A detected conflict/inconsistency."""

    type: str  # "ability" | "relation" | "location" | "death" | "direction" | "distance"
    severity: str  # "严重" | "一般" | "提示"
    description: str
    chapters: list[int]
    entity: str  # primary entity involved
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "severity": self.severity,
            "description": self.description,
            "chapters": self.chapters,
            "entity": self.entity,
            "details": self.details,
        }


async def detect_conflicts(
    novel_id: str,
    chapter_start: int | None = None,
    chapter_end: int | None = None,
) -> list[dict]:
    """Run all conflict detection rules and return sorted conflicts."""
    all_facts = await chapter_fact_store.get_all_chapter_facts(novel_id)
    if not all_facts:
        return []

    # Parse facts
    parsed: list[tuple[int, dict]] = []
    for row in all_facts:
        ch_id = row.get("chapter_id", 0)
        if chapter_start and ch_id < chapter_start:
            continue
        if chapter_end and ch_id > chapter_end:
            continue
        try:
            fact = json.loads(row["fact_json"]) if isinstance(row["fact_json"], str) else row["fact_json"]
            parsed.append((ch_id, fact))
        except (json.JSONDecodeError, KeyError):
            continue

    if not parsed:
        return []

    # Build alias map
    alias_map = await build_alias_map(novel_id)

    conflicts: list[Conflict] = []

    # Run all detectors
    conflicts.extend(_detect_ability_conflicts(parsed, alias_map))
    conflicts.extend(_detect_relation_conflicts(parsed, alias_map))
    conflicts.extend(_detect_location_conflicts(parsed))
    conflicts.extend(_detect_death_continuity(parsed, alias_map))
    conflicts.extend(_detect_direction_conflicts(parsed, alias_map))
    conflicts.extend(_detect_distance_conflicts(parsed, alias_map))

    # Sort by severity
    severity_order = {"严重": 0, "一般": 1, "提示": 2}
    conflicts.sort(key=lambda c: (severity_order.get(c.severity, 3), c.chapters[0] if c.chapters else 0))

    return [c.to_dict() for c in conflicts]


def _resolve(name: str, alias_map: dict[str, str]) -> str:
    """Resolve alias to canonical name."""
    return alias_map.get(name, name) if alias_map else name


# ── Ability conflict detection ────────────────────


def _detect_ability_conflicts(
    parsed: list[tuple[int, dict]], alias_map: dict[str, str]
) -> list[Conflict]:
    """Detect ability-related inconsistencies.

    Rules:
    - Same dimension ability changes from X to Y then back to X (regression)
    - Ability dimension level appears to decrease
    """
    conflicts: list[Conflict] = []

    # Track abilities per character: {canonical_name: {dimension: [(chapter, name, desc)]}}
    ability_timeline: dict[str, dict[str, list[tuple[int, str, str]]]] = {}

    for ch_id, fact in parsed:
        for char in fact.get("characters", []):
            cname = _resolve(char.get("name", ""), alias_map)
            if not cname:
                continue

            for ab in char.get("abilities_gained", []):
                dim = ab.get("dimension", "")
                name = ab.get("name", "")
                desc = ab.get("description", "")
                if not dim or not name:
                    continue

                if cname not in ability_timeline:
                    ability_timeline[cname] = {}
                if dim not in ability_timeline[cname]:
                    ability_timeline[cname][dim] = []
                ability_timeline[cname][dim].append((ch_id, name, desc))

    # Check for dimension regressions (A → B → A pattern)
    for cname, dims in ability_timeline.items():
        for dim, timeline in dims.items():
            if len(timeline) < 3:
                continue
            for i in range(2, len(timeline)):
                _, name_prev, _ = timeline[i - 1]
                _, name_curr, _ = timeline[i]
                # Check if current matches any earlier entry (regression)
                for j in range(i - 2, -1, -1):
                    ch_early, name_early, _ = timeline[j]
                    if name_curr == name_early and name_prev != name_curr:
                        conflicts.append(Conflict(
                            type="ability",
                            severity="一般",
                            description=(
                                f"{cname} 的{dim}从「{name_early}」(第{ch_early}章)"
                                f"变为「{name_prev}」(第{timeline[i-1][0]}章)"
                                f"又回到「{name_curr}」(第{timeline[i][0]}章)，疑似回退"
                            ),
                            chapters=[ch_early, timeline[i - 1][0], timeline[i][0]],
                            entity=cname,
                            details={"dimension": dim, "values": [name_early, name_prev, name_curr]},
                        ))
                        break

    return conflicts


# ── Relationship conflict detection ───────────────


def _detect_relation_conflicts(
    parsed: list[tuple[int, dict]], alias_map: dict[str, str]
) -> list[Conflict]:
    """Detect relationship logic conflicts.

    Rules:
    - Hostile → Family transition without explanation (suspicious)
    - Relation type flip-flop (A→B→A pattern)
    """
    conflicts: list[Conflict] = []

    # Track relation timeline: {(person_a, person_b): [(chapter, type, evidence)]}
    relation_timeline: dict[tuple[str, str], list[tuple[int, str, str]]] = {}

    for ch_id, fact in parsed:
        for rel in fact.get("relationships", []):
            pa = _resolve(rel.get("person_a", ""), alias_map)
            pb = _resolve(rel.get("person_b", ""), alias_map)
            rtype = rel.get("relation_type", "")
            evidence = rel.get("evidence", "")
            if not pa or not pb or not rtype:
                continue

            key = (min(pa, pb), max(pa, pb))
            if key not in relation_timeline:
                relation_timeline[key] = []
            relation_timeline[key].append((ch_id, rtype, evidence))

    # Incompatible transitions
    _HOSTILE = {"敌对", "仇人", "对手", "仇敌"}
    _FAMILY = {"亲属", "父子", "母子", "兄弟", "姐妹", "夫妻", "父女", "母女"}

    for (pa, pb), timeline in relation_timeline.items():
        if len(timeline) < 2:
            continue

        # Check for hostile ↔ family flips
        for i in range(1, len(timeline)):
            ch_prev, type_prev, _ = timeline[i - 1]
            ch_curr, type_curr, _ = timeline[i]

            prev_hostile = any(h in type_prev for h in _HOSTILE)
            curr_family = any(f in type_curr for f in _FAMILY)
            prev_family = any(f in type_prev for f in _FAMILY)
            curr_hostile = any(h in type_curr for h in _HOSTILE)

            if prev_hostile and curr_family:
                conflicts.append(Conflict(
                    type="relation",
                    severity="一般",
                    description=(
                        f"{pa}与{pb}的关系从「{type_prev}」(第{ch_prev}章)"
                        f"变为「{type_curr}」(第{ch_curr}章)，敌对→亲属转变异常"
                    ),
                    chapters=[ch_prev, ch_curr],
                    entity=pa,
                    details={"other": pb, "from": type_prev, "to": type_curr},
                ))
            elif prev_family and curr_hostile:
                conflicts.append(Conflict(
                    type="relation",
                    severity="提示",
                    description=(
                        f"{pa}与{pb}的关系从「{type_prev}」(第{ch_prev}章)"
                        f"变为「{type_curr}」(第{ch_curr}章)，亲属→敌对（可能是叛变剧情）"
                    ),
                    chapters=[ch_prev, ch_curr],
                    entity=pa,
                    details={"other": pb, "from": type_prev, "to": type_curr},
                ))

        # Flip-flop detection (A→B→A)
        if len(timeline) >= 3:
            for i in range(2, len(timeline)):
                _, t0, _ = timeline[i - 2]
                _, t1, _ = timeline[i - 1]
                _, t2, _ = timeline[i]
                if t0 == t2 and t1 != t0:
                    conflicts.append(Conflict(
                        type="relation",
                        severity="提示",
                        description=(
                            f"{pa}与{pb}的关系反复：「{t0}」→「{t1}」→「{t2}」"
                            f"(第{timeline[i-2][0]}/{timeline[i-1][0]}/{timeline[i][0]}章)"
                        ),
                        chapters=[timeline[i - 2][0], timeline[i - 1][0], timeline[i][0]],
                        entity=pa,
                        details={"other": pb, "pattern": [t0, t1, t2]},
                    ))

    return conflicts


# ── Location hierarchy conflict detection ─────────

from src.utils.location_names import is_homonym_prone

# Minimum number of chapters the minority parent must appear in
# to be reported as a conflict.  A single-chapter minority is most
# likely LLM extraction noise or a genuinely different physical place.
_MIN_MINORITY_CHAPTERS = 2


def _detect_location_conflicts(parsed: list[tuple[int, dict]]) -> list[Conflict]:
    """Detect location hierarchy inconsistencies.

    Rules:
    - Same location has different parents in different chapters
    - Skips homonym-prone names (generic architectural terms like 夹道/后门)
    - Requires minority parent to appear in ≥2 chapters
    """
    conflicts: list[Conflict] = []

    # Track parent assignments: {location: [(chapter, parent)]}
    parent_timeline: dict[str, list[tuple[int, str]]] = {}

    for ch_id, fact in parsed:
        for loc in fact.get("locations", []):
            name = loc.get("name", "")
            parent = loc.get("parent")
            if not name or not parent:
                continue

            if name not in parent_timeline:
                parent_timeline[name] = []
            parent_timeline[name].append((ch_id, parent))

    for loc_name, assignments in parent_timeline.items():
        parents = set(p for _, p in assignments)
        if len(parents) <= 1:
            continue

        # Skip generic architectural terms — different parents are expected
        if is_homonym_prone(loc_name):
            continue

        # Multiple different parents
        parent_chapters: dict[str, list[int]] = {}
        for ch, p in assignments:
            if p not in parent_chapters:
                parent_chapters[p] = []
            parent_chapters[p].append(ch)

        sorted_parents = sorted(parent_chapters.items(), key=lambda x: len(x[1]), reverse=True)
        majority_parent = sorted_parents[0][0]

        for parent, chapters in sorted_parents[1:]:
            # Skip minorities that appear in only 1 chapter (likely noise)
            if len(chapters) < _MIN_MINORITY_CHAPTERS:
                continue
            conflicts.append(Conflict(
                type="location",
                severity="一般",
                description=(
                    f"地点「{loc_name}」的上级不一致："
                    f"多数章节为「{majority_parent}」，但第{'/'.join(str(c) for c in chapters[:3])}章为「{parent}」"
                ),
                chapters=chapters[:3],
                entity=loc_name,
                details={
                    "majority_parent": majority_parent,
                    "conflict_parent": parent,
                },
            ))

    return conflicts


# ── Death continuity detection ────────────────────


def _detect_death_continuity(
    parsed: list[tuple[int, dict]], alias_map: dict[str, str]
) -> list[Conflict]:
    """Detect characters who die but reappear later.

    Rules:
    - Character has '阵亡' org_event, but appears as participant in later events
    - Character mentioned as dead but acts in later chapters
    """
    conflicts: list[Conflict] = []

    # Track death events
    death_chapter: dict[str, int] = {}  # canonical_name → chapter of death

    for ch_id, fact in parsed:
        for org_ev in fact.get("org_events", []):
            member = org_ev.get("member", "")
            action = org_ev.get("action", "")
            if member and action == "阵亡":
                cname = _resolve(member, alias_map)
                if cname and cname not in death_chapter:
                    death_chapter[cname] = ch_id

    if not death_chapter:
        return conflicts

    # Check for post-death appearances
    for ch_id, fact in parsed:
        for char in fact.get("characters", []):
            cname = _resolve(char.get("name", ""), alias_map)
            if cname in death_chapter and ch_id > death_chapter[cname]:
                # This character died earlier but appears again
                conflicts.append(Conflict(
                    type="death",
                    severity="严重",
                    description=(
                        f"角色「{cname}」在第{death_chapter[cname]}章阵亡，"
                        f"但在第{ch_id}章再次出现"
                    ),
                    chapters=[death_chapter[cname], ch_id],
                    entity=cname,
                    details={"death_chapter": death_chapter[cname], "reappear_chapter": ch_id},
                ))
                # Only report once per character
                del death_chapter[cname]
                break

    return conflicts


# ── Direction conflict detection ─────────────────


# Opposite direction pairs — if source→target is "north_of" in ch5
# but "south_of" in ch20, that's a contradiction.
_OPPOSITE_DIRECTIONS: dict[str, str] = {
    "north_of": "south_of",
    "south_of": "north_of",
    "east_of": "west_of",
    "west_of": "east_of",
    "northeast_of": "southwest_of",
    "southwest_of": "northeast_of",
    "northwest_of": "southeast_of",
    "southeast_of": "northwest_of",
}

_DIR_LABEL: dict[str, str] = {
    "north_of": "北方", "south_of": "南方",
    "east_of": "东方", "west_of": "西方",
    "northeast_of": "东北方", "northwest_of": "西北方",
    "southeast_of": "东南方", "southwest_of": "西南方",
}


def _detect_direction_conflicts(
    parsed: list[tuple[int, dict]], alias_map: dict[str, str]
) -> list[Conflict]:
    """Detect contradictory spatial direction claims for the same location pair.

    A conflict is raised when chapter A says "X is north_of Y" but chapter B
    says "X is south_of Y" (or the reverse pair claims opposite directions).
    """
    conflicts: list[Conflict] = []

    # {(canonical_source, canonical_target): [(chapter, direction_value)]}
    pair_directions: dict[tuple[str, str], list[tuple[int, str]]] = {}

    for ch_id, fact in parsed:
        for sr in fact.get("spatial_relationships", []):
            if sr.get("relation_type") != "direction":
                continue
            src = _resolve(sr.get("source", ""), alias_map)
            tgt = _resolve(sr.get("target", ""), alias_map)
            value = sr.get("value", "")
            if not src or not tgt or not value:
                continue

            # Normalize pair order: always store (min, max) with adjusted direction
            if src > tgt:
                src, tgt = tgt, src
                value = _OPPOSITE_DIRECTIONS.get(value, value)

            key = (src, tgt)
            if key not in pair_directions:
                pair_directions[key] = []
            pair_directions[key].append((ch_id, value))

    for (src, tgt), records in pair_directions.items():
        if len(records) < 2:
            continue

        # Group by direction value
        dir_chapters: dict[str, list[int]] = {}
        for ch, d in records:
            if d not in dir_chapters:
                dir_chapters[d] = []
            dir_chapters[d].append(ch)

        if len(dir_chapters) <= 1:
            continue

        # Check for opposite pairs
        reported: set[tuple[str, str]] = set()
        for d1, chs1 in dir_chapters.items():
            opposite = _OPPOSITE_DIRECTIONS.get(d1)
            if opposite and opposite in dir_chapters and (d1, opposite) not in reported:
                reported.add((d1, opposite))
                reported.add((opposite, d1))
                chs2 = dir_chapters[opposite]
                label1 = _DIR_LABEL.get(d1, d1)
                label2 = _DIR_LABEL.get(opposite, opposite)
                conflicts.append(Conflict(
                    type="direction",
                    severity="一般",
                    description=(
                        f"「{src}」相对「{tgt}」的方向矛盾："
                        f"第{'/'.join(str(c) for c in chs1[:3])}章为{label1}，"
                        f"第{'/'.join(str(c) for c in chs2[:3])}章为{label2}"
                    ),
                    chapters=sorted(set(chs1[:2] + chs2[:2])),
                    entity=src,
                    details={
                        "other": tgt,
                        "direction_a": d1,
                        "direction_b": opposite,
                        "chapters_a": chs1[:3],
                        "chapters_b": chs2[:3],
                    },
                ))

    return conflicts


# ── Distance conflict detection ──────────────────


# Distance class ordinal scale
_DISTANCE_ORDINAL: dict[str, int] = {
    "near": 0,
    "medium": 1,
    "far": 2,
    "very_far": 3,
}

_DC_LABEL: dict[str, str] = {"near": "近", "medium": "中等", "far": "远", "very_far": "极远"}

# Minimum ordinal gap to report as conflict (2 = "near" vs "far" or worse)
_MIN_DISTANCE_GAP = 2


def _detect_distance_conflicts(
    parsed: list[tuple[int, dict]], alias_map: dict[str, str]
) -> list[Conflict]:
    """Detect contradictory distance claims for the same location pair.

    A conflict is raised when the distance_class for a pair differs by ≥2
    ordinal steps (e.g., "near" vs "far", or "near" vs "very_far").
    Single-step changes ("near" → "medium") are tolerated as narrative
    variation or measurement imprecision.
    """
    conflicts: list[Conflict] = []

    # {(canonical_a, canonical_b): [(chapter, distance_class)]}
    pair_distances: dict[tuple[str, str], list[tuple[int, str]]] = {}

    for ch_id, fact in parsed:
        for sr in fact.get("spatial_relationships", []):
            if sr.get("relation_type") != "distance":
                continue
            src = _resolve(sr.get("source", ""), alias_map)
            tgt = _resolve(sr.get("target", ""), alias_map)
            dc = sr.get("distance_class") or ""
            if not src or not tgt or dc not in _DISTANCE_ORDINAL:
                continue

            # Normalize pair order
            key = (min(src, tgt), max(src, tgt))
            if key not in pair_distances:
                pair_distances[key] = []
            pair_distances[key].append((ch_id, dc))

    for (loc_a, loc_b), records in pair_distances.items():
        if len(records) < 2:
            continue

        # Group by distance_class
        dc_chapters: dict[str, list[int]] = {}
        for ch, dc in records:
            if dc not in dc_chapters:
                dc_chapters[dc] = []
            dc_chapters[dc].append(ch)

        if len(dc_chapters) <= 1:
            continue

        # Find pairs with significant ordinal gap
        classes = list(dc_chapters.keys())
        reported: set[tuple[str, str]] = set()
        for i, c1 in enumerate(classes):
            for c2 in classes[i + 1:]:
                gap = abs(_DISTANCE_ORDINAL[c1] - _DISTANCE_ORDINAL[c2])
                if gap < _MIN_DISTANCE_GAP:
                    continue
                pair_key = (min(c1, c2), max(c1, c2))
                if pair_key in reported:
                    continue
                reported.add(pair_key)

                chs1 = dc_chapters[c1]
                chs2 = dc_chapters[c2]
                label1 = _DC_LABEL.get(c1, c1)
                label2 = _DC_LABEL.get(c2, c2)
                severity = "一般" if gap >= 3 else "提示"
                conflicts.append(Conflict(
                    type="distance",
                    severity=severity,
                    description=(
                        f"「{loc_a}」与「{loc_b}」的距离矛盾："
                        f"第{'/'.join(str(c) for c in chs1[:3])}章为{label1}，"
                        f"第{'/'.join(str(c) for c in chs2[:3])}章为{label2}"
                    ),
                    chapters=sorted(set(chs1[:2] + chs2[:2])),
                    entity=loc_a,
                    details={
                        "other": loc_b,
                        "distance_a": c1,
                        "distance_b": c2,
                        "gap": gap,
                    },
                ))

    return conflicts
