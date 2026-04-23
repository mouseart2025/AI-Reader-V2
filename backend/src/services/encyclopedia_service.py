"""Encyclopedia service: category stats, concept details, entity list with definitions."""

import json
from collections import Counter

from src.db import chapter_fact_store
from src.services import entity_aggregator
from src.services.entity_identity import (
    choose_display_name,
    entity_identity_key,
    normalize_entity_name,
    same_entity_name,
)
from src.services.domain_labels import concept_category_id, normalize_concept_category


async def get_category_stats(novel_id: str) -> dict:
    """Get entity/concept counts by category."""
    facts = await chapter_fact_store.get_all_chapter_facts(novel_id)
    entities = await entity_aggregator.get_all_entities(novel_id)
    canonical_by_key = {
        entity_identity_key(entry.name): entry
        for entry in entities
    }

    counts = {"person": 0, "location": 0, "item": 0, "org": 0, "concept": 0}
    for entity in entities:
        if entity.type in counts:
            counts[entity.type] += 1

    concepts: dict[str, set[str]] = {}  # category_id -> identity keys
    concept_labels: dict[str, str] = {}
    unique_concepts: set[str] = set()

    for fact_row in facts:
        fact = fact_row["fact"]

        for nc in fact.get("new_concepts", []):
            name = normalize_entity_name(nc.get("name", ""))
            if not name:
                continue
            key = entity_identity_key(name)
            merged = canonical_by_key.get(key)
            if not key or not merged or merged.type != "concept":
                continue
            cat = normalize_concept_category(nc.get("category", "其他"))
            cat_id = concept_category_id(cat)
            unique_concepts.add(key)
            if cat_id not in concepts:
                concepts[cat_id] = set()
            concepts[cat_id].add(key)
            concept_labels.setdefault(cat_id, cat)

    total_concepts = len(unique_concepts)
    sorted_categories = sorted(
        concepts.items(),
        key=lambda item: (concept_labels.get(item[0], item[0]), item[0]),
    )

    return {
        "total": counts["person"] + counts["location"] + counts["item"] + counts["org"] + total_concepts,
        "person": counts["person"],
        "location": counts["location"],
        "item": counts["item"],
        "org": counts["org"],
        "concept": total_concepts,
        "concept_categories": {cat_id: len(names) for cat_id, names in sorted_categories},
        "concept_category_labels": {
            cat_id: concept_labels.get(cat_id, cat_id)
            for cat_id, _ in sorted_categories
        },
    }


