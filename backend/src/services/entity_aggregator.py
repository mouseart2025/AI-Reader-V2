"""Aggregate ChapterFact data into entity profiles.

ARCH-04: Aggregation is computed on demand, not persisted.
Uses LRU caching keyed by (novel_id, entity_name) with per-novel invalidation.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from functools import lru_cache
from typing import Any

from src.db.sqlite_db import get_connection
from src.models.chapter_fact import ChapterFact
from src.services.alias_resolver import build_alias_map
from src.services.domain_labels import (
    event_type_id,
    item_action_id,
    item_type_id,
    location_type_id,
    normalize_item_action,
    normalize_item_type,
    normalize_location_type,
    normalize_org_action,
    normalize_org_type,
    org_action_id,
    org_type_id,
)
from src.services.entity_identity import (
    choose_display_name,
    entity_identity_key,
    normalize_entity_name,
    same_entity_name,
)
from src.services.relation_utils import classify_relation_category, normalize_relation_type, relation_type_id
from src.models.entity_profiles import (
    AliasEntry,
    AppearanceEntry,
    AbilityEntry,
    EntitySummary,
    ItemAssociation,
    ItemFlowEntry,
    ItemProfile,
    LocationDescription,
    LocationEvent,
    LocationProfile,
    LocationVisitor,
    OrgMemberEvent,
    OrgProfile,
    OrgRelationEntry,
    PersonExperience,
    PersonProfile,
    RelationChain,
    RelationStage,
)

# ── Cache ─────────────────────────────────────────

logger = logging.getLogger(__name__)

_cache: dict[tuple[str, str, str], Any] = {}  # (novel_id, type, name) -> profile
_cache_order: list[tuple[str, str, str]] = []
_MAX_CACHE = 500


def _cache_get(key: tuple[str, str, str]) -> Any | None:
    return _cache.get(key)


def _cache_set(key: tuple[str, str, str], value: Any) -> None:
    if key in _cache:
        return
    _cache[key] = value
    _cache_order.append(key)
    while len(_cache_order) > _MAX_CACHE:
        old = _cache_order.pop(0)
        _cache.pop(old, None)


def invalidate_cache(novel_id: str) -> None:
    """Invalidate all cached profiles and alias map for a novel."""
    from src.services.alias_resolver import invalidate_alias_cache

    keys_to_remove = [k for k in _cache if k[0] == novel_id]
    for k in keys_to_remove:
        _cache.pop(k, None)
    _cache_order[:] = [k for k in _cache_order if k[0] != novel_id]
    invalidate_alias_cache(novel_id)


# ── Load ChapterFacts ─────────────────────────────


async def _load_chapter_facts(novel_id: str) -> list[ChapterFact]:
    """Load all ChapterFacts for a novel, ordered by chapter_id."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT cf.fact_json, c.chapter_num
            FROM chapter_facts cf
            JOIN chapters c ON cf.chapter_id = c.id AND cf.novel_id = c.novel_id
            WHERE cf.novel_id = ?
            ORDER BY c.chapter_num
            """,
            (novel_id,),
        )
        rows = await cursor.fetchall()
        facts: list[ChapterFact] = []
        for row in rows:
            data = json.loads(row["fact_json"])
            data["chapter_id"] = row["chapter_num"]
            data["novel_id"] = novel_id
            facts.append(ChapterFact.model_validate(data))
        return facts
    finally:
        await conn.close()


def _resolve_alias_name(name: str | None, alias_map: dict[str, str]) -> str:
    normalized = normalize_entity_name(name)
    if not normalized:
        return ""
    return normalize_entity_name(alias_map.get(normalized, alias_map.get(name or "", normalized)))


def _names_for_identity(alias_map: dict[str, str], target_name: str) -> set[str]:
    target_key = entity_identity_key(target_name)
    names: set[str] = set()
    normalized_target = normalize_entity_name(target_name)
    if normalized_target:
        names.add(normalized_target)
    for alias, canonical in alias_map.items():
        alias_name = normalize_entity_name(alias)
        canonical_name = normalize_entity_name(canonical)
        if entity_identity_key(alias_name) == target_key or entity_identity_key(canonical_name) == target_key:
            if alias_name:
                names.add(alias_name)
            if canonical_name:
                names.add(canonical_name)
    return names


# ── Person Aggregation ────────────────────────────


async def aggregate_person(novel_id: str, person_name: str) -> PersonProfile:
    cache_key = (novel_id, "person", person_name)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    facts = await _load_chapter_facts(novel_id)
    alias_map = await build_alias_map(novel_id)
    target_key = entity_identity_key(person_name)

    # Build the set of all names that resolve to this person
    name_set = _names_for_identity(alias_map, person_name)

    aliases: list[AliasEntry] = []
    seen_aliases: set[str] = set()
    # Collect raw appearances then merge duplicates after the loop
    _raw_appearances: list[tuple[int, str]] = []  # (chapter, description)
    abilities: list[AbilityEntry] = []
    # Collect raw relation entries, merge consecutive same-type stages after the loop
    _raw_relations: dict[str, list[tuple[int, str, str]]] = defaultdict(list)  # other -> [(ch, type, evidence)]
    items: list[ItemAssociation] = []
    experiences: list[PersonExperience] = []
    chapter_set: set[int] = set()
    first_chapter = 0

    # Pre-seed aliases from alias_map (names other than canonical)
    for alt_name in name_set:
        if alt_name != person_name and alt_name not in seen_aliases:
            seen_aliases.add(alt_name)
            aliases.append(AliasEntry(name=alt_name, first_chapter=0))

    for fact in facts:
        ch = fact.chapter_id

        # Characters
        for char in fact.characters:
            raw_name = normalize_entity_name(char.name)
            resolved_name = _resolve_alias_name(char.name, alias_map)
            if entity_identity_key(raw_name) != target_key and entity_identity_key(resolved_name) != target_key:
                continue
            chapter_set.add(ch)
            if not first_chapter:
                first_chapter = ch

            # Update first_chapter for alias entries seeded from alias_map
            if raw_name and raw_name != person_name:
                for ae in aliases:
                    if ae.name == raw_name and ae.first_chapter == 0:
                        ae.first_chapter = ch

            for alias in char.new_aliases:
                normalized_alias = normalize_entity_name(alias)
                if normalized_alias and normalized_alias not in seen_aliases and normalized_alias != person_name:
                    seen_aliases.add(normalized_alias)
                    aliases.append(AliasEntry(name=normalized_alias, first_chapter=ch))

            if char.appearance:
                _raw_appearances.append((ch, char.appearance))

            for ab in char.abilities_gained:
                abilities.append(
                    AbilityEntry(
                        chapter=ch,
                        dimension=ab.dimension,
                        name=ab.name,
                        description=ab.description,
                    )
                )

        # Relationships involving this person (any name in name_set)
        for rel in fact.relationships:
            a_resolved = _resolve_alias_name(rel.person_a, alias_map) or normalize_entity_name(rel.person_a)
            b_resolved = _resolve_alias_name(rel.person_b, alias_map) or normalize_entity_name(rel.person_b)
            if entity_identity_key(a_resolved) == target_key:
                other = b_resolved
            elif entity_identity_key(b_resolved) == target_key:
                other = a_resolved
            else:
                continue
            if entity_identity_key(other) == target_key:
                continue  # skip self-relations caused by alias
            _raw_relations[other].append((ch, rel.relation_type, rel.evidence))

        # Item events involving this person
        for ie in fact.item_events:
            actor = _resolve_alias_name(ie.actor, alias_map) if ie.actor else ""
            recipient = _resolve_alias_name(ie.recipient, alias_map) if ie.recipient else ""
            if entity_identity_key(actor) == target_key or entity_identity_key(recipient) == target_key:
                items.append(
                    ItemAssociation(
                        chapter=ch,
                        item_name=normalize_entity_name(ie.item_name),
                        item_type=normalize_item_type(ie.item_type),
                        item_type_id=item_type_id(ie.item_type),
                        action=normalize_item_action(ie.action),
                        action_id=item_action_id(ie.action),
                        description=ie.description or "",
                    )
                )

        # Events involving this person
        for ev in fact.events:
            resolved_participants = {
                _resolve_alias_name(p, alias_map) or normalize_entity_name(p)
                for p in ev.participants
            }
            if any(entity_identity_key(p) == target_key for p in resolved_participants):
                experiences.append(
                    PersonExperience(
                        chapter=ch,
                        summary=ev.summary,
                        type=ev.type,
                        type_id=event_type_id(ev.type),
                        location=ev.location,
                    )
                )

    # Merge relation stages with blood-relation locking + frequency voting:
    # Blood relations (family category) once established are not overridden.
    # For non-blood relations, frequency voting picks the dominant type.
    _BLOOD_CATEGORIES = frozenset({"family", "intimate"})

    relation_chains: list[RelationChain] = []
    for other, raw_stages in _raw_relations.items():
        # 1. Normalize all types and track frequencies
        type_counts: dict[str, int] = defaultdict(int)
        all_chapters: list[int] = []
        all_evidences: list[str] = []
        first_blood_type: str | None = None

        for ch, rtype, evidence in raw_stages:
            normalized = normalize_relation_type(rtype)
            cat = classify_relation_category(normalized)
            type_counts[normalized] += 1
            all_chapters.append(ch)
            if evidence and evidence not in all_evidences:
                all_evidences.append(evidence)

        # 2. Pick final type: smart blood lock > specificity boost > frequency voting
        _SPECIFIC_TYPES = {"结拜兄弟", "师兄弟", "师徒", "同门"}
        _GENERIC_TYPES = {"上下级", "朋友", "同伙", "社交"}
        _MIN_BLOOD_EVIDENCE = 3  # blood-lock needs ≥3 chapters to be reliable

        # Find the best blood/intimate type by frequency (not first-seen)
        blood_types = {t: c for t, c in type_counts.items()
                       if classify_relation_category(t) in _BLOOD_CATEGORIES}
        best_blood = max(blood_types.items(), key=lambda x: x[1]) if blood_types else None

        # "恋人"/"夫妻" can override weak blood types (宝玉↔黛玉: 兄妹1 vs 恋人58)
        intimate_types = {t: c for t, c in type_counts.items()
                         if classify_relation_category(t) == "intimate"}
        best_intimate = max(intimate_types.items(), key=lambda x: x[1]) if intimate_types else None

        if best_intimate and best_blood:
            # If intimate evidence overwhelms blood evidence (≥3x), prefer intimate
            if best_intimate[1] >= best_blood[1] * 3:
                best_blood = best_intimate

        if best_blood and best_blood[1] >= _MIN_BLOOD_EVIDENCE:
            chosen_type = best_blood[0]
            # Surname check: "兄弟"(blood) between different-surname characters
            # is almost certainly "结拜兄弟"(sworn)
            if chosen_type == "兄弟" and person_name and other:
                p_surname = person_name[0] if len(person_name) >= 2 else ""
                o_surname = other[0] if len(other) >= 2 else ""
                if p_surname and o_surname and p_surname != o_surname:
                    chosen_type = "结拜兄弟"
        elif best_blood:
            # Weak blood evidence (<3 chapters) — don't lock, fall through to
            # specificity boost / frequency voting (fixes 红孩儿↔行者 叔侄1 vs 敌对5)
            best_blood = None  # disable lock

        if not best_blood:
            # Check if a specific type exists with ≥2 chapters evidence
            specific_candidates = [
                (t, c) for t, c in type_counts.items()
                if t in _SPECIFIC_TYPES and c >= 1
            ]
            generic_winner = max(type_counts.items(), key=lambda x: x[1])
            if specific_candidates and generic_winner[0] in _GENERIC_TYPES:
                # Prefer the most frequent specific type
                chosen_type = max(specific_candidates, key=lambda x: x[1])[0]
            else:
                chosen_type = generic_winner[0]

        # 3. Build stages — keep consecutive stage merging for history,
        # but use chosen_type for category classification
        merged: list[RelationStage] = []
        for ch, rtype, evidence in raw_stages:
            normalized = normalize_relation_type(rtype)
            if merged and merged[-1].relation_type == normalized:
                merged[-1].chapters.append(ch)
                if evidence and evidence not in merged[-1].evidences:
                    merged[-1].evidences.append(evidence)
            else:
                merged.append(RelationStage(
                    chapters=[ch],
                    relation_type=normalized,
                    relation_type_id=relation_type_id(normalized),
                    evidences=[evidence] if evidence else [],
                ))

        category = classify_relation_category(chosen_type)
        relation_chains.append(RelationChain(
            other_person=other, stages=merged, category=category,
        ))

    # Merge duplicate appearances: group by description, collect chapters
    _appearance_map: dict[str, list[int]] = {}
    for ch, desc in _raw_appearances:
        _appearance_map.setdefault(desc, []).append(ch)
    appearances = [
        AppearanceEntry(chapters=sorted(chs), description=desc)
        for desc, chs in _appearance_map.items()
    ]
    # Sort by earliest chapter
    appearances.sort(key=lambda a: a.chapters[0])

    profile = PersonProfile(
        name=person_name,
        aliases=aliases,
        appearances=appearances,
        abilities=abilities,
        relations=relation_chains,
        items=items,
        experiences=experiences,
        stats={
            "chapter_count": len(chapter_set),
            "first_chapter": first_chapter,
            "last_chapter": max(chapter_set) if chapter_set else 0,
            "relation_count": len(relation_chains),
        },
    )

    # ── Profile quality check (Phase 1: pure rules, genre-aware) ──
    from src.services.profile_quality_checker import check_person_profile
    # Get genre hint for genre-aware quality checks
    _genre = None
    try:
        from src.db import world_structure_store
        _ws = await world_structure_store.load(novel_id)
        if _ws:
            _genre = _ws.novel_genre_hint
    except Exception:
        pass
    quality_findings = check_person_profile(profile, alias_map, genre=_genre)
    if quality_findings:
        logger.info(
            "Profile quality: %d findings for %s (%s)",
            len(quality_findings), person_name,
            ", ".join(f.finding_type for f in quality_findings),
        )

    _cache_set(cache_key, profile)
    return profile


# ── Location Aggregation ──────────────────────────


async def aggregate_location(novel_id: str, location_name: str) -> LocationProfile:
    cache_key = (novel_id, "location", location_name)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    facts = await _load_chapter_facts(novel_id)
    alias_map = await build_alias_map(novel_id)
    target_key = entity_identity_key(location_name)

    location_type = ""
    parent: str | None = None
    children_set: set[str] = set()
    descriptions: list[LocationDescription] = []
    visitor_map: dict[str, list[int]] = defaultdict(list)
    events: list[LocationEvent] = []
    chapter_set: set[int] = set()

    for fact in facts:
        ch = fact.chapter_id

        for loc in fact.locations:
            loc_canonical = _resolve_alias_name(loc.name, alias_map) or normalize_entity_name(loc.name)
            if entity_identity_key(loc_canonical) == target_key or same_entity_name(loc.name, location_name):
                chapter_set.add(ch)
                if not location_type and loc.type:
                    location_type = normalize_location_type(loc.type)
                if loc.parent and not parent:
                    parent = normalize_entity_name(loc.parent)
                if loc.description:
                    descriptions.append(
                        LocationDescription(chapter=ch, description=loc.description)
                    )
            # Build parent-child: if this location is someone's parent
            parent_canonical = normalize_entity_name(loc.parent) if loc.parent else None
            if entity_identity_key(parent_canonical) == target_key:
                children_set.add(loc_canonical)

        # Visitors: characters who were at this location
        for char in fact.characters:
            resolved_locs = {
                _resolve_alias_name(l, alias_map) or normalize_entity_name(l)
                for l in char.locations_in_chapter
            }
            if any(entity_identity_key(loc_name) == target_key for loc_name in resolved_locs):
                visitor_canonical = _resolve_alias_name(char.name, alias_map) or normalize_entity_name(char.name)
                visitor_map[visitor_canonical].append(ch)

        # Events at this location
        for ev in fact.events:
            ev_loc = normalize_entity_name(ev.location) if ev.location else None
            if entity_identity_key(ev_loc) == target_key:
                events.append(
                    LocationEvent(
                        chapter=ch,
                        summary=ev.summary,
                        type=ev.type,
                        type_id=event_type_id(ev.type),
                    )
                )

    # Override parent and children with authoritative WorldStructure data
    ws = None
    try:
        from src.db import world_structure_store
        ws = await world_structure_store.load(novel_id)
        if ws and ws.location_parents:
            authoritative_name = next(
                (name for name in ws.location_parents if entity_identity_key(name) == target_key),
                None,
            )
            authoritative = ws.location_parents.get(authoritative_name) if authoritative_name else None
            if authoritative:
                parent = authoritative
            # Collect children: all entries whose authoritative parent is this location
            ws_children = {
                child for child, p in ws.location_parents.items()
                if entity_identity_key(p) == target_key
            }
            children_set = children_set | ws_children
    except Exception:
        pass  # WorldStructure not available, use fact-based data

    resident_threshold = 3
    visitors = [
        LocationVisitor(
            name=name,
            chapters=chs,
            is_resident=len(chs) >= resident_threshold,
        )
        for name, chs in visitor_map.items()
    ]
    visitors.sort(key=lambda v: len(v.chapters), reverse=True)

    # Compute siblings: locations sharing the same parent
    siblings: list[str] = []
    if parent:
        try:
            if ws and ws.location_parents:
                siblings = sorted(
                    child for child, p in ws.location_parents.items()
                    if same_entity_name(p, parent) and not same_entity_name(child, location_name)
                )
        except Exception:
            pass

    profile = LocationProfile(
        name=location_name,
        location_type=location_type,
        location_type_id=location_type_id(location_type),
        parent=parent,
        children=sorted(children_set),
        siblings=siblings,
        descriptions=descriptions,
        visitors=visitors,
        events=events,
        stats={
            "chapter_count": len(chapter_set),
            "first_chapter": min(chapter_set) if chapter_set else 0,
            "visitor_count": len(visitors),
            "event_count": len(events),
        },
    )

    _cache_set(cache_key, profile)
    return profile


# ── Item Aggregation ──────────────────────────────


async def aggregate_item(novel_id: str, item_name: str) -> ItemProfile:
    cache_key = (novel_id, "item", item_name)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    facts = await _load_chapter_facts(novel_id)
    alias_map = await build_alias_map(novel_id)
    target_key = entity_identity_key(item_name)

    item_type = ""
    flow: list[ItemFlowEntry] = []
    related_set: set[str] = set()
    chapter_set: set[int] = set()

    for fact in facts:
        ch = fact.chapter_id
        chapter_items_in_event: list[str] = []

        for ie in fact.item_events:
            ie_canonical = normalize_entity_name(ie.item_name)
            if entity_identity_key(ie_canonical) == target_key:
                chapter_set.add(ch)
                if not item_type and ie.item_type:
                    item_type = normalize_item_type(ie.item_type)
                flow.append(
                    ItemFlowEntry(
                        chapter=ch,
                        action=normalize_item_action(ie.action),
                        action_id=item_action_id(ie.action),
                        actor=_resolve_alias_name(ie.actor, alias_map) if ie.actor else ie.actor,
                        recipient=_resolve_alias_name(ie.recipient, alias_map) if ie.recipient else ie.recipient,
                        description=ie.description or "",
                    )
                )
            chapter_items_in_event.append(ie_canonical)

        # Related items: other items appearing in chapters where this item appears
        if ch in chapter_set:
            for other_name in chapter_items_in_event:
                if entity_identity_key(other_name) != target_key:
                    related_set.add(other_name)

    profile = ItemProfile(
        name=item_name,
        item_type=item_type,
        item_type_id=item_type_id(item_type),
        flow=flow,
        related_items=sorted(related_set),
        stats={
            "chapter_count": len(chapter_set),
            "first_chapter": min(chapter_set) if chapter_set else 0,
            "flow_count": len(flow),
        },
    )

    _cache_set(cache_key, profile)
    return profile


# ── Organization Aggregation ──────────────────────


async def aggregate_org(novel_id: str, org_name: str) -> OrgProfile:
    cache_key = (novel_id, "org", org_name)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    facts = await _load_chapter_facts(novel_id)
    alias_map = await build_alias_map(novel_id)
    target_key = entity_identity_key(org_name)

    org_type = ""
    member_events: list[OrgMemberEvent] = []
    org_relations: list[OrgRelationEntry] = []
    chapter_set: set[int] = set()

    for fact in facts:
        ch = fact.chapter_id

        for oe in fact.org_events:
            oe_canonical = normalize_entity_name(oe.org_name)
            if entity_identity_key(oe_canonical) == target_key:
                chapter_set.add(ch)
                if not org_type and oe.org_type:
                    org_type = normalize_org_type(oe.org_type)
                if oe.member:
                    member_events.append(
                        OrgMemberEvent(
                            chapter=ch,
                            member=_resolve_alias_name(oe.member, alias_map),
                            role=oe.role,
                            action=normalize_org_action(oe.action),
                            action_id=org_action_id(oe.action),
                            description=oe.description or "",
                        )
                    )
                if oe.org_relation:
                    org_relations.append(
                        OrgRelationEntry(
                            chapter=ch,
                            other_org=normalize_entity_name(oe.org_relation.other_org),
                            relation_type=normalize_relation_type(oe.org_relation.type),
                            relation_type_id=relation_type_id(oe.org_relation.type),
                        )
                    )

    profile = OrgProfile(
        name=org_name,
        org_type=org_type,
        org_type_id=org_type_id(org_type),
        member_events=member_events,
        org_relations=org_relations,
        stats={
            "chapter_count": len(chapter_set),
            "first_chapter": min(chapter_set) if chapter_set else 0,
            "member_event_count": len(member_events),
        },
    )

    _cache_set(cache_key, profile)
    return profile


# ── Entity List ───────────────────────────────────


async def get_all_entities(novel_id: str) -> list[EntitySummary]:
    """Scan all ChapterFacts and return a deduplicated entity list.

    Uses alias_map to merge entities that are aliases of the same canonical name.
    """
    facts = await _load_chapter_facts(novel_id)
    alias_map = await build_alias_map(novel_id)

    entity_map: dict[tuple[str, str], set[int]] = defaultdict(set)
    entity_name_votes: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)

    def register(name: str | None, etype: str, chapter: int) -> None:
        resolved = _resolve_alias_name(name, alias_map) or normalize_entity_name(name)
        if not resolved:
            return
        key = entity_identity_key(resolved)
        if not key:
            return
        entity_map[(key, etype)].add(chapter)
        entity_name_votes[(key, etype)][resolved] += 1

    for fact in facts:
        ch = fact.chapter_id

        for char in fact.characters:
            register(char.name, "person", ch)

        for loc in fact.locations:
            register(loc.name, "location", ch)

        for ie in fact.item_events:
            register(ie.item_name, "item", ch)

        for oe in fact.org_events:
            register(oe.org_name, "org", ch)

        for nc in fact.new_concepts:
            register(nc.name, "concept", ch)

    # ── Entity type voting: resolve cross-type conflicts ──
    # When the same canonical name appears as multiple types (e.g., 林黛玉 as
    # both person and item), keep only the type with the most chapter appearances.
    _name_type_votes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for (name_key, etype), chapters in entity_map.items():
        _name_type_votes[name_key][etype] = len(chapters)

    _name_winning_type: dict[str, str] = {}
    # Type priority for tie-breaking: person > org > location > item > concept
    _TYPE_PRIORITY = {"person": 5, "org": 4, "location": 3, "item": 2, "concept": 1}
    for name_key, type_votes in _name_type_votes.items():
        if len(type_votes) > 1:
            # Pick type with most chapters; break ties by type priority
            winner = max(
                type_votes.items(),
                key=lambda x: (x[1], _TYPE_PRIORITY.get(x[0], 0)),
            )[0]
            _name_winning_type[name_key] = winner

    # ── Filter single-character entities ──
    # Non-person: always drop single-char (common nouns like 书/饭/茶)
    # Person: only keep if a longer entity shares it as surname prefix
    person_names = {
        choose_display_name(entity_name_votes[(name_key, etype)].keys(), entity_name_votes[(name_key, etype)])
        for (name_key, etype) in entity_map
        if etype == "person"
    }
    multi_char_persons = {name for name in person_names if len(name) >= 2}

    entities = []
    for (name_key, etype), chapters in entity_map.items():
        # Skip losing types in cross-type conflicts
        if name_key in _name_winning_type and etype != _name_winning_type[name_key]:
            continue
        name = choose_display_name(
            entity_name_votes[(name_key, etype)].keys(),
            entity_name_votes[(name_key, etype)],
        )
        if len(name) < 2:
            if etype != "person":
                continue  # drop single-char non-person
            # Person: check if any multi-char person shares this surname prefix
            if not any(p.startswith(name) for p in multi_char_persons):
                continue  # no matching full-name entity → drop
        entities.append(
            EntitySummary(
                name=name,
                type=etype,
                chapter_count=len(chapters),
                first_chapter=min(chapters) if chapters else 0,
            )
        )

    # Sort by chapter count descending, then alphabetically
    entities.sort(key=lambda e: (-e.chapter_count, e.name))
    return entities
