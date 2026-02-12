"""Visualization data aggregation from ChapterFacts.

Provides data for 4 views: graph, map, timeline, factions.
All functions accept chapter_start/chapter_end to filter by range.
"""

from __future__ import annotations

import json
from collections import defaultdict

from src.db.sqlite_db import get_connection
from src.models.chapter_fact import ChapterFact


async def _load_facts_in_range(
    novel_id: str, chapter_start: int, chapter_end: int
) -> list[ChapterFact]:
    """Load ChapterFacts within the given chapter range."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT cf.fact_json, c.chapter_num
            FROM chapter_facts cf
            JOIN chapters c ON cf.chapter_id = c.id AND cf.novel_id = c.novel_id
            WHERE cf.novel_id = ? AND c.chapter_num >= ? AND c.chapter_num <= ?
            ORDER BY c.chapter_num
            """,
            (novel_id, chapter_start, chapter_end),
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


async def get_analyzed_range(novel_id: str) -> tuple[int, int]:
    """Get the first and last analyzed chapter numbers."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT MIN(c.chapter_num) as first_ch, MAX(c.chapter_num) as last_ch
            FROM chapter_facts cf
            JOIN chapters c ON cf.chapter_id = c.id AND cf.novel_id = c.novel_id
            WHERE cf.novel_id = ?
            """,
            (novel_id,),
        )
        row = await cursor.fetchone()
        if row and row["first_ch"] is not None:
            return (row["first_ch"], row["last_ch"])
        return (0, 0)
    finally:
        await conn.close()


# ── Graph (Person Relationship Network) ──────────


async def get_graph_data(
    novel_id: str, chapter_start: int, chapter_end: int
) -> dict:
    facts = await _load_facts_in_range(novel_id, chapter_start, chapter_end)

    # Collect person nodes
    person_chapters: dict[str, set[int]] = defaultdict(set)
    person_org: dict[str, str] = {}

    # Collect edges (person_a, person_b) -> relation info
    edge_map: dict[tuple[str, str], dict] = {}

    for fact in facts:
        ch = fact.chapter_id

        for char in fact.characters:
            person_chapters[char.name].add(ch)

        # Track org membership
        for oe in fact.org_events:
            if oe.member and oe.action in ("加入", "晋升"):
                person_org[oe.member] = oe.org_name

        for rel in fact.relationships:
            key = tuple(sorted([rel.person_a, rel.person_b]))
            if key not in edge_map:
                edge_map[key] = {
                    "source": key[0],
                    "target": key[1],
                    "relation_type": rel.relation_type,
                    "chapters": set(),
                }
            edge_map[key]["chapters"].add(ch)
            edge_map[key]["relation_type"] = rel.relation_type  # latest

    nodes = [
        {
            "id": name,
            "name": name,
            "type": "person",
            "chapter_count": len(chs),
            "org": person_org.get(name, ""),
        }
        for name, chs in person_chapters.items()
    ]
    nodes.sort(key=lambda n: -n["chapter_count"])

    edges = [
        {
            "source": e["source"],
            "target": e["target"],
            "relation_type": e["relation_type"],
            "weight": len(e["chapters"]),
            "chapters": sorted(e["chapters"]),
        }
        for e in edge_map.values()
    ]

    return {"nodes": nodes, "edges": edges}


# ── Map (Location Hierarchy + Trajectories) ──────


async def get_map_data(
    novel_id: str, chapter_start: int, chapter_end: int
) -> dict:
    facts = await _load_facts_in_range(novel_id, chapter_start, chapter_end)

    loc_info: dict[str, dict] = {}
    loc_chapters: dict[str, set[int]] = defaultdict(set)
    trajectories: dict[str, list[dict]] = defaultdict(list)

    for fact in facts:
        ch = fact.chapter_id

        for loc in fact.locations:
            loc_chapters[loc.name].add(ch)
            if loc.name not in loc_info:
                loc_info[loc.name] = {
                    "name": loc.name,
                    "type": loc.type,
                    "parent": loc.parent,
                }
            elif loc.parent and not loc_info[loc.name]["parent"]:
                loc_info[loc.name]["parent"] = loc.parent

        # Build trajectories from characters' locations_in_chapter
        for char in fact.characters:
            for loc_name in char.locations_in_chapter:
                trajectories[char.name].append({
                    "location": loc_name,
                    "chapter": ch,
                })

    # Calculate hierarchy levels
    def get_level(name: str, visited: set[str] | None = None) -> int:
        if visited is None:
            visited = set()
        if name in visited:
            return 0
        visited.add(name)
        info = loc_info.get(name)
        if not info or not info["parent"]:
            return 0
        return 1 + get_level(info["parent"], visited)

    locations = [
        {
            "id": name,
            "name": name,
            "type": info["type"],
            "parent": info["parent"],
            "level": get_level(name),
            "mention_count": len(loc_chapters.get(name, set())),
        }
        for name, info in loc_info.items()
    ]
    locations.sort(key=lambda l: (-l["mention_count"], l["name"]))

    # Deduplicate trajectories
    for person in trajectories:
        seen = set()
        unique = []
        for entry in trajectories[person]:
            key = (entry["location"], entry["chapter"])
            if key not in seen:
                seen.add(key)
                unique.append(entry)
        trajectories[person] = unique

    return {"locations": locations, "trajectories": dict(trajectories)}