async def get_encyclopedia_entries(
    novel_id: str,
    category: str | None = None,
    sort_by: str = "name",
) -> list[dict]:
    """Get entity/concept entries for encyclopedia listing."""
    facts = await chapter_fact_store.get_all_chapter_facts(novel_id)
    merged_entities = await entity_aggregator.get_all_entities(novel_id)
    entity_categories = {"person", "location", "item", "org", "concept"}
    concept_filter_id = None
    if category and category not in entity_categories:
        raw_filter = category.split(":", 1)[1] if category.startswith("concept:") else category
        concept_filter_id = concept_category_id(raw_filter)
    canonical_by_key = {
        entity_identity_key(entry.name): entry
        for entry in merged_entities
    }

    # Collect entries with first chapter and definition
    entries: dict[str, dict] = {}
    # Track chapters per entity for chapter_count
    entry_chapters: dict[str, set[int]] = {}
    entry_name_votes: dict[str, Counter[str]] = {}

    for fact_row in facts:
        fact = fact_row["fact"]
        chapter_id = fact.get("chapter_id", 0)

        if category is None or category == "person":
            for ch in fact.get("characters", []):
                raw_name = normalize_entity_name(ch.get("name", ""))
                if not raw_name:
                    continue
                name_key = entity_identity_key(raw_name)
                merged = canonical_by_key.get(name_key)
                if not merged or merged.type != "person":
                    continue
                name = merged.name
                entry_chapters.setdefault(name_key, set()).add(chapter_id)
                entry_name_votes.setdefault(name_key, Counter())[raw_name] += 1
                if name_key not in entries:
                    entries[name_key] = {
                        "name": name,
                        "type": "person",
                        "category": "person",
                        "definition": "",
                        "first_chapter": chapter_id,
                    }
                # Build a short definition from first appearance
                if not entries[name_key]["definition"]:
                    parts = []
                    if ch.get("appearance"):
                        parts.append(ch["appearance"][:50])
                    if ch.get("abilities_gained"):
                        for ab in ch["abilities_gained"][:2]:
                            parts.append(f"{ab.get('dimension', '')}: {ab.get('name', '')}")
                    entries[name_key]["definition"] = " | ".join(parts)

        if category is None or category == "location":
            for loc in fact.get("locations", []):
                raw_name = normalize_entity_name(loc.get("name", ""))
                if not raw_name:
                    continue
                name_key = entity_identity_key(raw_name)
                merged = canonical_by_key.get(name_key)
                if not merged or merged.type != "location":
                    continue
                name = merged.name
                entry_chapters.setdefault(name_key, set()).add(chapter_id)
                entry_name_votes.setdefault(name_key, Counter())[raw_name] += 1
                if name_key not in entries:
                    entries[name_key] = {
                        "name": name,
                        "type": "location",
                        "category": "location",
                        "definition": loc.get("description", "") or loc.get("type", ""),
                        "first_chapter": chapter_id,
                        "parent": loc.get("parent"),
                    }
                elif not entries[name_key]["definition"] and (loc.get("description") or loc.get("type")):
                    entries[name_key]["definition"] = loc.get("description", "") or loc.get("type", "")
                if not entries[name_key].get("parent") and loc.get("parent"):
                    entries[name_key]["parent"] = loc.get("parent")

        if category is None or category == "item":
            for ie in fact.get("item_events", []):
                raw_name = normalize_entity_name(ie.get("item_name", ""))
                if not raw_name:
                    continue
                name_key = entity_identity_key(raw_name)
                merged = canonical_by_key.get(name_key)
                if not merged or merged.type != "item":
                    continue
                name = merged.name
                entry_chapters.setdefault(name_key, set()).add(chapter_id)
                entry_name_votes.setdefault(name_key, Counter())[raw_name] += 1
                if name_key not in entries:
                    entries[name_key] = {
                        "name": name,
                        "type": "item",
                        "category": "item",
                        "definition": f"{ie.get('item_type') or ''} - {(ie.get('description') or '')[:50]}",
                        "first_chapter": chapter_id,
                    }
                elif not entries[name_key]["definition"] and (ie.get("item_type") or ie.get("description")):
                    entries[name_key]["definition"] = f"{ie.get('item_type') or ''} - {(ie.get('description') or '')[:50]}"

        if category is None or category == "org":
            for oe in fact.get("org_events", []):
                raw_name = normalize_entity_name(oe.get("org_name", ""))
                if not raw_name:
                    continue
                name_key = entity_identity_key(raw_name)
                merged = canonical_by_key.get(name_key)
                if not merged or merged.type != "org":
                    continue
                name = merged.name
                entry_chapters.setdefault(name_key, set()).add(chapter_id)
                entry_name_votes.setdefault(name_key, Counter())[raw_name] += 1
                if name_key not in entries:
                    entries[name_key] = {
                        "name": name,
                        "type": "org",
                        "category": "org",
                        "definition": oe.get("org_type", "") or "",
                        "first_chapter": chapter_id,
                    }
                elif not entries[name_key]["definition"] and oe.get("org_type"):
                    entries[name_key]["definition"] = oe.get("org_type", "") or ""

        if category is None or category == "concept" or (
            category and category not in entity_categories
        ):
            for nc in fact.get("new_concepts", []):
                raw_name = normalize_entity_name(nc.get("name", ""))
                cat = normalize_concept_category(nc.get("category", "其他"))
                cat_id = concept_category_id(cat)
                if not raw_name:
                    continue
                name_key = entity_identity_key(raw_name)
                merged = canonical_by_key.get(name_key)
                if not merged or merged.type != "concept":
                    continue
                # If filtering by specific concept sub-category
                if concept_filter_id and cat_id != concept_filter_id:
                    continue
                name = merged.name
                entry_chapters.setdefault(name_key, set()).add(chapter_id)
                entry_name_votes.setdefault(name_key, Counter())[raw_name] += 1
                if name_key not in entries:
                    entries[name_key] = {
                        "name": name,
                        "type": "concept",
                        "category": cat,
                        "category_id": cat_id,
                        "definition": (nc.get("definition") or "")[:100],
                        "first_chapter": chapter_id,
                    }
                elif not entries[name_key]["definition"] and nc.get("definition"):
                    entries[name_key]["definition"] = (nc.get("definition") or "")[:100]

    # Inject chapter_count and variant hints into every entry
    from src.extraction.fact_validator import get_name_variant_hint
    for name_key, entry in entries.items():
        if name_key in entry_name_votes:
            entry["name"] = choose_display_name(
                entry_name_votes[name_key].keys(),
                entry_name_votes[name_key],
            ) or entry["name"]
        entry["chapter_count"] = len(entry_chapters.get(name_key, set()))
        hint = get_name_variant_hint(entry["name"])
        if hint:
            entry["variant_hint"] = hint

    result = list(entries.values())

    # Load WorldStructure for tier/icon enrichment and hierarchy sort
    from src.db import world_structure_store
    ws = await world_structure_store.load(novel_id)

    # Enrich location entries with tier and icon
    if ws:
        tiers = ws.location_tiers or {}
        icons = ws.location_icons or {}
        for entry in result:
            if entry.get("type") == "location":
                ws_name = next(
                    (name for name in tiers if same_entity_name(name, entry["name"])),
                    entry["name"],
                )
                entry["tier"] = tiers.get(ws_name, "")
                entry["icon"] = icons.get(ws_name, "")

    if sort_by == "hierarchy" and (category is None or category == "location"):
        # Override parents with authoritative WorldStructure data
        if ws and ws.location_parents:
            for entry in result:
                if entry.get("type") == "location":
                    auth_parent = next(
                        (p for loc_name, p in ws.location_parents.items() if same_entity_name(loc_name, entry["name"])),
                        None,
                    )
                    if auth_parent:
                        entry["parent"] = auth_parent
            # Inject virtual parent nodes from location_parents so
            # the tree structure matches WorldStructureEditor
            existing_names = {e["name"] for e in result if e.get("type") == "location"}
            parent_names = set(ws.location_parents.values())
            for vp in parent_names - existing_names:
                auth_parent = ws.location_parents.get(vp)
                tier = (ws.location_tiers or {}).get(vp, "")
                icon = (ws.location_icons or {}).get(vp, "")
                result.append({
                    "name": vp,
                    "type": "location",
                    "category": "location",
                    "definition": "",
                    "first_chapter": 0,
                    "chapter_count": 0,
                    "parent": auth_parent or "",
                    "tier": tier,
                    "icon": icon,
                    "virtual": True,
                })
        result = _sort_by_hierarchy(result)
    elif sort_by == "mentions":
        result.sort(
            key=lambda e: -len(entry_chapters.get(entity_identity_key(e["name"]), set()))
        )
    elif sort_by == "chapter":
        result.sort(key=lambda e: e["first_chapter"])
    else:
        result.sort(key=lambda e: e["name"])

    return result


