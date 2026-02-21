"""Visualization data aggregation from ChapterFacts.

Provides data for 4 views: graph, map, timeline, factions.
All functions accept chapter_start/chapter_end to filter by range.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter, defaultdict

from src.db.sqlite_db import get_connection
from src.models.chapter_fact import ChapterFact
from src.db import world_structure_store
from src.services.map_layout_service import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    SPATIAL_SCALE_CANVAS,
    ConstraintSolver,
    _layout_regions,
    compute_chapter_hash,
    compute_layered_layout,
    generate_terrain,
    generate_voronoi_boundaries,
    layout_to_list,
    place_unresolved_near_neighbors,
)
from src.services.alias_resolver import build_alias_map
from src.services.geo_resolver import (
    auto_resolve as geo_auto_resolve,
    place_unresolved_geo_coords,
)
from src.services.conflict_detector import _detect_location_conflicts
from src.services.relation_utils import normalize_relation_type
from src.services.world_structure_agent import WorldStructureAgent

logger = logging.getLogger(__name__)


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


async def _get_earlier_location_names(
    novel_id: str, first_chapter: int, before_chapter: int,
) -> set[str]:
    """Get location names from chapters before the given chapter number."""
    if before_chapter <= first_chapter:
        return set()
    facts = await _load_facts_in_range(novel_id, first_chapter, before_chapter - 1)
    names: set[str] = set()
    for fact in facts:
        for loc in fact.locations:
            names.add(loc.name)
    return names


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
    alias_map = await build_alias_map(novel_id)

    # Collect person nodes
    person_chapters: dict[str, set[int]] = defaultdict(set)
    person_org: dict[str, str] = {}
    # Track all aliases seen per canonical name
    person_aliases: dict[str, set[str]] = defaultdict(set)

    # Collect edges (person_a, person_b) -> relation info
    edge_map: dict[tuple[str, str], dict] = {}

    for fact in facts:
        ch = fact.chapter_id

        for char in fact.characters:
            canonical = alias_map.get(char.name, char.name)
            person_chapters[canonical].add(ch)
            if char.name != canonical:
                person_aliases[canonical].add(char.name)

        # Track org membership
        for oe in fact.org_events:
            if oe.member and oe.action in ("加入", "晋升"):
                member = alias_map.get(oe.member, oe.member)
                org = alias_map.get(oe.org_name, oe.org_name)
                person_org[member] = org

        for rel in fact.relationships:
            a = alias_map.get(rel.person_a, rel.person_a)
            b = alias_map.get(rel.person_b, rel.person_b)
            if a == b:
                continue  # skip self-relations caused by alias
            key = tuple(sorted([a, b]))
            if key not in edge_map:
                edge_map[key] = {
                    "source": key[0],
                    "target": key[1],
                    "type_counts": Counter(),
                    "chapters": set(),
                }
            edge_map[key]["chapters"].add(ch)
            normalized = normalize_relation_type(rel.relation_type)
            edge_map[key]["type_counts"][normalized] += 1

    nodes = [
        {
            "id": name,
            "name": name,
            "type": "person",
            "chapter_count": len(chs),
            "org": person_org.get(name, ""),
            "aliases": sorted(person_aliases.get(name, set())),
        }
        for name, chs in person_chapters.items()
    ]
    nodes.sort(key=lambda n: -n["chapter_count"])

    edges = [
        {
            "source": e["source"],
            "target": e["target"],
            "relation_type": e["type_counts"].most_common(1)[0][0],
            "all_types": [t for t, _ in e["type_counts"].most_common()],
            "weight": len(e["chapters"]),
            "chapters": sorted(e["chapters"]),
        }
        for e in edge_map.values()
    ]

    # Compute a suggested min_edge_weight for large graphs
    max_weight = max((e["weight"] for e in edges), default=1)
    suggested_min_edge = 1
    if len(edges) > 500:
        suggested_min_edge = 2
    if len(edges) > 2000:
        suggested_min_edge = max(3, max_weight // 10)

    return {
        "nodes": nodes,
        "edges": edges,
        "max_edge_weight": max_weight,
        "suggested_min_edge_weight": suggested_min_edge,
    }


# ── Map (Location Hierarchy + Trajectories) ──────


_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}

# Valid direction enum values expected by the constraint solver
_VALID_DIRECTION_VALUES = {
    "north_of", "south_of", "east_of", "west_of",
    "northeast_of", "northwest_of", "southeast_of", "southwest_of",
}

# Chinese direction → English enum
_CHINESE_DIRECTION_MAP = {
    "北": "north_of", "北方": "north_of", "北边": "north_of", "以北": "north_of",
    "南": "south_of", "南方": "south_of", "南边": "south_of", "以南": "south_of",
    "东": "east_of", "东方": "east_of", "东边": "east_of", "以东": "east_of",
    "西": "west_of", "西方": "west_of", "西边": "west_of", "以西": "west_of",
    "东北": "northeast_of", "西北": "northwest_of",
    "东南": "southeast_of", "西南": "southwest_of",
}


def _clean_spatial_constraints(
    constraints: list[dict],
    locations: list[dict],
) -> list[dict]:
    """Post-process spatial constraints to fix common LLM extraction errors.

    1. Fix inverted contains relationships using hierarchy levels.
    2. Normalize Chinese direction values to English enum.
    3. Remove constraints with invalid/unparseable values.
    """
    # Build lookup tables
    loc_level = {loc["name"]: loc.get("level", 0) for loc in locations}
    loc_parent = {loc["name"]: loc.get("parent") for loc in locations}

    cleaned = []
    fixed = 0
    removed = 0

    for c in constraints:
        rtype = c["relation_type"]

        # ── Fix contains inversions ──
        if rtype == "contains":
            src, tgt = c["source"], c["target"]
            src_level = loc_level.get(src, 0)
            tgt_level = loc_level.get(tgt, 0)

            # Check if source is actually a child of target (inverted)
            if loc_parent.get(src) == tgt:
                # Swap: target should contain source
                c = {**c, "source": tgt, "target": src}
                fixed += 1
            elif loc_parent.get(tgt) == src:
                pass  # Correct: source contains target
            elif src_level > tgt_level:
                # Higher level = deeper in hierarchy = smaller area → likely inverted
                c = {**c, "source": tgt, "target": src}
                fixed += 1

            cleaned.append(c)
            continue

        # ── Normalize direction values ──
        if rtype == "direction":
            value = c["value"]
            if value in _VALID_DIRECTION_VALUES:
                cleaned.append(c)
                continue
            # Try Chinese mapping
            for zh, en in _CHINESE_DIRECTION_MAP.items():
                if zh in value:
                    c = {**c, "value": en}
                    fixed += 1
                    cleaned.append(c)
                    break
            else:
                # Unparseable direction value — drop
                removed += 1
            continue

        # Other relation types: keep as-is
        cleaned.append(c)

    if fixed or removed:
        logger.info(
            "Constraint cleaning: fixed %d, removed %d, kept %d",
            fixed, removed, len(cleaned),
        )
    return cleaned


async def get_map_data(
    novel_id: str, chapter_start: int, chapter_end: int,
    layer_id: str | None = None,
) -> dict:
    facts = await _load_facts_in_range(novel_id, chapter_start, chapter_end)

    loc_info: dict[str, dict] = {}
    loc_chapters: dict[str, set[int]] = defaultdict(set)
    trajectories: dict[str, list[dict]] = defaultdict(list)
    # Spatial constraint aggregation: (source, target, relation_type) -> best entry
    constraint_map: dict[tuple[str, str, str], dict] = {}

    # Track the "best" role per location: setting > boundary > referenced > None
    _ROLE_PRIORITY = {"setting": 3, "boundary": 2, "referenced": 1}
    loc_role: dict[str, str | None] = {}

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
            # Upgrade role to most significant seen
            new_role = loc.role
            if new_role:
                cur = loc_role.get(loc.name)
                if cur is None or _ROLE_PRIORITY.get(new_role, 0) > _ROLE_PRIORITY.get(cur, 0):
                    loc_role[loc.name] = new_role

        # Build trajectories from characters' locations_in_chapter
        for char in fact.characters:
            for loc_name in char.locations_in_chapter:
                trajectories[char.name].append({
                    "location": loc_name,
                    "chapter": ch,
                })

        # Aggregate spatial relationships
        for sr in fact.spatial_relationships:
            key = (sr.source, sr.target, sr.relation_type)
            new_rank = _CONFIDENCE_RANK.get(sr.confidence, 1)
            existing = constraint_map.get(key)
            if existing is None or new_rank > _CONFIDENCE_RANK.get(existing["confidence"], 1):
                constraint_map[key] = {
                    "source": sr.source,
                    "target": sr.target,
                    "relation_type": sr.relation_type,
                    "value": sr.value,
                    "confidence": sr.confidence,
                    "narrative_evidence": sr.narrative_evidence,
                }

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

    # Pre-load tier/icon maps from WorldStructure (loaded later, but we need a ref)
    # We'll populate these after ws is loaded; for now default to empty
    _tier_map: dict[str, str] = {}
    _icon_map: dict[str, str] = {}

    locations = [
        {
            "id": name,
            "name": name,
            "type": info["type"],
            "parent": info["parent"],
            "level": get_level(name),
            "mention_count": len(loc_chapters.get(name, set())),
            "tier": "city",     # placeholder, updated after ws load
            "icon": "generic",  # placeholder, updated after ws load
            "role": loc_role.get(name),
        }
        for name, info in loc_info.items()
    ]
    locations.sort(key=lambda l: (-l["mention_count"], l["name"]))

    # Deduplicate trajectories
    for person in list(trajectories.keys()):
        seen = set()
        unique = []
        for entry in trajectories[person]:
            key = (entry["location"], entry["chapter"])
            if key not in seen:
                seen.add(key)
                unique.append(entry)
        trajectories[person] = unique

    # Limit trajectories: only keep characters with meaningful movement (≥2 unique locations)
    # This prevents sending thousands of single-location entries that bloat the response
    trajectories = {
        person: entries
        for person, entries in trajectories.items()
        if len({e["location"] for e in entries}) >= 2
    }

    spatial_constraints = list(constraint_map.values())

    # Clean up common LLM extraction errors
    spatial_constraints = _clean_spatial_constraints(spatial_constraints, locations)

    # Build first-chapter-appearance map for narrative axis
    first_chapter_map: dict[str, int] = {}
    for name, chs in loc_chapters.items():
        if chs:
            first_chapter_map[name] = min(chs)

    # ── Load WorldStructure ──
    region_boundaries: list[dict] = []
    location_region_bounds: dict[str, tuple[float, float, float, float]] = {}
    layer_layouts: dict[str, list[dict]] = {}
    ws = None
    ws_summary: dict | None = None
    portals_response: list[dict] = []

    try:
        ws = await world_structure_store.load(novel_id)
        if ws is not None:
            # Update location tier/icon from WorldStructure (with heuristic fallback)
            _tier_map = ws.location_tiers if ws else {}
            _icon_map = ws.location_icons if ws else {}
            for loc in locations:
                name = loc["name"]
                loc_type = loc.get("type", "")
                parent = loc.get("parent")
                level = loc.get("level", 0)
                tier = _tier_map.get(name, "")
                if not tier:
                    tier = WorldStructureAgent._classify_tier(name, loc_type, parent, level)
                loc["tier"] = tier
                icon = _icon_map.get(name, "")
                if not icon or icon == "generic":
                    icon = WorldStructureAgent._classify_icon(name, loc_type)
                loc["icon"] = icon

            # Override parents with authoritative voted parents
            if ws.location_parents:
                for loc in locations:
                    authoritative = ws.location_parents.get(loc["name"])
                    if authoritative:
                        loc["parent"] = authoritative

            # Override parents with user-locked parents (highest priority)
            try:
                locked_parents = await _load_locked_parents(novel_id)
                if locked_parents:
                    for loc in locations:
                        locked_p = locked_parents.get(loc["name"])
                        if locked_p is not None:
                            loc["parent"] = locked_p
                            loc["locked"] = True
            except Exception:
                logger.warning("Failed to load locked parents", exc_info=True)

            # Recalculate hierarchy levels with updated parents
            if ws.location_parents:
                # Rebuild loc_info parents first
                for loc in locations:
                    if loc["name"] in loc_info:
                        loc_info[loc["name"]]["parent"] = loc["parent"]
                for loc in locations:
                    loc["level"] = get_level(loc["name"])

            # Build world_structure summary for API response
            ws_summary = _build_ws_summary(ws)

            # Build portals response
            for p in ws.portals:
                target_layer_name = ""
                for layer in ws.layers:
                    if layer.layer_id == p.target_layer:
                        target_layer_name = layer.name
                        break
                portals_response.append({
                    "name": p.name,
                    "source_layer": p.source_layer,
                    "source_location": p.source_location,
                    "target_layer": p.target_layer,
                    "target_layer_name": target_layer_name,
                    "target_location": p.target_location,
                    "is_bidirectional": p.is_bidirectional,
                })

            # Auto-generate portal entries for merged layers (≤1 location)
            _existing_portal_targets = {p["target_layer"] for p in portals_response}
            for layer_info in ws_summary["layers"]:
                if not layer_info.get("merged"):
                    continue
                if layer_info["layer_id"] in _existing_portal_targets:
                    continue  # already has a portal
                if layer_info["location_count"] < 1:
                    continue
                # Find the single location in this layer
                loc_name = next(
                    (name for name, lid in ws.location_layer_map.items()
                     if lid == layer_info["layer_id"]),
                    None,
                )
                if loc_name:
                    portals_response.append({
                        "name": f"进入{layer_info['name']}",
                        "source_layer": "overworld",
                        "source_location": loc_name,
                        "target_layer": layer_info["layer_id"],
                        "target_layer_name": layer_info["name"],
                        "target_location": loc_name,
                        "is_bidirectional": True,
                    })

            # Get regions from the active layer (default: overworld)
            target_layer_id = layer_id or "overworld"
            active_regions = []
            for layer_obj in ws.layers:
                if layer_obj.layer_id == target_layer_id and layer_obj.regions:
                    active_regions = [
                        {
                            "name": r.name,
                            "cardinal_direction": r.cardinal_direction,
                            "region_type": r.region_type,
                        }
                        for r in layer_obj.regions
                    ]
                    break

            if active_regions:
                # Cap regions for display: if too many, keep top N by location count
                MAX_DISPLAY_REGIONS = 30
                if len(active_regions) > MAX_DISPLAY_REGIONS:
                    # Count locations per region
                    region_loc_counts: dict[str, int] = {}
                    for loc_name_r, region_name_r in ws.location_region_map.items():
                        region_loc_counts[region_name_r] = region_loc_counts.get(region_name_r, 0) + 1
                    active_regions.sort(
                        key=lambda r: region_loc_counts.get(r["name"], 0), reverse=True,
                    )
                    logger.info(
                        "Capping display regions from %d to %d",
                        len(active_regions), MAX_DISPLAY_REGIONS,
                    )
                    active_regions = active_regions[:MAX_DISPLAY_REGIONS]

                # Dynamic canvas size for region layout
                _ws_cw, _ws_ch = SPATIAL_SCALE_CANVAS.get(
                    ws.spatial_scale or "", (CANVAS_WIDTH, CANVAS_HEIGHT)
                )
                region_layout = _layout_regions(active_regions, canvas_width=_ws_cw, canvas_height=_ws_ch)

                # Generate Voronoi polygon boundaries
                voronoi_result = generate_voronoi_boundaries(region_layout, canvas_width=_ws_cw, canvas_height=_ws_ch)

                # Build region_boundaries for API response (polygon + center)
                for rname, rdata in voronoi_result.items():
                    region_boundaries.append({
                        "region_name": rname,
                        "color": rdata["color"],
                        "polygon": [list(p) for p in rdata["polygon"]],
                        "center": list(rdata["center"]),
                    })

                # Map locations to their region bounds (still use rectangular bounds for solver)
                for loc_name, region_name in ws.location_region_map.items():
                    if region_name in region_layout:
                        location_region_bounds[loc_name] = region_layout[region_name]["bounds"]
    except Exception:
        logger.warning("Failed to load WorldStructure for region layout", exc_info=True)

    # ── Layout computation with caching ──
    _ws_scale_for_hash = ws.spatial_scale if ws else None
    _cw_hash, _ch_hash = SPATIAL_SCALE_CANVAS.get(
        _ws_scale_for_hash or "", (CANVAS_WIDTH, CANVAS_HEIGHT)
    )
    ch_hash = compute_chapter_hash(chapter_start, chapter_end, _cw_hash, _ch_hash)
    target_layer = layer_id or "overworld"

    # Raw geo coordinates for Leaflet frontend (populated by geographic branch)
    geo_coords_raw: dict[str, dict[str, float]] | None = None

    # Try layer-level cache first
    cached_layer = await _load_cached_layer_layout(novel_id, target_layer, ch_hash)
    # Invalidate stale cache: if world_structure says geographic but cache says otherwise
    if (
        cached_layer is not None
        and target_layer == "overworld"
        and ws and ws.geo_type in ("realistic", "mixed")
        and cached_layer["layout_mode"] != "geographic"
    ):
        logger.info("Invalidating stale overworld cache (geo_type=%s but cached as %s)",
                     ws.geo_type, cached_layer["layout_mode"])
        cached_layer = None
    if cached_layer is not None:
        layout_data = cached_layer["layout"]
        layout_mode = cached_layer["layout_mode"]
        terrain_url = None
        # Restore geo_coords for cached geographic layouts (coords are not in cache)
        if layout_mode == "geographic" and target_layer == "overworld" and ws:
            try:
                all_names = [loc["name"] for loc in locations]
                loc_parent_map = {
                    loc["name"]: loc.get("parent")
                    for loc in locations
                }
                _scope, _gtype, _resolver, resolved = await geo_auto_resolve(
                    ws.novel_genre_hint, all_names, all_names, loc_parent_map,
                )
                if resolved:
                    geo_coords_raw = {
                        name: {"lat": coord[0], "lng": coord[1]}
                        for name, coord in resolved.items()
                    }
                    # Also estimate geo_coords for unresolved locations
                    resolved_names = set(resolved.keys())
                    unresolved_names = [
                        loc["name"] for loc in locations
                        if loc["name"] not in resolved_names
                    ]
                    if unresolved_names:
                        estimated = place_unresolved_geo_coords(
                            unresolved_names, resolved, loc_parent_map,
                        )
                        for name, (lat, lng) in estimated.items():
                            geo_coords_raw[name] = {"lat": lat, "lng": lng}
            except Exception:
                logger.warning("Failed to restore geo_coords from cache", exc_info=True)
    else:
        # ── Geographic layout: real-world coordinates via GeoNames ──
        # Only attempt for overworld layer (sub-layers are fictional internal spaces)
        geo_resolved = False
        if ws and target_layer == "overworld":
            try:
                all_names = [loc["name"] for loc in locations]
                major_names = [
                    loc["name"] for loc in locations
                    if loc.get("level", 0) <= 3
                ]
                loc_parent_map = {
                    loc["name"]: loc.get("parent")
                    for loc in locations
                }
                geo_scope, geo_type, resolver, resolved = await geo_auto_resolve(
                    ws.novel_genre_hint, all_names, major_names, loc_parent_map,
                )

                # Cache geo_type on WorldStructure
                if ws.geo_type != geo_type:
                    ws.geo_type = geo_type
                    await world_structure_store.save(novel_id, ws)

                if resolver and resolved and geo_type in ("realistic", "mixed"):
                    # Store raw lat/lng for Leaflet frontend
                    geo_coords_raw = {
                        name: {"lat": coord[0], "lng": coord[1]}
                        for name, coord in resolved.items()
                    }
                    layout_data = resolver.project_to_canvas(
                        resolved, locations, _cw_hash, _ch_hash,
                    )

                    # Place unresolved names near their resolved neighbors
                    resolved_names = set(resolved.keys())
                    unresolved_names = [
                        loc["name"] for loc in locations
                        if loc["name"] not in resolved_names
                    ]
                    if unresolved_names:
                        parent_map = {
                            loc["name"]: loc.get("parent")
                            for loc in locations
                        }
                        extra = place_unresolved_near_neighbors(
                            unresolved_names, layout_data, locations,
                            parent_map, _cw_hash, _ch_hash,
                        )
                        layout_data.extend(extra)

                        # Also estimate geo_coords for unresolved locations
                        # so GeoMap (Leaflet) can render them as markers
                        estimated = place_unresolved_geo_coords(
                            unresolved_names, resolved, parent_map,
                        )
                        for name, (lat, lng) in estimated.items():
                            geo_coords_raw[name] = {"lat": lat, "lng": lng}

                    layout_mode = "geographic"
                    terrain_url = None
                    geo_resolved = True

                    # Cache the geographic layout
                    await _save_cached_layer_layout(
                        novel_id, target_layer, ch_hash,
                        layout_data, "geographic",
                    )
            except Exception:
                logger.warning(
                    "Geographic layout failed, falling back to solver",
                    exc_info=True,
                )

        # ── Existing layout paths (layered / constraint solver) ──
        if not geo_resolved:
            if ws is not None and len(ws.layers) > 1:
                try:
                    user_overrides = await _load_user_overrides(novel_id)
                    ws_dict = ws.model_dump()
                    layer_layouts = await asyncio.to_thread(
                        compute_layered_layout,
                        ws_dict, locations, spatial_constraints,
                        user_overrides, first_chapter_map,
                        spatial_scale=ws.spatial_scale,
                    )
                    # Cache each layer
                    for lid, litems in layer_layouts.items():
                        await _save_cached_layer_layout(
                            novel_id, lid, ch_hash, litems, "layered",
                        )
                except Exception:
                    logger.warning("Layered layout computation failed", exc_info=True)

                # Get the requested layer's data
                layout_data = layer_layouts.get(target_layer, [])
                layout_mode = "layered" if layout_data else "hierarchy"
                terrain_url = None
            else:
                # Global solve (backward compatible path)
                layout_data, layout_mode, terrain_url = await _compute_or_load_layout(
                    novel_id, ch_hash, locations, spatial_constraints,
                    first_chapter_map,
                    location_region_bounds=location_region_bounds,
                )

    # ── Revealed location names for fog of war ──
    revealed_names: list[str] = []
    try:
        analyzed_first, _ = await get_analyzed_range(novel_id)
        if analyzed_first > 0 and chapter_start > analyzed_first:
            earlier_names = await _get_earlier_location_names(
                novel_id, analyzed_first, chapter_start,
            )
            active_names = {loc["name"] for loc in locations}
            revealed_names = sorted(earlier_names - active_names)
    except Exception:
        logger.warning("Failed to load revealed location names", exc_info=True)

    # ── Geography context: location descriptions + spatial evidence ──
    geo_context: list[dict] = []
    for fact in facts:
        entries: list[dict] = []
        for loc in fact.locations:
            if loc.description:
                entries.append({
                    "type": "location",
                    "name": loc.name,
                    "text": loc.description,
                })
        for sr in fact.spatial_relationships:
            if sr.narrative_evidence:
                entries.append({
                    "type": "spatial",
                    "name": f"{sr.source} → {sr.target}",
                    "text": sr.narrative_evidence,
                })
        if entries:
            geo_context.append({"chapter": fact.chapter_id, "entries": entries})

    # ── Detect location hierarchy conflicts (reuse loaded facts, no extra DB query) ──
    location_conflicts: list[dict] = []
    try:
        parsed_for_conflicts = [
            (f.chapter_id, f.model_dump()) for f in facts
        ]
        raw_conflicts = _detect_location_conflicts(parsed_for_conflicts)
        location_conflicts = [c.to_dict() for c in raw_conflicts]
    except Exception:
        logger.warning("Failed to detect location conflicts for map", exc_info=True)

    # Compute canvas_size for API response
    _ws_scale = ws.spatial_scale if ws else None
    _resp_cw, _resp_ch = SPATIAL_SCALE_CANVAS.get(
        _ws_scale or "", (CANVAS_WIDTH, CANVAS_HEIGHT)
    ) if ws else (CANVAS_WIDTH, CANVAS_HEIGHT)

    result: dict = {
        "locations": locations,
        "trajectories": dict(trajectories),
        "spatial_constraints": spatial_constraints,
        "layout": layout_data,
        "layout_mode": layout_mode,
        "terrain_url": terrain_url if not layer_id else None,
        "region_boundaries": region_boundaries,
        "portals": portals_response,
        "revealed_location_names": revealed_names,
        "spatial_scale": _ws_scale,
        "canvas_size": {"width": _resp_cw, "height": _resp_ch},
        "geography_context": geo_context,
        "location_conflicts": location_conflicts,
    }
    if geo_coords_raw:
        # Apply user lat/lng overrides on top of auto-resolved coordinates
        geo_overrides = await _load_geo_overrides(novel_id)
        for loc_name, (lat, lng) in geo_overrides.items():
            geo_coords_raw[loc_name] = {"lat": lat, "lng": lng}
        result["geo_coords"] = geo_coords_raw

    # Include world_structure summary and layer_layouts when no specific layer requested
    if not layer_id:
        result["world_structure"] = ws_summary
        result["layer_layouts"] = layer_layouts

    return result


def _build_ws_summary(ws) -> dict:
    """Build a concise world_structure summary for the API response."""
    layer_summaries = []
    for layer in ws.layers:
        # Count locations assigned to this layer
        loc_count = sum(
            1 for lid in ws.location_layer_map.values()
            if lid == layer.layer_id
        )
        # Merge layers with ≤1 location into the main world (except overworld)
        merged = (
            layer.layer_id != "overworld"
            and loc_count <= 1
        )
        layer_summaries.append({
            "layer_id": layer.layer_id,
            "name": layer.name,
            "layer_type": layer.layer_type.value if hasattr(layer.layer_type, "value") else str(layer.layer_type),
            "location_count": loc_count,
            "region_count": len(layer.regions),
            "merged": merged,
        })
    return {"layers": layer_summaries}


async def _load_cached_layer_layout(
    novel_id: str, layer_id: str, chapter_hash: str,
) -> dict | None:
    """Load a cached layer layout from the layer_layouts table."""
    return await world_structure_store.load_layer_layout(
        novel_id, layer_id, chapter_hash,
    )


async def _save_cached_layer_layout(
    novel_id: str, layer_id: str, chapter_hash: str,
    layout_items: list[dict], layout_mode: str,
) -> None:
    """Cache a layer layout to the layer_layouts table."""
    await world_structure_store.save_layer_layout(
        novel_id, layer_id, chapter_hash,
        json.dumps(layout_items, ensure_ascii=False),
        layout_mode,
    )


async def _load_user_overrides(novel_id: str) -> dict[str, tuple[float, float]]:
    """Load user-adjusted coordinates for a novel."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT location_name, x, y FROM map_user_overrides WHERE novel_id = ?",
            (novel_id,),
        )
        rows = await cursor.fetchall()
        return {row["location_name"]: (row["x"], row["y"]) for row in rows}
    finally:
        await conn.close()


