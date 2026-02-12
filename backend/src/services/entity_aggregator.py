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
    """Invalidate all cached profiles for a novel."""
    keys_to_remove = [k for k in _cache if k[0] == novel_id]
    for k in keys_to_remove:
        _cache.pop(k, None)
    _cache_order[:] = [k for k in _cache_order if k[0] != novel_id]


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

    aliases: list[AliasEntry] = []
    seen_aliases: set[str] = set()
    appearances: list[AppearanceEntry] = []
    abilities: list[AbilityEntry] = []
    relations_map: dict[str, list[RelationStage]] = defaultdict(list)
    items: list[ItemAssociation] = []
    experiences: list[PersonExperience] = []
    chapter_set: set[int] = set()
    first_chapter = 0

    for fact in facts:
        ch = fact.chapter_id

        # Characters
        for char in fact.characters:
            if char.name != person_name:
                continue
            chapter_set.add(ch)
            if not first_chapter:
                first_chapter = ch

            for alias in char.new_aliases:
                if alias not in seen_aliases:
                    seen_aliases.add(alias)
                    aliases.append(AliasEntry(name=alias, first_chapter=ch))

            if char.appearance:
                appearances.append(
                    AppearanceEntry(chapter=ch, description=char.appearance)
                )

            for ab in char.abilities_gained:
                abilities.append(
                    AbilityEntry(
                        chapter=ch,
                        dimension=ab.dimension,
                        name=ab.name,
                        description=ab.description,
                    )
                )

        # Relationships involving this person
        for rel in fact.relationships:
            if rel.person_a == person_name:
                other = rel.person_b
            elif rel.person_b == person_name:
                other = rel.person_a
            else:
                continue
            relations_map[other].append(
                RelationStage(
                    chapter=ch,
                    relation_type=rel.relation_type,
                    evidence=rel.evidence,
                )
            )

        # Item events involving this person
        for ie in fact.item_events:
            if ie.actor == person_name or ie.recipient == person_name:
                items.append(
                    ItemAssociation(
                        chapter=ch,
                        item_name=ie.item_name,
                        item_type=ie.item_type,
                        action=ie.action,
                        description=ie.description or "",
                    )
                )

        # Events involving this person
        for ev in fact.events:
            if person_name in ev.participants:
                experiences.append(
                    PersonExperience(
                        chapter=ch,
                        summary=ev.summary,
                        type=ev.type,
                        location=ev.location,
                    )
                )

    relation_chains = [
        RelationChain(other_person=other, stages=stages)
        for other, stages in relations_map.items()
    ]

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
            if loc.name == location_name:
                chapter_set.add(ch)
                if not location_type and loc.type:
                    location_type = loc.type
                if loc.parent and not parent:
                    parent = loc.parent
                if loc.description:
                    descriptions.append(
                        LocationDescription(chapter=ch, description=loc.description)
                    )
            # Build parent-child: if this location is someone's parent
            if loc.parent == location_name:
                children_set.add(loc.name)

        # Visitors: characters who were at this location
        for char in fact.characters:
            if location_name in char.locations_in_chapter:
                visitor_map[char.name].append(ch)

        # Events at this location
        for ev in fact.events:
            if ev.location == location_name:
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

    item_type = ""
    flow: list[ItemFlowEntry] = []
    related_set: set[str] = set()
    chapter_set: set[int] = set()

    for fact in facts:
        ch = fact.chapter_id
        chapter_items_in_event: list[str] = []

        for ie in fact.item_events:
            if ie.item_name == item_name:
                chapter_set.add(ch)
                if not item_type and ie.item_type:
                    item_type = ie.item_type
                flow.append(
                    ItemFlowEntry(
                        chapter=ch,
                        action=ie.action,
                        actor=ie.actor,
                        recipient=ie.recipient,
                        description=ie.description or "",
                    )
                )
            chapter_items_in_event.append(ie.item_name)

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

    org_type = ""
    member_events: list[OrgMemberEvent] = []
    org_relations: list[OrgRelationEntry] = []
    chapter_set: set[int] = set()

    for fact in facts:
        ch = fact.chapter_id

        for oe in fact.org_events:
            if oe.org_name == org_name:
                chapter_set.add(ch)
                if not org_type and oe.org_type:
                    org_type = oe.org_type
                if oe.member:
                    member_events.append(
                        OrgMemberEvent(
                            chapter=ch,
                            member=oe.member,
                            role=oe.role,
                            action=oe.action,
                            description=oe.description or "",
                        )
                    )
                if oe.org_relation:
                    org_relations.append(
                        OrgRelationEntry(
                            chapter=ch,
                            other_org=oe.org_relation.other_org,
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
    """Scan all ChapterFacts and return a deduplicated entity list."""
    facts = await _load_chapter_facts(novel_id)

    entity_map: dict[tuple[str, str], set[int]] = defaultdict(set)

    for fact in facts:
        ch = fact.chapter_id

        for char in fact.characters:
            entity_map[(char.name, "person")].add(ch)

        for loc in fact.locations:
            entity_map[(loc.name, "location")].add(ch)

        for ie in fact.item_events:
            entity_map[(ie.item_name, "item")].add(ch)

        for oe in fact.org_events:
            entity_map[(oe.org_name, "org")].add(ch)

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