def _sort_by_hierarchy(entries: list[dict]) -> list[dict]:
    """Sort entries by DFS tree order. Location entries get depth; others appended at end."""
    locations = [e for e in entries if e.get("type") == "location"]
    others = [e for e in entries if e.get("type") != "location"]

    # Build parent→children map
    name_set = {e["name"] for e in locations}
    children_map: dict[str, list[str]] = {}
    for e in locations:
        parent = e.get("parent")
        if parent and parent in name_set:
            children_map.setdefault(parent, []).append(e["name"])

    entry_map = {e["name"]: e for e in locations}

    # Identify roots: locations with no parent or parent not in the list
    roots = [
        e["name"] for e in locations
        if not e.get("parent") or e["parent"] not in name_set
    ]
    roots.sort(key=lambda n: entry_map[n]["name"])

    # DFS traversal
    result: list[dict] = []
    visited: set[str] = set()

    def dfs(name: str, depth: int) -> None:
        if name in visited:
            return
        visited.add(name)
        entry = entry_map[name].copy()
        entry["depth"] = depth
        result.append(entry)
        for child in sorted(children_map.get(name, []), key=lambda n: entry_map[n]["name"]):
            dfs(child, depth + 1)

    for root in roots:
        dfs(root, 0)

    # Add any locations not visited (disconnected from tree)
    for e in locations:
        if e["name"] not in visited:
            entry = e.copy()
            entry["depth"] = 0
            result.append(entry)

    # Append non-location entries at the end
    result.extend(others)
    return result