async def _load_locked_parents(novel_id: str) -> dict[str, str]:
    """Load locked parent assignments from user overrides."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT location_name, locked_parent FROM map_user_overrides "
            "WHERE novel_id = ? AND constraint_type = 'locked' AND locked_parent IS NOT NULL",
            (novel_id,),
        )
        rows = await cursor.fetchall()
        return {row["location_name"]: row["locked_parent"] for row in rows}
    finally:
        await conn.close()


async def save_user_override(
    novel_id: str, location_name: str, x: float, y: float,
    *, lat: float | None = None, lng: float | None = None,
    constraint_type: str = "position", locked_parent: str | None = None,
) -> None:
    """Save or update a user coordinate override and invalidate layout cache."""
    conn = await get_connection()
    try:
        await conn.execute(
            """INSERT INTO map_user_overrides
               (novel_id, location_name, x, y, lat, lng, constraint_type, locked_parent, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT (novel_id, location_name)
               DO UPDATE SET x=excluded.x, y=excluded.y,
                             lat=excluded.lat, lng=excluded.lng,
                             constraint_type=excluded.constraint_type,
                             locked_parent=excluded.locked_parent,
                             updated_at=datetime('now')""",
            (novel_id, location_name, x, y, lat, lng, constraint_type, locked_parent),
        )
        # Invalidate all cached layouts for this novel
        await conn.execute(
            "DELETE FROM map_layouts WHERE novel_id = ?", (novel_id,),
        )
        await conn.commit()
    finally:
        await conn.close()


async def _load_geo_overrides(novel_id: str) -> dict[str, tuple[float, float]]:
    """Load user-adjusted geographic (lat/lng) overrides for a novel."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT location_name, lat, lng FROM map_user_overrides "
            "WHERE novel_id = ? AND lat IS NOT NULL AND lng IS NOT NULL",
            (novel_id,),
        )
        rows = await cursor.fetchall()
        return {row["location_name"]: (row["lat"], row["lng"]) for row in rows}
    finally:
        await conn.close()


