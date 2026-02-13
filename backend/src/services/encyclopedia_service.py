"""Encyclopedia service: category stats, concept details, entity list with definitions."""

import json
from collections import Counter

from src.db import chapter_fact_store


async def get_category_stats(novel_id: str) -> dict:
    """Get entity/concept counts by category."""
    facts = await chapter_fact_store.get_all_chapter_facts(novel_id)

    persons: set[str] = set()
    locations: set[str] = set()
    items: set[str] = set()
    orgs: set[str] = set()
    concepts: dict[str, set[str]] = {}  # category -> set of names

    for fact_row in facts:
        fact = fact_row["fact"]

        for ch in fact.get("characters", []):
            if ch.get("name"):
                persons.add(ch["name"])

        for loc in fact.get("locations", []):
            if loc.get("name"):
                locations.add(loc["name"])

        for ie in fact.get("item_events", []):
            if ie.get("item_name"):
                items.add(ie["item_name"])

        for oe in fact.get("org_events", []):
            if oe.get("org_name"):
                orgs.add(oe["org_name"])

        for nc in fact.get("new_concepts", []):
            name = nc.get("name", "")
            cat = nc.get("category", "其他")
            if name:
                if cat not in concepts:
                    concepts[cat] = set()
                concepts[cat].add(name)

    total_concepts = sum(len(v) for v in concepts.values())

    return {
        "total": len(persons) + len(locations) + len(items) + len(orgs) + total_concepts,
        "person": len(persons),
        "location": len(locations),
        "item": len(items),
        "org": len(orgs),
        "concept": total_concepts,
        "concept_categories": {cat: len(names) for cat, names in sorted(concepts.items())},
    }


async def get_encyclopedia_entries(
    novel_id: str,
    category: str | None = None,
    sort_by: str = "name",
) -> list[dict]:
    """Get entity/concept entries for encyclopedia listing."""
    facts = await chapter_fact_store.get_all_chapter_facts(novel_id)

    # Collect entries with first chapter and definition
    entries: dict[str, dict] = {}

    for fact_row in facts:
        fact = fact_row["fact"]
        chapter_id = fact.get("chapter_id", 0)

        if category is None or category == "person":
            for ch in fact.get("characters", []):
                name = ch.get("name", "")
                if not name:
                    continue
                if name not in entries:
                    entries[name] = {
                        "name": name,
                        "type": "person",
                        "category": "person",
                        "definition": "",
                        "first_chapter": chapter_id,
                    }
                # Build a short definition from first appearance
                if not entries[name]["definition"]:
                    parts = []
                    if ch.get("appearance"):
                        parts.append(ch["appearance"][:50])
                    if ch.get("abilities_gained"):
                        for ab in ch["abilities_gained"][:2]:
                            parts.append(f"{ab.get('dimension', '')}: {ab.get('name', '')}")
                    entries[name]["definition"] = " | ".join(parts)

        if category is None or category == "location":
            for loc in fact.get("locations", []):
                name = loc.get("name", "")
                if not name:
                    continue
                if name not in entries:
                    entries[name] = {
                        "name": name,
                        "type": "location",
                        "category": "location",
                        "definition": loc.get("description", "") or loc.get("type", ""),
                        "first_chapter": chapter_id,
                    }

        if category is None or category == "item":
            for ie in fact.get("item_events", []):
                name = ie.get("item_name", "")
                if not name:
                    continue
                if name not in entries:
                    entries[name] = {
                        "name": name,
                        "type": "item",
                        "category": "item",
                        "definition": f"{ie.get('item_type') or ''} - {(ie.get('description') or '')[:50]}",
                        "first_chapter": chapter_id,
                    }

        if category is None or category == "org":
            for oe in fact.get("org_events", []):
                name = oe.get("org_name", "")
                if not name:
                    continue
                if name not in entries:
                    entries[name] = {
                        "name": name,
                        "type": "org",
                        "category": "org",
                        "definition": oe.get("org_type", "") or "",
                        "first_chapter": chapter_id,
                    }

        if category is None or category == "concept" or (
            category and category not in ("person", "location", "item", "org")
        ):
            for nc in fact.get("new_concepts", []):
                name = nc.get("name", "")
                cat = nc.get("category", "其他")
                if not name:
                    continue
                # If filtering by specific concept sub-category
                if category and category not in ("concept", "person", "location", "item", "org"):
                    if cat != category:
                        continue
                if name not in entries:
                    entries[name] = {
                        "name": name,
                        "type": "concept",
                        "category": cat,
                        "definition": (nc.get("definition") or "")[:100],
                        "first_chapter": chapter_id,
                    }

    result = list(entries.values())

    if sort_by == "chapter":
        result.sort(key=lambda e: e["first_chapter"])
    else:
        result.sort(key=lambda e: e["name"])

    return result


async def get_concept_detail(novel_id: str, name: str) -> dict | None:
    """Get full detail for a concept entry."""
    facts = await chapter_fact_store.get_all_chapter_facts(novel_id)

    concept_info: dict | None = None
    excerpts: list[dict] = []
    related_concepts: set[str] = set()

    for fact_row in facts:
        fact = fact_row["fact"]
        chapter_id = fact.get("chapter_id", 0)

        for nc in fact.get("new_concepts", []):
            if nc.get("name") == name:
                if concept_info is None:
                    concept_info = {
                        "name": name,
                        "category": nc.get("category", "其他"),
                        "definition": nc.get("definition", ""),
                        "first_chapter": chapter_id,
                    }
                # Collect related concepts
                for rel in nc.get("related", []):
                    if rel and rel != name:
                        related_concepts.add(rel)
                # Each chapter mention is an excerpt
                if nc.get("definition"):
                    excerpts.append({
                        "chapter": chapter_id,
                        "text": nc["definition"],
                    })

    if concept_info is None:
        return None

    concept_info["excerpts"] = excerpts[:5]
    concept_info["related_concepts"] = sorted(related_concepts)

    # Find related entities (who/where mentions this concept)
    related_entities: list[dict] = []
    for fact_row in facts:
        fact = fact_row["fact"]
        chapter_id = fact.get("chapter_id", 0)
        for evt in fact.get("events", []):
            summary = evt.get("summary", "")
            if name in summary:
                for p in evt.get("participants", []):
                    related_entities.append({"name": p, "type": "person", "chapter": chapter_id})

    # Deduplicate
    seen: set[str] = set()
    unique_entities: list[dict] = []
    for e in related_entities:
        if e["name"] not in seen:
            seen.add(e["name"])
            unique_entities.append(e)
    concept_info["related_entities"] = unique_entities[:10]

    return concept_info