async def get_concept_detail(novel_id: str, name: str) -> dict | None:
    """Get full detail for a concept entry."""
    facts = await chapter_fact_store.get_all_chapter_facts(novel_id)
    target_key = entity_identity_key(name)

    concept_info: dict | None = None
    excerpts: list[dict] = []
    related_concepts: set[str] = set()

    for fact_row in facts:
        fact = fact_row["fact"]
        chapter_id = fact.get("chapter_id", 0)

        for nc in fact.get("new_concepts", []):
            raw_name = normalize_entity_name(nc.get("name", ""))
            if raw_name and entity_identity_key(raw_name) == target_key:
                cat = normalize_concept_category(nc.get("category", "其他"))
                if concept_info is None:
                    concept_info = {
                        "name": name,
                        "category": cat,
                        "category_id": concept_category_id(cat),
                        "definition": nc.get("definition", ""),
                        "first_chapter": chapter_id,
                    }
                # Collect related concepts
                for rel in nc.get("related", []):
                    if rel and entity_identity_key(rel) != target_key:
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


async def get_location_spatial_summary(novel_id: str, name: str) -> list[dict]:
    """Get spatial relationships involving a location, aggregated across chapters."""
    facts = await chapter_fact_store.get_all_chapter_facts(novel_id)

    # (source, target, relation_type, value) → set of chapter_ids
    agg: dict[tuple[str, str, str, str], set[int]] = {}

    for fact_row in facts:
        fact = fact_row["fact"]
        chapter_id = fact.get("chapter_id", 0)
        for sr in fact.get("spatial_relationships", []):
            source = sr.get("source", "")
            target = sr.get("target", "")
            if name not in (source, target):
                continue
            key = (source, target, sr.get("relation_type", ""), sr.get("value", ""))
            agg.setdefault(key, set()).add(chapter_id)

    result = []
    for (source, target, relation_type, value), chapters in agg.items():
        result.append({
            "source": source,
            "target": target,
            "relation_type": relation_type,
            "value": value,
            "chapters": sorted(chapters),
        })
    result.sort(key=lambda r: -len(r["chapters"]))
    return result


async def get_entity_scenes(novel_id: str, entity_name: str) -> list[dict]:
    """Get scenes involving an entity, capped at 30."""
    scenes = await chapter_fact_store.get_all_scenes(novel_id)
    from src.services.domain_labels import normalize_scene_role, scene_role_id, scene_tone_id

    result: list[dict] = []
    target_key = entity_identity_key(entity_name)
    for scene in scenes:
        # Check if entity appears in characters, location, or summary
        characters = scene.get("characters", [])
        char_names = [
            normalize_entity_name(c if isinstance(c, str) else c.get("name", ""))
            for c in characters
        ]
        location = normalize_entity_name(scene.get("location", ""))
        summary = scene.get("summary", "") or scene.get("description", "")
        summary_key = entity_identity_key(summary)

        if (
            any(entity_identity_key(name) == target_key for name in char_names)
            or entity_identity_key(location) == target_key
            or (target_key and target_key in summary_key)
        ):
            # Determine the entity's role in this scene
            role = "提及"
            if entity_identity_key(location) == target_key:
                role = "场所"
            elif any(entity_identity_key(name) == target_key for name in char_names):
                # Check character_roles for richer info
                for cr in scene.get("character_roles", []):
                    if same_entity_name(cr.get("name", ""), entity_name):
                        role = cr.get("role", "配")
                        break
                else:
                    role = "出场"
            role = normalize_scene_role(role)

            result.append({
                "chapter": scene.get("chapter", 0),
                "index": scene.get("index", 0),
                "title": scene.get("title", "") or scene.get("heading", ""),
                "location": location,
                "emotional_tone": scene.get("emotional_tone", ""),
                "emotional_tone_id": scene.get("emotional_tone_id") or scene_tone_id(scene.get("emotional_tone", "")),
                "summary": (summary or "")[:80],
                "role": role,
                "role_id": scene_role_id(role),
            })
            if len(result) >= 30:
                break

    return result


async def get_location_conflicts_summary(novel_id: str) -> dict[str, list[dict]]:
    """Get location conflicts grouped by location name."""
    from src.services.conflict_detector import _detect_location_conflicts
    facts = await chapter_fact_store.get_all_chapter_facts(novel_id)

    parsed: list[tuple[int, dict]] = []
    for fact_row in facts:
        chapter_id = fact_row["fact"].get("chapter_id", 0)
        parsed.append((chapter_id, fact_row["fact"]))

    conflicts = _detect_location_conflicts(parsed)

    result: dict[str, list[dict]] = {}
    for c in conflicts:
        entity = c.entity
        result.setdefault(entity, []).append({
            "type": c.type,
            "severity": c.severity,
            "description": c.description,
            "chapters": c.chapters,
        })

    return result