async def invalidate_layout_cache(novel_id: str) -> None:
    """Invalidate layout cache when chapter facts are updated."""
    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM map_layouts WHERE novel_id = ?", (novel_id,),
        )
        await conn.commit()
    finally:
        await conn.close()
    # Also invalidate layer-level layout cache
    await world_structure_store.delete_layer_layouts(novel_id)


async def _compute_or_load_layout(
    novel_id: str,
    chapter_hash: str,
    locations: list[dict],
    spatial_constraints: list[dict],
    first_chapter: dict[str, int] | None = None,
    location_region_bounds: dict[str, tuple[float, float, float, float]] | None = None,
) -> tuple[list[dict], str, str | None]:
    """Load cached layout or compute a new one.

    Returns (layout_list, layout_mode, terrain_url).
    """
    # Try loading from cache
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT layout_json, layout_mode, terrain_path FROM map_layouts WHERE novel_id = ? AND chapter_hash = ?",
            (novel_id, chapter_hash),
        )
        row = await cursor.fetchone()
        if row:
            layout_data = json.loads(row["layout_json"])
            terrain_path = row["terrain_path"]
            terrain_url = f"/api/novels/{novel_id}/map/terrain" if terrain_path else None
            return layout_data, row["layout_mode"], terrain_url
    finally:
        await conn.close()

    if not locations:
        return [], "hierarchy", None

    # Load user overrides
    user_overrides = await _load_user_overrides(novel_id)

    # Compute layout in thread pool to avoid blocking the event loop
    solver = ConstraintSolver(
        locations, spatial_constraints, user_overrides,
        first_chapter=first_chapter,
        location_region_bounds=location_region_bounds,
    )
    layout_coords, layout_mode = await asyncio.to_thread(solver.solve)
    layout_data = layout_to_list(layout_coords, locations)

    # Generate terrain image in thread pool (only for constraint mode with enough locations)
    terrain_path = None
    if layout_mode == "constraint" and len(layout_coords) >= 3:
        terrain_path = await asyncio.to_thread(
            generate_terrain, locations, layout_coords, novel_id
        )

    terrain_url = f"/api/novels/{novel_id}/map/terrain" if terrain_path else None

    # Cache the result
    conn = await get_connection()
    try:
        await conn.execute(
            """INSERT INTO map_layouts (novel_id, chapter_hash, layout_json, layout_mode, terrain_path, created_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT (novel_id, chapter_hash)
               DO UPDATE SET layout_json=excluded.layout_json, layout_mode=excluded.layout_mode,
                            terrain_path=excluded.terrain_path, created_at=datetime('now')""",
            (novel_id, chapter_hash, json.dumps(layout_data, ensure_ascii=False), layout_mode, terrain_path),
        )
        await conn.commit()
    finally:
        await conn.close()

    return layout_data, layout_mode, terrain_url