# ── Timeline (Events) ────────────────────────────


async def get_timeline_data(
    novel_id: str, chapter_start: int, chapter_end: int
) -> dict:
    facts = await _load_facts_in_range(novel_id, chapter_start, chapter_end)

    events: list[dict] = []
    swimlanes: dict[str, list[int]] = defaultdict(list)

    event_id = 0
    for fact in facts:
        ch = fact.chapter_id

        for ev in fact.events:
            events.append({
                "id": event_id,
                "chapter": ch,
                "summary": ev.summary,
                "type": ev.type,
                "importance": ev.importance,
                "participants": ev.participants,
                "location": ev.location,
            })
            for p in ev.participants:
                swimlanes[p].append(event_id)
            event_id += 1

    return {"events": events, "swimlanes": dict(swimlanes)}


# ── Factions (Organization Network) ──────────────

# Location types that indicate an organization
_ORG_TYPE_KEYWORDS = ("门", "派", "宗", "帮", "教", "盟", "会", "阁", "堂",
                       "军", "朝", "国", "族", "殿", "府", "院")


def _is_org_type(loc_type: str) -> bool:
    """Check whether a location type represents an organization."""
    return any(kw in loc_type for kw in _ORG_TYPE_KEYWORDS)


async def get_factions_data(
    novel_id: str, chapter_start: int, chapter_end: int
) -> dict:
    facts = await _load_facts_in_range(novel_id, chapter_start, chapter_end)

    # org_name -> {name, type}
    org_info: dict[str, dict] = {}
    # org_name -> {person_name -> {person, role, status}}
    org_members: dict[str, dict[str, dict]] = defaultdict(dict)
    org_relations: list[dict] = []

    # ── Source 1: org_events (explicit membership changes) ──
    for fact in facts:
        ch = fact.chapter_id

        for oe in fact.org_events:
            org_name = oe.org_name
            if org_name not in org_info:
                org_info[org_name] = {"name": org_name, "type": oe.org_type}

            if oe.member:
                existing = org_members[org_name].get(oe.member)
                # Keep the latest action; prefer explicit role over None
                if existing is None or oe.role:
                    org_members[org_name][oe.member] = {
                        "person": oe.member,
                        "role": oe.role or (existing["role"] if existing else ""),
                        "status": oe.action,
                    }

            if oe.org_relation:
                org_relations.append({
                    "source": org_name,
                    "target": oe.org_relation.other_org,
                    "type": oe.org_relation.type,
                    "chapter": ch,
                })
                # Ensure the related org is also tracked
                if oe.org_relation.other_org not in org_info:
                    org_info[oe.org_relation.other_org] = {
                        "name": oe.org_relation.other_org,
                        "type": "组织",
                    }

    # ── Source 2: locations with org-like types ──
    # Many sects/factions appear as locations (type="门派"/"帮派" etc.)
    # Characters visiting these locations are associated as members.
    org_locations: set[str] = set()  # location names that are orgs
    for fact in facts:
        for loc in fact.locations:
            if _is_org_type(loc.type) and loc.name not in org_info:
                org_info[loc.name] = {"name": loc.name, "type": loc.type}
            if _is_org_type(loc.type):
                org_locations.add(loc.name)

    # ── Source 3: characters at org-locations ──
    for fact in facts:
        for char in fact.characters:
            for loc_name in char.locations_in_chapter:
                if loc_name in org_locations:
                    if char.name not in org_members[loc_name]:
                        org_members[loc_name][char.name] = {
                            "person": char.name,
                            "role": "",
                            "status": "出现",
                        }

    # ── Source 4: new_concepts about org systems ──
    for fact in facts:
        for concept in fact.new_concepts:
            cat = concept.category
            if _is_org_type(cat) and concept.name not in org_info:
                org_info[concept.name] = {"name": concept.name, "type": cat}

    # Build output
    orgs = [
        {
            "id": name,
            "name": name,
            "type": info["type"],
            "member_count": len(org_members.get(name, {})),
        }
        for name, info in org_info.items()
    ]
    orgs.sort(key=lambda o: -o["member_count"])

    members = {
        org: list(members_map.values())
        for org, members_map in org_members.items()
    }

    return {"orgs": orgs, "relations": org_relations, "members": members}
