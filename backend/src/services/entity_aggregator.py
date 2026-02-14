"""Aggregate ChapterFact data into entity profiles.

ARCH-04: Aggregation is computed on demand, not persisted.
Uses LRU caching keyed by (novel_id, entity_name) with per-novel invalidation.
"""

from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache
from typing import Any

from src.db.sqlite_db import get_connection
from src.models.chapter_fact import ChapterFact
from src.services.alias_resolver import build_alias_map
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

_cache: dict[tuple[str, str, str], Any] = {}  # (novel_id, type, name) -> profile
_cache_order: list[tuple[str, str, str]] = []
_MAX_CACHE = 200


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


# ── Person Aggregation ────────────────────────────


async def aggregate_person(novel_id: str, person_name: str) -> PersonProfile:
    cache_key = (novel_id, "person", person_name)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    facts = await _load_chapter_facts(novel_id)
    alias_map = await build_alias_map(novel_id)

    # Build the set of all names that resolve to this person
    name_set = {person_name}
    for alias, canonical in alias_map.items():
        if canonical == person_name:
            name_set.add(alias)

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
            if char.name not in name_set:
                continue
            chapter_set.add(ch)
            if not first_chapter:
                first_chapter = ch

            # Update first_chapter for alias entries seeded from alias_map
            if char.name != person_name:
                for ae in aliases:
                    if ae.name == char.name and ae.first_chapter == 0:
                        ae.first_chapter = ch

            for alias in char.new_aliases:
                if alias not in seen_aliases and alias != person_name:
                    seen_aliases.add(alias)
                    aliases.append(AliasEntry(name=alias, first_chapter=ch))

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
            a_resolved = alias_map.get(rel.person_a, rel.person_a)
            b_resolved = alias_map.get(rel.person_b, rel.person_b)
            if a_resolved == person_name:
                other = b_resolved
            elif b_resolved == person_name:
                other = a_resolved
            else:
                continue
            if other == person_name:
                continue  # skip self-relations caused by alias
            _raw_relations[other].append((ch, rel.relation_type, rel.evidence))

        # Item events involving this person
        for ie in fact.item_events:
            actor = alias_map.get(ie.actor, ie.actor) if ie.actor else ""
            recipient = alias_map.get(ie.recipient, ie.recipient) if ie.recipient else ""
            if actor == person_name or recipient == person_name:
                items.append(
                    ItemAssociation(
                        chapter=ch,
                        item_name=alias_map.get(ie.item_name, ie.item_name),
                        item_type=ie.item_type,
                        action=ie.action,
                        description=ie.description or "",
                    )
                )

        # Events involving this person
        for ev in fact.events:
            resolved_participants = {alias_map.get(p, p) for p in ev.participants}
            if person_name in resolved_participants:
                experiences.append(
                    PersonExperience(
                        chapter=ch,
                        summary=ev.summary,
                        type=ev.type,
                        location=ev.location,
                    )
                )

    # Merge consecutive same-type relation stages:
    # [(ch3,"师徒"), (ch5,"师徒"), (ch8,"弟子"), (ch9,"师徒")]
    # → [RelationStage(chapters=[3,5], type="师徒"), RS(chapters=[8], type="弟子"), RS(chapters=[9], type="师徒")]
    relation_chains: list[RelationChain] = []
    for other, raw_stages in _raw_relations.items():
        merged: list[RelationStage] = []
        for ch, rtype, evidence in raw_stages:
            if merged and merged[-1].relation_type == rtype:
                merged[-1].chapters.append(ch)
                # Keep the longest evidence
                if len(evidence) > len(merged[-1].evidence):
                    merged[-1].evidence = evidence
            else:
                merged.append(RelationStage(
                    chapters=[ch],
                    relation_type=rtype,
                    evidence=evidence,
                ))
        relation_chains.append(RelationChain(other_person=other, stages=merged))

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

    # Build the set of all names that resolve to this location
    name_set = {location_name}
    for alias, canonical in alias_map.items():
        if canonical == location_name:
            name_set.add(alias)

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
            loc_canonical = alias_map.get(loc.name, loc.name)
            if loc_canonical == location_name:
                chapter_set.add(ch)
                if not location_type and loc.type:
                    location_type = loc.type
                if loc.parent and not parent:
                    parent = alias_map.get(loc.parent, loc.parent)
                if loc.description:
                    descriptions.append(
                        LocationDescription(chapter=ch, description=loc.description)
                    )
            # Build parent-child: if this location is someone's parent
            parent_canonical = alias_map.get(loc.parent, loc.parent) if loc.parent else None
            if parent_canonical == location_name:
                children_set.add(loc_canonical)

        # Visitors: characters who were at this location
        for char in fact.characters:
            resolved_locs = {alias_map.get(l, l) for l in char.locations_in_chapter}
            if location_name in resolved_locs:
                visitor_canonical = alias_map.get(char.name, char.name)
                visitor_map[visitor_canonical].append(ch)

        # Events at this location
        for ev in fact.events:
            ev_loc = alias_map.get(ev.location, ev.location) if ev.location else None
            if ev_loc == location_name:
                events.append(
                    LocationEvent(chapter=ch, summary=ev.summary, type=ev.type)
                )

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

    profile = LocationProfile(
        name=location_name,
        location_type=location_type,
        parent=parent,
        children=sorted(children_set),
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

    # Build the set of all names that resolve to this item
    name_set = {item_name}
    for alias, canonical in alias_map.items():
        if canonical == item_name:
            name_set.add(alias)

    item_type = ""
    flow: list[ItemFlowEntry] = []
    related_set: set[str] = set()
    chapter_set: set[int] = set()

    for fact in facts:
        ch = fact.chapter_id
        chapter_items_in_event: list[str] = []

        for ie in fact.item_events:
            ie_canonical = alias_map.get(ie.item_name, ie.item_name)
            if ie_canonical == item_name:
                chapter_set.add(ch)
                if not item_type and ie.item_type:
                    item_type = ie.item_type
                flow.append(
                    ItemFlowEntry(
                        chapter=ch,
                        action=ie.action,
                        actor=alias_map.get(ie.actor, ie.actor) if ie.actor else ie.actor,
                        recipient=alias_map.get(ie.recipient, ie.recipient) if ie.recipient else ie.recipient,
                        description=ie.description or "",
                    )
                )
            chapter_items_in_event.append(ie_canonical)

        # Related items: other items appearing in chapters where this item appears
        if ch in chapter_set:
            for other_name in chapter_items_in_event:
                if other_name != item_name:
                    related_set.add(other_name)

    profile = ItemProfile(
        name=item_name,
        item_type=item_type,
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

    # Build the set of all names that resolve to this org
    name_set = {org_name}
    for alias, canonical in alias_map.items():
        if canonical == org_name:
            name_set.add(alias)

    org_type = ""
    member_events: list[OrgMemberEvent] = []
    org_relations: list[OrgRelationEntry] = []
    chapter_set: set[int] = set()

    for fact in facts:
        ch = fact.chapter_id

        for oe in fact.org_events:
            oe_canonical = alias_map.get(oe.org_name, oe.org_name)
            if oe_canonical == org_name:
                chapter_set.add(ch)
                if not org_type and oe.org_type:
                    org_type = oe.org_type
                if oe.member:
                    member_events.append(
                        OrgMemberEvent(
                            chapter=ch,
                            member=alias_map.get(oe.member, oe.member),
                            role=oe.role,
                            action=oe.action,
                            description=oe.description or "",
                        )
                    )
                if oe.org_relation:
                    org_relations.append(
                        OrgRelationEntry(
                            chapter=ch,
                            other_org=alias_map.get(oe.org_relation.other_org, oe.org_relation.other_org),
                            relation_type=oe.org_relation.type,
                        )
                    )

    profile = OrgProfile(
        name=org_name,
        org_type=org_type,
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

    for fact in facts:
        ch = fact.chapter_id

        for char in fact.characters:
            canonical = alias_map.get(char.name, char.name)
            entity_map[(canonical, "person")].add(ch)

        for loc in fact.locations:
            canonical = alias_map.get(loc.name, loc.name)
            entity_map[(canonical, "location")].add(ch)

        for ie in fact.item_events:
            canonical = alias_map.get(ie.item_name, ie.item_name)
            entity_map[(canonical, "item")].add(ch)

        for oe in fact.org_events:
            canonical = alias_map.get(oe.org_name, oe.org_name)
            entity_map[(canonical, "org")].add(ch)

        for nc in fact.new_concepts:
            entity_map[(nc.name, "concept")].add(ch)

    entities = [
        EntitySummary(
            name=name,
            type=etype,
            chapter_count=len(chapters),
            first_chapter=min(chapters) if chapters else 0,
        )
        for (name, etype), chapters in entity_map.items()
    ]

    # Sort by chapter count descending, then alphabetically
    entities.sort(key=lambda e: (-e.chapter_count, e.name))
    return entities