# ── Timeline (Events) ────────────────────────────


async def get_timeline_data(
    novel_id: str, chapter_start: int, chapter_end: int
) -> dict:
    facts = await _load_facts_in_range(novel_id, chapter_start, chapter_end)

    events: list[dict] = []
    swimlanes: dict[str, list[int]] = defaultdict(list)
    seen_characters: set[str] = set()

    event_id = 0

    def _add(summary: str, etype: str, importance: str,
             participants: list[str], location: str | None, ch: int) -> None:
        nonlocal event_id
        is_major = len(participants) >= 3
        if is_major and importance == "medium":
            importance = "high"
        events.append({
            "id": event_id,
            "chapter": ch,
            "summary": summary,
            "type": etype,
            "importance": importance,
            "participants": participants,
            "location": location,
            "is_major": is_major,
        })
        for p in participants:
            swimlanes[p].append(event_id)
        event_id += 1

    for fact in facts:
        ch = fact.chapter_id

        # ── Original events (战斗/成长/社交/旅行/其他) ──
        for ev in fact.events:
            _add(ev.summary, ev.type, ev.importance,
                 ev.participants, ev.location, ch)

        # ── Derived: 角色登场 (character first appearance) ──
        for char in fact.characters:
            if char.name not in seen_characters:
                seen_characters.add(char.name)
                loc = char.locations_in_chapter[0] if char.locations_in_chapter else None
                _add(f"{char.name} 首次登场", "角色登场", "medium",
                     [char.name], loc, ch)

        # ── Derived: 物品交接 (item events) ──
        for ie in fact.item_events:
            participants = [ie.actor]
            if ie.recipient:
                participants.append(ie.recipient)
            summary = f"{ie.actor} {ie.action} {ie.item_name}"
            if ie.recipient:
                summary += f" → {ie.recipient}"
            _add(summary, "物品交接", "medium", participants, None, ch)

        # ── Derived: 组织变动 (org events) ──
        for oe in fact.org_events:
            participants = []
            if oe.member:
                participants.append(oe.member)
            summary = f"{oe.member or '?'} {oe.action} {oe.org_name}"
            if oe.role:
                summary += f" ({oe.role})"
            _add(summary, "组织变动", "medium", participants, None, ch)

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
    alias_map = await build_alias_map(novel_id)

    # org_name -> {name, type}
    org_info: dict[str, dict] = {}
    # org_name -> {person_name -> {person, role, status}}
    org_members: dict[str, dict[str, dict]] = defaultdict(dict)
    org_relations: list[dict] = []

    # ── Source 1: org_events (explicit membership changes) ──
    for fact in facts:
        ch = fact.chapter_id

        for oe in fact.org_events:
            org_name = alias_map.get(oe.org_name, oe.org_name)
            if org_name not in org_info:
                org_info[org_name] = {"name": org_name, "type": oe.org_type}

            if oe.member:
                member = alias_map.get(oe.member, oe.member)
                existing = org_members[org_name].get(member)
                # Keep the latest action; prefer explicit role over None
                if existing is None or oe.role:
                    org_members[org_name][member] = {
                        "person": member,
                        "role": oe.role or (existing["role"] if existing else ""),
                        "status": oe.action,
                    }

            if oe.org_relation:
                other = alias_map.get(oe.org_relation.other_org, oe.org_relation.other_org)
                org_relations.append({
                    "source": org_name,
                    "target": other,
                    "type": oe.org_relation.type,
                    "chapter": ch,
                })
                # Ensure the related org is also tracked
                if other not in org_info:
                    org_info[other] = {
                        "name": other,
                        "type": "组织",
                    }

    # ── Source 2: locations with org-like types ──
    # Many sects/factions appear as locations (type="门派"/"帮派" etc.)
    # Characters visiting these locations are associated as members.
    org_locations: set[str] = set()  # canonical location names that are orgs
    for fact in facts:
        for loc in fact.locations:
            loc_canonical = alias_map.get(loc.name, loc.name)
            if _is_org_type(loc.type) and loc_canonical not in org_info:
                org_info[loc_canonical] = {"name": loc_canonical, "type": loc.type}
            if _is_org_type(loc.type):
                org_locations.add(loc_canonical)

    # ── Source 3: characters at org-locations ──
    for fact in facts:
        for char in fact.characters:
            char_canonical = alias_map.get(char.name, char.name)
            for loc_name in char.locations_in_chapter:
                loc_canonical = alias_map.get(loc_name, loc_name)
                if loc_canonical in org_locations:
                    if char_canonical not in org_members[loc_canonical]:
                        org_members[loc_canonical][char_canonical] = {
                            "person": char_canonical,
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
