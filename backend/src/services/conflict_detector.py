"""Conflict detection engine — detect setting inconsistencies from ChapterFacts.

Scans all chapter facts and identifies:
1. Character ability conflicts (abilities appearing then vanishing)
2. Relationship logic conflicts (incompatible relation changes)
3. Location hierarchy conflicts (same location, different parents)
4. Character death continuity errors (dead characters reappearing)

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

    type: str  # "ability" | "relation" | "location" | "death" | "attribute"
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

# Architectural suffixes — single chars representing building parts/rooms/passages.
# Locations composed purely of these are inherently ambiguous (e.g. "夹道", "后门")
# and can exist in multiple distinct buildings across a novel.
_ARCH_SUFFIXES = frozenset(
    "门道廊厅堂殿阁楼房室间院墙窗"
    "阶梯井亭台榭轩斋"
)

# Explicit homonym-prone names — common architectural terms that appear
# in many different buildings (e.g. 荣国府's 夹道 vs 甄家's 夹道).
_HOMONYM_PRONE_NAMES = frozenset({
    # Passages / entrances
    "夹道", "角门", "后门", "侧门", "正门", "大门", "二门", "垂花门",
    "甬道", "走廊", "过道", "回廊", "穿堂", "抄手游廊",
    # Rooms / chambers
    "上房", "正房", "正室", "里间", "外间", "外间房", "内室", "内房",
    "厢房", "偏房", "耳房", "暖阁", "套间",
    "书房", "卧房", "卧室", "厨房", "柴房",
    # Halls
    "前厅", "后堂", "正厅", "大厅", "花厅", "偏厅", "中堂",
    "配殿", "偏殿", "抱厦",
    # Outdoor spaces
    "后院", "前院", "院子", "花园", "后花园",
    # Generic
    "仓库", "马厩", "马棚", "门房", "倒座",
})

# Minimum number of chapters the minority parent must appear in
# to be reported as a conflict.  A single-chapter minority is most
# likely LLM extraction noise or a genuinely different physical place.
_MIN_MINORITY_CHAPTERS = 2


def _is_homonym_prone(name: str) -> bool:
    """Return True if the location name is a generic architectural term
    that commonly exists in multiple distinct buildings."""
    if name in _HOMONYM_PRONE_NAMES:
        return True
    # Short names (≤2 chars) composed entirely of architectural suffixes
    if len(name) <= 2 and all(c in _ARCH_SUFFIXES for c in name):
        return True
    return False


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
        if _is_homonym_prone(loc_name):
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
