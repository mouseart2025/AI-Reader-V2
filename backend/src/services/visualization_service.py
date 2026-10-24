"""Visualization data aggregation from ChapterFacts.

Provides data for 4 views: graph, map, timeline, factions.
All functions accept chapter_start/chapter_end to filter by range.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
from collections import Counter, defaultdict

from src.db import world_structure_store
from src.db.sqlite_db import get_connection
from src.extraction.fact_validator import _LOCATION_NAME_NORMALIZE
from src.models.chapter_fact import ChapterFact, classify_spatial_relation
from src.models.world_structure import LayerType
from src.services.alias_resolver import build_alias_map
from src.services.conflict_detector import (
    _detect_direction_conflicts,
    _detect_distance_conflicts,
    _detect_location_conflicts,
)
from src.services.geo_resolver import (
    auto_resolve as geo_auto_resolve,
)
from src.services.geo_resolver import (
    place_unresolved_geo_coords,
)
from src.services.map_layout_service import (
    _TERRAIN_VERSION,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    SPATIAL_SCALE_CANVAS,
    ConstraintSolver,
    _layout_regions,
    compute_chapter_hash,
    compute_layered_layout,
    generate_landmasses,
    generate_rivers,
    generate_roads,
    generate_terrain,
    generate_voronoi_boundaries,
    layout_to_list,
    place_unresolved_near_neighbors,
    terrain_path_for,
    terrain_url_for,
)
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
    from src.services.name_authority import (
        CANONICAL_BLOCKLIST,
        GENERIC_PERSON_ALIASES,
        is_surname_plus_shi,
    )
    from src.services.name_authority import (
        is_generic_person as _is_generic_person,
    )
    from src.services.relation_utils import classify_relation_category

    facts = await _load_facts_in_range(novel_id, chapter_start, chapter_end)
    alias_map = await build_alias_map(novel_id)

    # Build the set of "known canonical names" — names that appear as values
    # in alias_map. Override-rescued primaries (e.g. 薛姨妈, 王夫人 in 红楼梦)
    # are dict-registered characters.
    known_canonicals = set(alias_map.values())

    def _skip_person_name(name: str) -> bool:
        """v0.71.1: narrow post-aggregation filter for generic names that
        remain in chapter_facts persisted BEFORE the new safety rules were
        added (e.g. 李氏 / 太太 / 老太太 / 贾母等 / 飞东洋...鹦哥).

        Uses a NARROW blocklist to avoid false positives on override-rescued
        primaries like 王夫人/薛姨妈 (which end with "夫人/姨妈" and hit the
        structural spouse-pattern rule but are real characters).
        """
        # Collective/generic references are never real entities — checked
        # BEFORE the canonical trust guard, since such a name can wrongly become
        # a canonical with aliases merged in (e.g. 群妖). _is_generic_person
        # ignores structural level-0, so override-rescued primaries (王夫人/
        # 薛姨妈) are unaffected.
        if _is_generic_person(name) is not None:
            return True
        if name in alias_map or name in known_canonicals:
            return False  # known entity — trust alias_map
        # Explicit blocklist (太太/老太太/奶奶/那呆子/取经人/老孙 ...)
        if name in CANONICAL_BLOCKLIST:
            return True
        # Kinship/generic person terms (覆盖 李氏/王氏 会误伤真实)
        # 只过滤那些明确的"X等"/集合引用
        if name in GENERIC_PERSON_ALIASES:
            return True
        # 姓+氏 模式 (李氏/王氏/甄氏)
        if is_surname_plus_shi(name):
            return True
        # "X等" 集合引用 (贾母等/宝玉等)
        if len(name) >= 3 and name.endswith("等"):
            return True
        # 长描述性名称 (飞东洋游普世感恩行孝黄毛红嘴白鹦哥)
        return len(name) >= 10

    # Collect person nodes
    person_chapters: dict[str, set[int]] = defaultdict(set)
    person_org: dict[str, str] = {}
    # Track all aliases seen per canonical name
    person_aliases: dict[str, set[str]] = defaultdict(set)

    # Collect edges (person_a, person_b) -> relation info
    edge_map: dict[tuple[str, str], dict] = {}

    # ── Org attribution: collect from org_events + org-type locations ──
    _ORG_ACTION_JOIN = {"加入", "晋升", "出现", "创建", "成立"}
    org_locations: set[str] = set()  # location names that are org-like
    person_org_visits: dict[str, Counter] = defaultdict(Counter)  # person → org → visit count

    for fact in facts:
        ch = fact.chapter_id

        for char in fact.characters:
            if _skip_person_name(char.name):
                continue
            canonical = alias_map.get(char.name, char.name)
            person_chapters[canonical].add(ch)
            if char.name != canonical:
                person_aliases[canonical].add(char.name)

        # Track org membership from org_events
        for oe in fact.org_events:
            if oe.member and oe.action in _ORG_ACTION_JOIN:
                member = alias_map.get(oe.member, oe.member)
                org = alias_map.get(oe.org_name, oe.org_name)
                person_org[member] = org

        # Identify org-type locations
        for loc in fact.locations:
            loc_canonical = alias_map.get(loc.name, loc.name)
            if _is_org_type(loc.type):
                org_locations.add(loc_canonical)

        # Track character visits to org-type locations
        for char in fact.characters:
            if _skip_person_name(char.name):
                continue
            canonical = alias_map.get(char.name, char.name)
            for loc_name in char.locations_in_chapter:
                loc_canonical = alias_map.get(loc_name, loc_name)
                if loc_canonical in org_locations:
                    person_org_visits[canonical][loc_canonical] += 1

        for rel in fact.relationships:
            if _skip_person_name(rel.person_a) or _skip_person_name(rel.person_b):
                continue
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

    # ── Fallback org attribution from location visits ──
    for person, org_counts in person_org_visits.items():
        if person not in person_org and org_counts:
            # Assign to the org-location visited most frequently
            best_org = org_counts.most_common(1)[0][0]
            if org_counts[best_org] >= 2:  # require ≥ 2 visits
                person_org[person] = best_org

    from src.services.alias_resolver import get_override_targets
    from src.services.hallucination_filter import get_ungrounded_persons

    # v0.72: filter hallucination islands (issue #30) — person nodes whose
    # canonical name and aliases never literally appear in the source text
    # (e.g. characters leaked from the LLM's pretrained knowledge of other
    # novels). Checked against the full book text, so real characters are
    # never filtered just because they appear outside the selected range.
    ungrounded = await get_ungrounded_persons(novel_id, person_chapters.keys(), alias_map)
    if ungrounded:
        for name in ungrounded:
            person_chapters.pop(name, None)
            person_org.pop(name, None)
            person_aliases.pop(name, None)
        edge_map = {
            k: v
            for k, v in edge_map.items()
            if k[0] not in ungrounded and k[1] not in ungrounded
        }

    override_targets = await get_override_targets(novel_id)

    # 实体级可见性 override(issue #66 Epic 1):隐藏的实体、或改型到非
    # person 的实体从图谱剔除(节点 + 相关边)。无 override 时零行为变化。
    from src.services.entity_visibility import (
        expand_hidden,
        expand_retype,
        get_visibility_overrides,
    )

    hidden, retype = await get_visibility_overrides(novel_id)
    if hidden or retype:
        hidden_r = expand_hidden(alias_map, hidden)
        retype_r = expand_retype(alias_map, retype)
        gone = {
            name
            for name in person_chapters
            if name in hidden_r or retype_r.get(name, "person") != "person"
        }
        if gone:
            for name in gone:
                person_chapters.pop(name, None)
                person_org.pop(name, None)
                person_aliases.pop(name, None)
            edge_map = {
                k: v
                for k, v in edge_map.items()
                if k[0] not in gone and k[1] not in gone
            }

    nodes = [
        {
            "id": name,
            "name": name,
            "type": "person",
            "chapter_count": len(chs),
            "org": person_org.get(name, ""),
            "aliases": sorted(person_aliases.get(name, set())),
            "edit_status": "edited" if name in override_targets else "",
        }
        for name, chs in person_chapters.items()
    ]
    nodes.sort(key=lambda n: -n["chapter_count"])

    edges_out: list[dict] = []
    category_counts: Counter = Counter()
    for e in edge_map.values():
        primary_type = e["type_counts"].most_common(1)[0][0]
        category = classify_relation_category(primary_type)
        category_counts[category] += 1
        edges_out.append({
            "source": e["source"],
            "target": e["target"],
            "relation_type": primary_type,
            "all_types": [t for t, _ in e["type_counts"].most_common()],
            "weight": len(e["chapters"]),
            "chapters": sorted(e["chapters"]),
            "category": category,
        })

    # Compute a suggested min_edge_weight for large graphs
    max_weight = max((e["weight"] for e in edges_out), default=1)
    suggested_min_edge = 1
    if len(edges_out) > 500:
        suggested_min_edge = 2
    if len(edges_out) > 2000:
        suggested_min_edge = max(3, max_weight // 10)

    # Relation type stats for frontend display
    type_counts: Counter = Counter()
    for e in edges_out:
        type_counts[e["relation_type"]] += 1

    return {
        "nodes": nodes,
        "edges": edges_out,
        "max_edge_weight": max_weight,
        "suggested_min_edge_weight": suggested_min_edge,
        "category_counts": dict(category_counts),
        "type_counts": dict(type_counts.most_common(20)),
        "filtered_ungrounded_persons": sorted(ungrounded),
    }


# ── Map (Location Hierarchy + Trajectories) ──────


# Keywords for ocean/sea type locations (should stay in sea)
_OCEAN_ICON_TYPES = {"ocean", "water", "island"}
_OCEAN_TYPE_KEYWORDS_VIS = ("海", "洋", "湖泊", "河流", "海域", "水域", "大海", "海洋")
# Broader water type keywords for parent-chain water detection
_WATER_TYPE_KEYWORDS = ("海", "洋", "湖", "江", "河", "溪", "泉", "潭", "池", "水域", "海域")


def _is_water_location(
    name: str,
    loc_lookup: dict[str, dict],
    _cache: dict[str, bool] | None = None,
    _depth: int = 0,
) -> bool:
    """Triple water detection: icon + type keywords + parent chain (max 5 levels).

    Returns True if this location should stay in sea (is a water body).
    """
    if _cache is not None and name in _cache:
        return _cache[name]

    loc = loc_lookup.get(name)
    if not loc:
        return False

    # Layer 1: icon-based detection
    icon = loc.get("icon", "generic")
    if icon in _OCEAN_ICON_TYPES:
        if _cache is not None:
            _cache[name] = True
        return True

    # Layer 2: type keyword detection
    loc_type = loc.get("type", "")
    if any(kw in loc_type for kw in _OCEAN_TYPE_KEYWORDS_VIS):
        if _cache is not None:
            _cache[name] = True
        return True

    # Layer 3: parent chain — if any ancestor is a water body, this is too
    if _depth < 5:
        parent = loc.get("parent", "")
        if parent and parent != name:
            if _is_water_location(parent, loc_lookup, _cache, _depth + 1):
                if _cache is not None:
                    _cache[name] = True
                return True

    if _cache is not None:
        _cache[name] = False
    return False


def _snap_sea_orphans_to_land(
    layout_data: list[dict],
    locations: list[dict],
    landmass_result: dict,
    spatial_constraints: list[dict],
) -> int:
    """Snap non-ocean locations that fell into the sea toward nearby land locations.

    Triple water detection (icon + type + parent chain). Recursive snap ensures
    children of snapped parents also move to land. Returns total snap count.
    """
    import math

    import numpy as np
    from scipy.spatial import KDTree

    land_mask = landmass_result.get("_land_mask")
    cell_size = landmass_result.get("_cell_size", 8.0)
    if land_mask is None:
        return 0

    grid_h, grid_w = land_mask.shape

    # Build location lookup for triple water detection
    loc_lookup: dict[str, dict] = {loc["name"]: loc for loc in locations}
    water_cache: dict[str, bool] = {}

    # Build ocean name set using triple detection
    ocean_names: set[str] = set()
    for loc in locations:
        if _is_water_location(loc["name"], loc_lookup, water_cache):
            ocean_names.add(loc["name"])

    def _is_on_land(x: float, y: float) -> bool:
        gxi = round(x / cell_size)
        gyi = round(y / cell_size)
        if 0 <= gyi < grid_h and 0 <= gxi < grid_w:
            return bool(land_mask[gyi, gxi])
        return False

    loc_parent = {loc["name"]: loc.get("parent") for loc in locations}
    layout_map = {item["name"]: item for item in layout_data if not item.get("is_portal")}

    golden_angle = math.pi * (3 - math.sqrt(5))
    anchor_counter: dict[str, int] = {}

    def _snap_one(item: dict, anchor: dict) -> None:
        """Snap a single item near an anchor using sunflower distribution."""
        anchor_name = anchor["name"]
        count = anchor_counter.get(anchor_name, 0)
        anchor_counter[anchor_name] = count + 1

        angle = golden_angle * count
        base_r = 40 + 10 * (count // 8)
        new_x = anchor["x"] + base_r * math.cos(angle)
        new_y = anchor["y"] + base_r * math.sin(angle)

        if not _is_on_land(new_x, new_y):
            new_x = anchor["x"] * 0.8 + new_x * 0.2
            new_y = anchor["y"] * 0.8 + new_y * 0.2

        item["x"] = round(new_x, 1)
        item["y"] = round(new_y, 1)

    # ── Recursive snap loop (max 3 rounds) ──
    total_snapped = 0
    rounds_done = 0
    for round_num in range(3):
        # Re-classify on each round (newly snapped parents become land anchors)
        land_items: list[dict] = []
        sea_orphans: list[dict] = []

        for item in layout_data:
            name = item.get("name", "")
            if item.get("is_portal") or not name:
                continue
            if name in ocean_names:
                continue
            # Macro anchors are never "orphans": if the mask says ocean at a
            # continent's position, the mask is wrong, not the geography.
            # Snapping them teleports the map's skeleton into the nearest
            # blob (西游记: all four continents collapsed to one point).
            _tier = (loc_lookup.get(name) or {}).get("tier", "")
            if _tier in ("world", "continent", "kingdom"):
                continue
            if _is_on_land(item["x"], item["y"]):
                land_items.append(item)
            else:
                sea_orphans.append(item)

        if not sea_orphans or not land_items:
            break

        land_coords = np.array([(it["x"], it["y"]) for it in land_items], dtype=np.float64)
        land_tree = KDTree(land_coords)

        round_snapped = 0
        for item in sea_orphans:
            name = item["name"]
            # Priority 1: snap to parent if parent is on land
            parent = loc_parent.get(name)
            anchor = None
            if parent and parent in layout_map:
                p_item = layout_map[parent]
                if _is_on_land(p_item["x"], p_item["y"]):
                    anchor = p_item

            # Priority 2: nearest land location
            if anchor is None:
                _, idx = land_tree.query([item["x"], item["y"]])
                anchor = land_items[idx]

            _snap_one(item, anchor)
            round_snapped += 1

        total_snapped += round_snapped
        rounds_done = round_num + 1
        if round_snapped == 0:
            break

        logger.debug("Sea snap round %d: snapped %d locations", round_num + 1, round_snapped)

    if total_snapped:
        logger.info("Total sea-orphan snap: %d locations (%d rounds)", total_snapped, rounds_done)

    return total_snapped


def _snap_sea_bodies_to_water(
    layout_data: list[dict],
    locations: list[dict],
    landmass_result: dict,
    canvas_width: float,
    canvas_height: float,
) -> int:
    """Snap sea/ocean locations that sit on dry land to the nearest water.

    Mirror of `_snap_sea_orphans_to_land`, which only rescues land locations
    from the sea — nothing moves a sea off the land. Type drift makes this
    reachable: 南海 came back from extraction typed "region", so the landmass
    generator treated it as a land anchor and the coastline bent *towards*
    it instead of around it.

    Works from the persisted coastline polygons rather than `_land_mask`
    (which only exists on the freshly-generated path), so cached maps are
    covered too. Only sea-named bodies are moved: icon=water/ocean plus a
    sea type or a *海/*洋 name. Lakes are deliberately excluded — a 湖 on
    land is correct. Rivers are excluded the same way. Returns snap count.
    """
    import numpy as np
    from matplotlib.path import Path as _MplPath
    from scipy.ndimage import distance_transform_edt

    landmasses = landmass_result.get("landmasses") or []
    if not landmasses or not layout_data:
        return 0

    loc_lookup = {loc["name"]: loc for loc in locations}

    candidates = [
        item for item in layout_data
        if not item.get("is_portal") and _is_sea_body_loc(loc_lookup.get(item.get("name", "")) or {})
    ]
    if not candidates:
        return 0

    # Rasterise the land polygons onto a coarse grid, then let the distance
    # transform name each land cell's nearest water cell.
    gs = 16.0
    gw = max(1, int(canvas_width / gs))
    gh = max(1, int(canvas_height / gs))
    xs = (np.arange(gw) + 0.5) * gs
    ys = (np.arange(gh) + 0.5) * gs
    xx, yy = np.meshgrid(xs, ys)
    grid_pts = np.column_stack([xx.ravel(), yy.ravel()])
    land = np.zeros(len(grid_pts), dtype=bool)
    for lm in landmasses:
        ring = lm.get("coastline") or []
        if len(ring) < 3:
            continue
        inside = _MplPath(np.asarray(ring)).contains_points(grid_pts)
        for hole in lm.get("holes") or []:
            if len(hole) >= 3:
                inside &= ~_MplPath(np.asarray(hole)).contains_points(grid_pts)
        land |= inside
    land2d = land.reshape(gh, gw)
    if land2d.all() or (~land2d).all():
        return 0
    nearest_water = distance_transform_edt(
        land2d, return_distances=False, return_indices=True,
    )

    # Exact point-in-polygon land test for the candidate's own point. The
    # gs=16 raster above samples cell *centres*: a sea sitting on a land
    # tip narrower than a cell reads as water here while the plausibility
    # audit (exact) still flags it — 南海 sat exactly on such a tip and was
    # skipped by every run of this snap. Decision by exact test, target by
    # raster EDT.
    lm_paths = [
        (_MplPath(np.asarray(lm["coastline"])),
         [_MplPath(np.asarray(h)) for h in lm.get("holes") or [] if len(h) >= 3])
        for lm in landmasses
        if len(lm.get("coastline") or []) >= 3
    ]

    def _exact_on_land(x: float, y: float) -> bool:
        pt = [(x, y)]
        for ring_path, hole_paths in lm_paths:
            if ring_path.contains_points(pt)[0] and not any(
                hp.contains_points(pt)[0] for hp in hole_paths
            ):
                return True
        return False

    snapped = 0
    for item in candidates:
        if not _exact_on_land(item["x"], item["y"]):
            continue
        ci = int(item["y"] / gs)
        cj = int(item["x"] / gs)
        if not (0 <= ci < gh and 0 <= cj < gw):
            continue
        ti, tj = nearest_water[:, ci, cj]
        if (ti, tj) == (ci, cj):
            # Exact says land but the cell centre sampled water: borrow the
            # nearest land cell's EDT target within a small window.
            found = False
            for r in range(1, 6):
                for di in range(-r, r + 1):
                    for dj in range(-r, r + 1):
                        ni, nj = ci + di, cj + dj
                        if 0 <= ni < gh and 0 <= nj < gw and land2d[ni, nj]:
                            ti, tj = nearest_water[:, ni, nj]
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            if not found:
                continue
        item["x"] = round(float((tj + 0.5) * gs), 1)
        item["y"] = round(float((ti + 0.5) * gs), 1)
        snapped += 1
        logger.info(
            "Snapped sea body %s from land to water at (%.0f, %.0f)",
            item["name"], item["x"], item["y"],
        )
    return snapped


_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


def _is_sea_body_loc(loc: dict) -> bool:
    """Sea/ocean body detection for map hygiene rules.

    icon=water/ocean plus a sea type or a *海/*洋 name. Islands, rivers and
    lakes are not seas (an island is land, a river runs through it, a 湖 sits
    in it). The name suffix exists because type drift (南海 came back typed
    "region") makes the type field unreliable on exactly the cases these
    rules protect against; cities like 上海/威海 carry no water icon and so
    cannot match.
    """
    icon = loc.get("icon", "generic")
    if icon == "island":
        return False
    if icon == "ocean":
        return True
    if icon != "water":
        return False
    loc_type = loc.get("type", "")
    return loc_type in ("海", "洋", "海洋", "大海", "海域") or loc.get("name", "").endswith(("海", "洋"))


# Cardinal direction hinted by a sea's name: 南海 should escape *south* of the
# land it was misplaced into, not just away from the nearest centroid (which
# for a sea sitting on a continent's centre is any direction at all).
_SEA_NAME_DIRECTION = (
    ("东北", (0.7, -0.7)), ("东南", (0.7, 0.7)), ("西北", (-0.7, -0.7)), ("西南", (-0.7, 0.7)),
    ("东", (1.0, 0.0)), ("南", (0.0, 1.0)), ("西", (-1.0, 0.0)), ("北", (0.0, -1.0)),
)


def _relocate_misplaced_seas(
    layout_data: list[dict],
    locations: list[dict],
    canvas_width: float,
    canvas_height: float,
) -> int:
    """Push sea/ocean locations that sit inside (or right against) a land
    cluster out into open water, along the name's cardinal direction when the
    name carries one.

    The layout engine places seas with the same scatter rules as land
    locations, so a sea can land on the continent it should border — measured:
    南海 43 px from 南赡部洲's anchor, i.e. deep inside the southern continent,
    and 南洋苦海 64 px from 盱眙山. Downstream hole carving then had to choose
    between an invisible 8 px puddle and eating the continent; relocating the
    sea itself is the honest fix. Runs before landmass generation on the cold
    path; `_snap_sea_bodies_to_water` stays as the polygon-level safety net.

    A sea is "misplaced" when its nearest land anchor is closer than the land
    that anchor will paint (base radius × its tier weight, with a margin).
    Escape search: rays fanned around the preferred direction, first point far
    enough from every land anchor wins. Returns the number of moved seas.
    """
    import numpy as np
    from scipy.spatial import KDTree

    from src.services.map_layout_service import _TIER_WEIGHT, landmass_base_radius

    loc_lookup = {loc["name"]: loc for loc in locations}
    land_items = [
        it for it in layout_data
        if not it.get("is_portal") and not _is_sea_body_loc(loc_lookup.get(it.get("name", "")) or {})
    ]
    sea_items = [
        it for it in layout_data
        if not it.get("is_portal") and _is_sea_body_loc(loc_lookup.get(it.get("name", "")) or {})
    ]
    if not sea_items or len(land_items) < 3:
        return 0

    land_pts = np.array([[it["x"], it["y"]] for it in land_items])
    land_tree = KDTree(land_pts)
    nn, _ = land_tree.query(land_pts, k=2)
    base_r = landmass_base_radius(canvas_width, canvas_height, float(np.median(nn[:, 1])))
    tiers = {loc.get("name", ""): loc.get("tier", "site") for loc in locations}
    margin = base_r

    moved = 0
    for _pass in range(2):  # a moved sea can land against a different cluster
        pass_moved = False
        for item in sea_items:
            name = item.get("name", "")
            x, y = float(item["x"]), float(item["y"])
            dists, idxs = land_tree.query([[x, y]], k=min(6, len(land_items)))
            dists, idxs = np.atleast_1d(dists[0]), np.atleast_1d(idxs[0])
            near_names = [land_items[int(i)].get("name", "") for i in idxs]
            w_near = max(_TIER_WEIGHT.get(tiers.get(nm, "site"), 1.0) for nm in near_names[:1])
            clearance = base_r * max(w_near, 1.5) * 1.2
            if dists[0] >= clearance:
                continue

            # Preferred escape direction: cardinal from the name, else away
            # from the centroid of the nearest land anchors.
            base_dir = None
            for kw, vec in _SEA_NAME_DIRECTION:
                if kw in name:
                    base_dir = np.array(vec, dtype=np.float64)
                    break
            if base_dir is None:
                cx = float(np.mean([land_items[int(i)]["x"] for i in idxs]))
                cy = float(np.mean([land_items[int(i)]["y"] for i in idxs]))
                vx, vy = x - cx, y - cy
                norm = math.hypot(vx, vy)
                base_dir = np.array([vx / norm, vy / norm]) if norm >= 1 else np.array([0.0, 1.0])
            base_dir = base_dir / np.linalg.norm(base_dir)

            # Escape search: rays fanned around the preferred direction.
            # Clearance is attempted at full strength first, then relaxed —
            # 南海 sits inside the southern continent, which touches the canvas
            # bottom edge, so no 810 px escape exists; at ~500 px the strait
            # south-east of the continent qualifies. Best-effort fallback: the
            # farthest-from-land point seen, if it improves on the current one.
            best = None  # (cost, x, y)
            fallback = (dists[0], x, y)  # (min land distance, x, y)
            for clear_frac in (1.0, 0.75, 0.55, 0.4):
                clear = max(clearance * clear_frac, base_r * 1.1)
                for deg in (0, 20, -20, 45, -45, 75, -75, 105, -105, 150, -150, 180):
                    rad = math.radians(deg)
                    dvec = np.array([
                        base_dir[0] * math.cos(rad) - base_dir[1] * math.sin(rad),
                        base_dir[0] * math.sin(rad) + base_dir[1] * math.cos(rad),
                    ])
                    step = clear - dists[0] + base_r * 0.5
                    while step <= clearance + base_r * 8:
                        nx, ny = x + dvec[0] * step, y + dvec[1] * step
                        if not (margin <= nx <= canvas_width - margin and margin <= ny <= canvas_height - margin):
                            break
                        d_land = float(land_tree.query([[nx, ny]], k=1)[0][0])
                        if d_land > fallback[0]:
                            fallback = (d_land, nx, ny)
                        if d_land >= clear:
                            cost = step + abs(deg) * base_r * 0.01
                            if best is None or cost < best[0]:
                                best = (cost, nx, ny)
                            break
                        step += base_r * 0.5
                if best is not None:
                    break
            if best is None:
                if fallback[0] <= dists[0] * 1.2:
                    continue  # nowhere meaningfully wetter; hole carving handles it
                best = (0.0, fallback[1], fallback[2])
            item["x"], item["y"] = round(best[1], 1), round(best[2], 1)
            moved += 1
            pass_moved = True
            logger.info(
                "Relocated sea %s from (%.0f, %.0f) to open water at (%.0f, %.0f)",
                name, x, y, item["x"], item["y"],
            )
        if not pass_moved:
            break
    return moved


def _continent_subtree_members(locations: list[dict], anchors: list[str]) -> dict[str, set[str]]:
    """Map each continent anchor to the names in its parent-chain subtree."""
    parent_of = {loc.get("name", ""): loc.get("parent") for loc in locations}
    anchor_set = set(anchors)

    def _root(name: str) -> str | None:
        cur, seen = name, set()
        while cur and cur not in seen and len(seen) < 12:
            seen.add(cur)
            if cur in anchor_set:
                return cur
            cur = parent_of.get(cur)
        return None

    members: dict[str, set[str]] = {a: set() for a in anchors}
    for loc in locations:
        name = loc.get("name", "")
        root = _root(name)
        if root is not None:
            members[root].add(name)
    return members


def _separate_merged_continents(
    layout_data: list[dict],
    locations: list[dict],
    landmask_info: dict,
    canvas_width: float,
    canvas_height: float,
) -> int:
    """Translate a continent subtree when its anchor shares a landmass with
    another continent's anchor.

    西游记 measures why: the layout interleaves 东胜神洲's subtree with the
    central belt, so the eastern island continent (花果山/傲来国 — overseas in
    the text) fuses with the mainland. No field radius can separate groups
    whose points touch; moving the compact subtree is the fix. The smaller
    subtree moves, away from the other anchor(s) it merged with, one step per
    call — the caller regenerates landmasses and calls again (≤3 rounds).

    Uses the pre-warp land mask for component tests; the warp only roughens
    coastlines and never merges components. Returns moved location count.
    """
    import numpy as np
    from scipy.ndimage import label as _nd_label

    from src.services.map_layout_service import (
        continent_anchor_names,
        landmass_base_radius,
    )

    land_mask = landmask_info.get("_land_mask")
    cell_size = landmask_info.get("_cell_size") or 8.0
    if land_mask is None:
        return 0
    items_by_name = {it.get("name", ""): it for it in layout_data if not it.get("is_portal")}
    anchors = [a for a in continent_anchor_names(locations, set(items_by_name)) if a in items_by_name]
    if len(anchors) < 2:
        return 0

    lab, _n = _nd_label(land_mask, structure=np.ones((3, 3), dtype=int))
    comp_of: dict[str, int] = {}
    for a in anchors:
        it = items_by_name[a]
        gi, gj = int(it["y"] / cell_size), int(it["x"] / cell_size)
        comp_of[a] = int(lab[gi, gj]) if 0 <= gi < lab.shape[0] and 0 <= gj < lab.shape[1] else 0

    by_comp: dict[int, list[str]] = {}
    for a, cid in comp_of.items():
        if cid > 0:
            by_comp.setdefault(cid, []).append(a)
    merged = [sorted(group) for group in by_comp.values() if len(group) >= 2]
    if not merged:
        return 0

    members = _continent_subtree_members(locations, anchors)
    # Distance to the nearest land cell, in px, for every water cell: the score
    # table for "how much open water is there". A merged continent escapes to
    # the best water pocket in its escape fan, not blindly away from its
    # partner — the straight away-vector ran 东胜神洲 into the canvas edge,
    # where clamping ate the whole move and three rounds changed nothing.
    from scipy.ndimage import distance_transform_edt as _edt
    from scipy.spatial import KDTree as _KDTree

    water_edt = _edt(~land_mask) * cell_size
    gh, gw = land_mask.shape
    base_r = landmass_base_radius(canvas_width, canvas_height, 0.0)

    def _water_clearance(x: float, y: float) -> float:
        gi, gj = int(y / cell_size), int(x / cell_size)
        if 0 <= gi < gh and 0 <= gj < gw:
            return float(water_edt[gi, gj])
        return 0.0

    moved = 0
    for group in merged:
        # Move the smallest subtree; the big one stays put.
        mover = min(group, key=lambda a: len(members.get(a) or {a}))
        others = [a for a in group if a != mover]
        mover_names = members.get(mover) or {mover}
        mx, my = items_by_name[mover]["x"], items_by_name[mover]["y"]
        ox = float(np.mean([items_by_name[a]["x"] for a in others]))
        oy = float(np.mean([items_by_name[a]["y"] for a in others]))
        vx, vy = mx - ox, my - oy
        norm = math.hypot(vx, vy)
        if norm < 1:
            vx, vy, norm = 1.0, 0.0, 1.0
        base_dir = (vx / norm, vy / norm)

        sub = [items_by_name[n] for n in mover_names if n in items_by_name]
        if not sub:
            continue
        # What the move must clear is not "water" in the abstract but the other
        # side's *members*: the moat carves contested cells except within
        # ~threshold*0.55 of a real location, so a bridge survives only if a
        # moved member lands within ~2× that of a mainland member.
        mainland_pts = np.array([
            [it["x"], it["y"]]
            for nm, it in items_by_name.items()
            if nm not in mover_names
        ])
        mainland_tree = _KDTree(mainland_pts)
        min_bridge = base_r * 1.1

        best = None  # (score, dx, dy)
        for deg in (0, 25, -25, 50, -50, 80, -80, 115, -115, 155, -155, 180):
            rad = math.radians(deg)
            dvec = (
                base_dir[0] * math.cos(rad) - base_dir[1] * math.sin(rad),
                base_dir[0] * math.sin(rad) + base_dir[1] * math.cos(rad),
            )
            step = base_r * 2.0
            while step <= base_r * 16:
                tx, ty = mx + dvec[0] * step, my + dvec[1] * step
                if not (150 <= tx <= canvas_width - 150 and 150 <= ty <= canvas_height - 150):
                    break
                dx, dy = tx - mx, ty - my
                new_pts = np.array([[it["x"] + dx, it["y"] + dy] for it in sub])
                d_land = float(mainland_tree.query(new_pts, k=1)[0].min())
                if d_land >= min_bridge and _water_clearance(tx, ty) >= base_r:
                    score = d_land - 0.25 * step
                    if best is None or score > best[0]:
                        best = (score, dx, dy)
                step += base_r
        if best is None:
            logger.warning("Continent %s merged with %s but no clear escape found; leaving as-is",
                           mover, ", ".join(others))
            continue
        dx, dy = best[1], best[2]

        # Clamp so the subtree bbox stays inside the canvas margins.
        xs = [it["x"] for it in sub]
        ys = [it["y"] for it in sub]
        margin = 150.0
        dx = min(dx, canvas_width - margin - max(xs)) if dx > 0 else max(dx, margin - min(xs))
        dy = min(dy, canvas_height - margin - max(ys)) if dy > 0 else max(dy, margin - min(ys))
        if math.hypot(dx, dy) < base_r:
            logger.warning("Continent %s merged but no room to move; leaving as-is", mover)
            continue
        for it in sub:
            it["x"] = round(it["x"] + dx, 1)
            it["y"] = round(it["y"] + dy, 1)
        moved += len(sub)
        logger.info(
            "Translated continent subtree %s (%d locations) by (%.0f, %.0f) to separate it from %s",
            mover, len(sub), dx, dy, ", ".join(others),
        )
    return moved

# Valid direction enum values expected by the constraint solver
_VALID_DIRECTION_VALUES = {
    "north_of", "south_of", "east_of", "west_of",
    "northeast_of", "northwest_of", "southeast_of", "southwest_of",
}

# Chinese direction → English enum
_CHINESE_DIRECTION_MAP = {
    "北": "north_of", "北方": "north_of", "北边": "north_of", "以北": "north_of",
    "北面": "north_of", "北侧": "north_of", "北向": "north_of", "在北": "north_of",
    "南": "south_of", "南方": "south_of", "南边": "south_of", "以南": "south_of",
    "南面": "south_of", "南侧": "south_of", "南向": "south_of", "在南": "south_of",
    "东": "east_of", "东方": "east_of", "东边": "east_of", "以东": "east_of",
    "东面": "east_of", "东侧": "east_of", "东向": "east_of", "在东": "east_of",
    "西": "west_of", "西方": "west_of", "西边": "west_of", "以西": "west_of",
    "西面": "west_of", "西侧": "west_of", "西向": "west_of", "在西": "west_of",
    "东北": "northeast_of", "西北": "northwest_of",
    "东南": "southeast_of", "西南": "southwest_of",
    "东北方": "northeast_of", "西北方": "northwest_of",
    "东南方": "southeast_of", "西南方": "southwest_of",
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
    loc_names_set = {loc["name"] for loc in locations}
    loc_by_name = {loc["name"]: loc for loc in locations}
    water_cache: dict[str, bool] = {}

    cleaned = []
    fixed = 0
    removed = 0
    dangling = 0

    for c in constraints:
        # ── Filter dangling references: source/target must exist in locations ──
        if c["source"] not in loc_names_set or c["target"] not in loc_names_set:
            dangling += 1
            continue

        rtype = c["relation_type"]

        # ── Fix contains inversions ──
        if classify_spatial_relation(rtype) == "hierarchy":
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

            # Dry land cannot contain a sea. "南赡部洲 contains 南海" is a
            # real extraction ("南海属于南赡部洲", evidence: "大慈悲回南海不题")
            # and it anchored the sea inside the continent through the whole
            # solver. The reverse — a sea containing an island or mountain
            # (南海 contains 落伽山) — is ordinary geography and stays.
            if _is_sea_body_loc(loc_by_name.get(c["target"]) or {}) and not _is_water_location(
                c["source"], loc_by_name, water_cache,
            ):
                removed += 1
                continue

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

    if fixed or removed or dangling:
        logger.info(
            "Constraint cleaning: fixed %d, removed %d, dangling %d, kept %d",
            fixed, removed, dangling, len(cleaned),
        )
    return cleaned


# ── Cardinal name priors ──
# Names like 东胜神洲/南海/北俱芦洲 encode their direction in the name itself.
_CARDINAL_NAME_VALUES: dict[str, str] = {
    "east": "east_of", "south": "south_of", "west": "west_of", "north": "north_of",
}


def _inject_cardinal_name_priors(
    constraints: list[dict],
    locations: list[dict],
) -> list[dict]:
    """Inject soft direction constraints for cardinal-named geography.

    The LLM extraction frequently misses what the name already says (in
    西游记, 北俱芦洲 ended up with zero constraints and drifted into the
    eastern cluster with 东胜神洲/南赡部洲). Anchor each cardinal-named
    location to its parent with a low-weight direction constraint so the
    solver spreads same-parent siblings into their name-implied quadrants.

    The prior never overrides text: when a direction constraint already
    exists between the pair (either direction), injection is skipped, and
    the low confidence keeps it below real evidence elsewhere in the graph.
    """
    from src.services.location_hint_service import strict_cardinal_direction

    loc_names = {loc["name"] for loc in locations}
    existing_pairs: set[tuple[str, str]] = set()
    for c in constraints:
        if c["relation_type"] == "direction":
            existing_pairs.add((c["source"], c["target"]))
            existing_pairs.add((c["target"], c["source"]))

    injected = 0
    for loc in locations:
        name = loc["name"]
        direction = strict_cardinal_direction(name)
        if direction is None:
            continue
        parent = loc.get("parent")
        if not parent or parent not in loc_names:
            continue
        if (name, parent) in existing_pairs:
            continue
        constraints.append({
            "source": name,
            "target": parent,
            "relation_type": "direction",
            "value": _CARDINAL_NAME_VALUES[direction],
            "confidence": "low",
            "confidence_score": 0.5,
            "source_type": "name_prior",
        })
        existing_pairs.add((name, parent))
        injected += 1

    if injected:
        logger.info("Cardinal name priors: injected %d soft direction constraints", injected)
    return constraints


# ── Direction opposites for contradiction detection ──
_DIRECTION_OPPOSITES: dict[str, str] = {
    "north_of": "south_of", "south_of": "north_of",
    "east_of": "west_of", "west_of": "east_of",
    "northeast_of": "southwest_of", "southwest_of": "northeast_of",
    "northwest_of": "southeast_of", "southeast_of": "northwest_of",
}

# 4 cardinal directions used for transitive inference (diagonal excluded)
_CARDINAL_DIRECTIONS = {"north_of", "south_of", "east_of", "west_of"}

# Tier rank for tier-gap filtering
_TIER_RANK: dict[str, int] = {
    "world": 0, "continent": 1, "kingdom": 2, "region": 3,
    "city": 4, "site": 5, "building": 6,
}


def _enhance_constraints(
    spatial_constraints: list[dict],
    trajectories: dict[str, list[dict]],
    locations: list[dict],
    completed_relations: list[dict] | None = None,
) -> list[dict]:
    """Enhance spatial constraints with trajectory-implied adjacency and transitive direction inference.

    Phase A (no LLM, < 1 second):
    1. Trajectory travel_sequence: consecutive chapter character movement → adjacent constraint
    2. Transitive direction inference: A north B, B north C → A north C (chain ≤ 2)
    3. Inject completed_spatial_relations from WorldStructure (Phase B results)

    Priority: existing (chapter-extracted) > completed (Phase B) > inferred (Phase A)
    """
    existing_keys: set[tuple[str, str, str]] = {
        (c["source"], c["target"], c["relation_type"]) for c in spatial_constraints
    }
    # Also track (source, target) pairs for any relation type
    existing_pairs: set[tuple[str, str]] = {
        (c["source"], c["target"]) for c in spatial_constraints
    }
    existing_pairs |= {(c["target"], c["source"]) for c in spatial_constraints}

    loc_tier: dict[str, str] = {loc["name"]: loc.get("tier", "city") for loc in locations}
    loc_parent: dict[str, str | None] = {loc["name"]: loc.get("parent") for loc in locations}
    loc_set: set[str] = {loc["name"] for loc in locations}

    added_travel = 0
    added_transitive = 0
    added_completed = 0

    # ── 1. Inject completed_spatial_relations (Phase B, higher priority than Phase A inferred) ──
    if completed_relations:
        for rel in completed_relations:
            key = (rel["source"], rel["target"], rel["relation_type"])
            if key not in existing_keys:
                spatial_constraints.append(rel)
                existing_keys.add(key)
                existing_pairs.add((rel["source"], rel["target"]))
                existing_pairs.add((rel["target"], rel["source"]))
                added_completed += 1

    # ── 2. Trajectory travel_sequence constraints ──
    def _has_common_ancestor(a: str, b: str, depth: int = 3) -> bool:
        """Check if a and b share a common ancestor within `depth` levels."""
        ancestors_a: set[str] = set()
        cur = a
        for _ in range(depth):
            p = loc_parent.get(cur)
            if not p:
                break
            ancestors_a.add(p)
            cur = p
        cur = b
        for _ in range(depth):
            p = loc_parent.get(cur)
            if not p:
                break
            if p in ancestors_a:
                return True
            cur = p
        # Both rootless → trivially share "no parent" ancestor
        return bool(not loc_parent.get(a) and not loc_parent.get(b))

    for _person, path in trajectories.items():
        if len(path) < 2:
            continue
        for i in range(len(path) - 1):
            loc_a = path[i]["location"]
            loc_b = path[i + 1]["location"]
            if loc_a == loc_b:
                continue
            if loc_a not in loc_set or loc_b not in loc_set:
                continue
            # Non-consecutive chapters → skip (teleport, not travel)
            ch_gap = abs(path[i + 1]["chapter"] - path[i]["chapter"])
            if ch_gap > 1:
                continue
            # Tier gap check: only same tier or adjacent tiers
            tier_a = _TIER_RANK.get(loc_tier.get(loc_a, "city"), 4)
            tier_b = _TIER_RANK.get(loc_tier.get(loc_b, "city"), 4)
            if abs(tier_a - tier_b) > 1:
                continue
            # Common ancestor check
            if not _has_common_ancestor(loc_a, loc_b):
                continue
            # Don't overwrite existing constraints
            key = (loc_a, loc_b, "travel_sequence")
            rev_key = (loc_b, loc_a, "travel_sequence")
            if key in existing_keys or rev_key in existing_keys:
                continue
            spatial_constraints.append({
                "source": loc_a,
                "target": loc_b,
                "relation_type": "travel_sequence",
                "value": "travel_implied",
                "confidence": "medium",
                "confidence_score": 0.6,
                "source_type": "trajectory",
            })
            existing_keys.add(key)
            existing_pairs.add((loc_a, loc_b))
            existing_pairs.add((loc_b, loc_a))
            added_travel += 1

    # ── 3. Transitive direction inference (chain ≤ 2, cardinal only) ──
    # Build directed graph: (source, direction) → [targets]
    dir_graph: dict[str, dict[str, list[str]]] = {}  # source → {direction → [targets]}
    dir_confidence: dict[tuple[str, str, str], str] = {}  # (src, tgt, dir) → confidence

    for c in spatial_constraints:
        if c["relation_type"] == "direction" and c.get("value") in _CARDINAL_DIRECTIONS:
            src, tgt, direction = c["source"], c["target"], c["value"]
            dir_graph.setdefault(src, {}).setdefault(direction, []).append(tgt)
            dir_confidence[(src, tgt, direction)] = c.get("confidence", "medium")

    # One-hop transitive: A →dir→ B →dir→ C ⇒ A →dir→ C
    for a, dir_targets in dir_graph.items():
        for direction, b_list in dir_targets.items():
            for b in b_list:
                b_targets = dir_graph.get(b, {}).get(direction, [])
                for c in b_targets:
                    if c == a:
                        continue
                    key = (a, c, "direction")
                    if key in existing_keys:
                        continue
                    # Check for contradiction: if C→A exists in opposite direction, skip
                    opposite = _DIRECTION_OPPOSITES.get(direction)
                    rev_key = (c, a, "direction")
                    is_contradiction = False
                    if rev_key in existing_keys:
                        for ec in spatial_constraints:
                            if ec["source"] == c and ec["target"] == a and ec["relation_type"] == "direction":
                                if ec.get("value") == opposite:
                                    is_contradiction = True
                                    break
                    if is_contradiction:
                        continue
                    # Confidence decay: high→medium, medium→low, low→skip
                    src_conf_ab = dir_confidence.get((a, b, direction), "medium")
                    src_conf_bc = dir_confidence.get((b, c, direction), "medium")
                    min_conf = min(
                        _CONFIDENCE_RANK.get(src_conf_ab, 1),
                        _CONFIDENCE_RANK.get(src_conf_bc, 1),
                    )
                    if min_conf <= 1:
                        continue  # source too weak to infer
                    new_conf = "medium" if min_conf >= 3 else "low"

                    spatial_constraints.append({
                        "source": a,
                        "target": c,
                        "relation_type": "direction",
                        "value": direction,
                        "confidence": new_conf,
                        "confidence_score": 0.6 if new_conf == "medium" else 0.3,
                        "source_type": "transitive",
                    })
                    existing_keys.add(key)
                    added_transitive += 1

    if added_travel or added_transitive or added_completed:
        logger.info(
            "Constraint enhancement: +%d travel_sequence, +%d transitive, +%d completed",
            added_travel, added_transitive, added_completed,
        )
    return spatial_constraints


_map_cache: dict[str, tuple[float, dict]] = {}  # key → (timestamp, data)
_MAP_CACHE_TTL = 1800  # 30 minutes

# 进程级一次性 geo 重试：旧版本把「海底/龙宫」层误判成幻想世界，跳过了地理
# 布局并留下 geo_failed=1。判定修复后，缓存里仍是 layered，而 geo_failed 会
# 永久阻止 stale-cache 自失效 —— 这里在判定不再偏好幻想层时放行一次重试，
# 成功则清掉标记，失败则标记重新置位、本进程不再重试（下次重启再试一次）。
_GEO_RETRY_DONE: set[str] = set()


def canonicalize_map_names(
    loc_info: dict,
    loc_chapters: dict,
    loc_role: dict,
    trajectories: dict,
    constraint_map: dict,
    alias_map: dict,
) -> dict:
    """map 展示层地名 canonical 化(map.alias_canonical;只改展示,不改 facts/ws)。

    facts 原始名(水浒 京师/红楼 神京)归一到层级权威 canonical 名:
    地点条目合并、章节/角色/轨迹/空间约束引用同步映射,别名自指约束
    (映射后 s==t)剔除。保序遍历保确定性。返回归并报告。
    """
    report: dict = {"renamed": [], "merged_locations": [],
                    "self_loop_constraints": []}
    if not alias_map:
        return report

    role_priority = {"setting": 3, "boundary": 2, "referenced": 1}
    for alias in sorted(alias_map):
        canon = alias_map[alias]
        if alias in loc_info:
            if canon not in loc_info:
                loc_info[canon] = loc_info[alias]
            else:
                if not loc_info[canon].get("parent") and loc_info[alias].get("parent"):
                    loc_info[canon]["parent"] = loc_info[alias]["parent"]
                report["merged_locations"].append(alias)
            del loc_info[alias]
            report["renamed"].append((alias, canon))
        if alias in loc_chapters:
            loc_chapters.setdefault(canon, set()).update(
                loc_chapters.pop(alias))
        if alias in loc_role:
            new_role = loc_role.pop(alias)
            cur = loc_role.get(canon)
            if role_priority.get(new_role or "", 0) > role_priority.get(cur or "", 0):
                loc_role[canon] = new_role
        for entries in trajectories.values():
            for entry in entries:
                if entry.get("location") == alias:
                    entry["location"] = canon

    # 空间约束:端点/途经点统一映射;s==t 的自指约束剔除;键冲突取高置信
    remapped: dict = {}
    for (src, tgt, rel_type), c in constraint_map.items():
        ns, nt = alias_map.get(src, src), alias_map.get(tgt, tgt)
        if ns == nt:
            report["self_loop_constraints"].append((src, tgt, rel_type))
            continue
        waypoints = c.get("waypoints")
        c = {**c, "source": ns, "target": nt}
        if waypoints:
            c["waypoints"] = [alias_map.get(w, w) for w in waypoints]
        key = (ns, nt, rel_type)
        existing = remapped.get(key)
        if existing is None or _CONFIDENCE_RANK.get(
                c.get("confidence"), 1) > _CONFIDENCE_RANK.get(
                existing.get("confidence"), 1):
            remapped[key] = c
    constraint_map.clear()
    constraint_map.update(remapped)
    return report


def canonicalize_constraint_list(
    spatial_constraints: list[dict],
    alias_map: dict[str, str],
) -> list[dict]:
    """约束清单的 canonical 过一遍(enhance/注入之后):端点与途经点过
    别名表、剔除映射后自指(s==t)、按键去重(先见优先,同键高置信优先)。
    保序。alias_map 为空时原样返回。
    """
    if not alias_map:
        return spatial_constraints
    out: list[dict] = []
    best: dict[tuple[str, str, str], int] = {}
    for c in spatial_constraints:
        ns = alias_map.get(c.get("source"), c.get("source"))
        nt = alias_map.get(c.get("target"), c.get("target"))
        if ns == nt:
            continue
        nc = dict(c, source=ns, target=nt)
        if nc.get("waypoints"):
            nc["waypoints"] = [alias_map.get(w, w) for w in nc["waypoints"]]
        key = (ns, nt, nc.get("relation_type"))
        rank = _CONFIDENCE_RANK.get(nc.get("confidence"), 1)
        idx = best.get(key)
        if idx is None:
            best[key] = len(out)
            out.append(nc)
        elif rank > _CONFIDENCE_RANK.get(out[idx].get("confidence"), 1):
            out[idx] = nc
    return out


async def invalidate_map_response_cache(novel_id: str) -> None:
    """丢弃某小说的内存地图响应缓存 + 持久化地理 artifacts(实体 override 写入后调用)。

    DB 层布局缓存(map_layouts/layer_layouts)不动 — 隐藏/改型在响应边界
    过滤,布局坐标本身与可见性无关,可复用。landmass/rivers/roads 由
    地点集合塑形,地点增删必须重算,故持久化 artifacts 一并删除。
    """
    prefix = f"{novel_id}:"
    for key in [k for k in _map_cache if k.startswith(prefix)]:
        _map_cache.pop(key, None)
    _GEO_RETRY_DONE.discard(novel_id)
    await world_structure_store.delete_geo_artifacts(novel_id)


# Layer types that are SEPARATE WORLDS rather than places on Earth. `pocket` is
# deliberately excluded: pockets are interiors (洞府/府邸) that sit inside the real
# world, and every instance_* layer is one.
_REALM_LAYER_TYPES = frozenset({"sky", "underground", "underwater", "sea", "spirit"})

# 海底/海域层在现实题材里是同一颗地球的海面与海底，不是独立幻想空间。
# 《海底两万里》的「海底/龙宫」层装有 11 个地点（11/411 = 2.7%），旧判定
# 把整本真实地理小说踢出 Leaflet 地图，改画分层幻想图：南极出现在画面顶部、
# 北冰洋落在亚洲下方、太平洋与大西洋挤在欧洲旁边。见
# `_is_earthbound_sea_setting()` / `_prefers_fantasy_layers()`。
_SEA_REALM_LAYER_TYPES = frozenset({"underwater", "sea"})
_EARTHBOUND_GENRES = frozenset({"realistic", "adventure", "urban"})


def _is_earthbound_sea_setting(ws) -> bool:
    """True when an underwater/sea layer is just this Earth's ocean floor.

    Realistic/adventure novels (20,000 Leagues, sea stories) put the ship and
    its dive sites in an `underwater` layer, but they still happen on Earth and
    every port/ocean resolves to real coordinates. Treating that layer as a
    separate realm throws the whole book off the basemap.
    """
    if ws is None:
        return False
    genre = (ws.novel_genre_hint or "").strip().lower()
    geo_type = (ws.geo_type or "").strip().lower()
    return geo_type == "realistic" or genre in _EARTHBOUND_GENRES


def _fantasy_forcing_layer_types(ws) -> frozenset[str]:
    """Realm layer types that require a layered (non-geographic) map."""
    if _is_earthbound_sea_setting(ws):
        return _REALM_LAYER_TYPES - _SEA_REALM_LAYER_TYPES
    return _REALM_LAYER_TYPES


def _realm_layer_location_count(
    ws, realm_types: frozenset[str] | None = None,
) -> tuple[int, int]:
    """(locations mapped into a non-earth realm layer, total mapped locations)."""
    types = _REALM_LAYER_TYPES if realm_types is None else realm_types
    realm_ids = {
        lyr.layer_id
        for lyr in (ws.layers or [])
        if str(getattr(lyr.layer_type, "value", lyr.layer_type)) in types
    }
    mapped = ws.location_layer_map or {}
    total = len(mapped)
    return sum(1 for lid in mapped.values() if lid in realm_ids), total


def _has_sea_realm_layer(ws) -> bool:
    """True when the world declares an underwater/sea layer (regardless of count)."""
    if ws is None:
        return False
    return any(
        str(getattr(lyr.layer_type, "value", lyr.layer_type))
        in _SEA_REALM_LAYER_TYPES
        for lyr in (ws.layers or [])
    )


def _prefers_fantasy_layers(ws) -> bool:
    """True when a world should be drawn as a LAYERED fantasy map, not on a
    real-world basemap.

    A real basemap has nowhere to put 天界/冥界, so those locations simply vanish
    from the map. 封神演义 is the case that forced this: 64 % of its names are real
    ancient toponyms (朝歌/西岐/昆仑/冀州), so the geo detector reads it as
    realistic/mixed — yet its celestial + underwater layers hold 10 mapped
    locations, and those were invisible on the Leaflet map.

    Keyed on the MAPPED LOCATION COUNT, not on a layer merely being declared:
    水浒传 declares a 天界 (sky) layer holding 0 locations, and 红楼梦 maps 2
    (0.3 %). Thresholds are >= 5 locations AND >= 1 % of the mapped ones, which
    封神 clears (10, 2.1 %) and 红楼 does not (2, 0.3 %) — so only 封神 flips.

    Exception: in a realistic/adventure setting an `underwater`/`sea` layer is
    the same Earth's ocean floor, not a separate realm. It must not force the
    whole overworld off the basemap (《海底两万里》的误判).
    """
    if ws is None:
        return False
    n, total = _realm_layer_location_count(ws, _fantasy_forcing_layer_types(ws))
    return n >= 5 and n / max(total, 1) >= 0.01


# 地理底图上没有坐标的对象：船只、船内舱室/构件、抽象世界节点、经纬线标记。
# 它们保留在响应的 `locations` 里（实体卡片/分层图仍要用），但不进入 GeoNames
# 解析与 Leaflet 标记 —— 否则《海底两万里》的 30+ 个舱室会被估算到船所在的
# 同一个点上，「诺第留斯号」会被钉在南冰洋，而「世界」「地球」「赤道线」
# 会变成地图上的一个坐标点。
_GEO_NON_PLACE_TYPES = frozenset({
    # 船是移动载体，不是固定坐标；把「诺第留斯号」钉在南冰洋会制造假地点。
    "船只", "潜水船", "舰船", "船",
    "甲板", "船舱", "舱房", "船构件", "灯具", "通道",
    "界域", "地理标记", "经线",
})
_GEO_NON_PLACE_NAMES = frozenset({"世界", "地球", "主世界", "天下"})


def _is_geographic_map_location(loc: dict) -> bool:
    """Whether a location can be drawn as a point on a real-world basemap."""
    name = (loc.get("name") or "").strip()
    if not name or name in _GEO_NON_PLACE_NAMES:
        return False
    return (loc.get("type") or "").strip() not in _GEO_NON_PLACE_TYPES


def _allowed_map_layers(ws, target_layer: str) -> set[str]:
    """Layer ids rendered on the target layer's canvas.

    The target layer plus merged layers (<=1 location, which have no tab of
    their own). Shared by the geographic resolver and the layout fill below so
    the two membership rules cannot drift apart.
    """
    allowed = {target_layer}
    if ws is None:
        return allowed
    counts = Counter((ws.location_layer_map or {}).values())
    for layer in ws.layers:
        if layer.layer_id == "overworld":
            continue
        if counts.get(layer.layer_id, 0) <= 1:
            allowed.add(layer.layer_id)
    return allowed


async def get_map_data(
    novel_id: str, chapter_start: int, chapter_end: int,
    layer_id: str | None = None,
) -> dict:
    import time as _time
    cache_key = f"{novel_id}:{chapter_start}:{chapter_end}:{layer_id or ''}"
    if cache_key in _map_cache:
        ts, cached = _map_cache[cache_key]
        if _time.time() - ts < _MAP_CACHE_TTL:
            return cached

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
                    "distance_class": sr.distance_class,
                    "confidence_score": sr.confidence_score,
                    "waypoints": sr.waypoints,
                }

    # 实体级可见性 override(issue #66 Epic 1, FR-1.1/FR-1.2):隐藏的、或
    # 改型到非 location 的地点从地图剔除。地图用的是原始名,override key 是
    # canonical,经 alias_map 双向展开。无 override 时零行为变化。
    removed_locations: set[str] = set()
    from src.services.entity_visibility import (
        expand_hidden,
        expand_retype,
        get_visibility_overrides,
    )

    hidden_locs, retype_locs = await get_visibility_overrides(novel_id)
    if hidden_locs or retype_locs:
        alias_map_vis = await build_alias_map(novel_id)
        hidden_r = expand_hidden(alias_map_vis, hidden_locs)
        retype_r = expand_retype(alias_map_vis, retype_locs)
        for name in list(loc_info):
            canon = alias_map_vis.get(name, name)
            if canon in hidden_r or name in hidden_r:
                removed_locations.add(name)
            else:
                target = retype_r.get(canon, retype_r.get(name))
                if target and target != "location":
                    removed_locations.add(name)
        for name in removed_locations:
            loc_info.pop(name, None)
            loc_chapters.pop(name, None)
            loc_role.pop(name, None)
        if removed_locations:
            trajectories = defaultdict(list, {
                p: [e for e in entries if e["location"] not in removed_locations]
                for p, entries in trajectories.items()
            })
            # 引用被剔除地点的空间约束一并丢弃(否则也会被下游 dangling 检查删掉)
            for key in [
                k for k in constraint_map
                if k[0] in removed_locations or k[1] in removed_locations
            ]:
                del constraint_map[key]

    # map 展示层地名 canonical 化(map.alias_canonical 默认开,表空零行为):
    # facts 原始名(水浒 京师/北京大名府、红楼 神京)归一到层级权威
    # canonical 名,使地图与 world-structure 层级树展示一致;
    # 轨迹/空间约束引用同步映射,不产生 dangling。
    _map_alias_map: dict[str, str] = {}
    from src.services.geo_skills.evolve_params import evolve_param
    if evolve_param("map.alias_canonical", True):
        from src.db.novel_store import get_novel as _get_novel_meta
        from src.utils.location_names import location_alias_map_for_title
        _meta = await _get_novel_meta(novel_id)
        _map_alias_map = location_alias_map_for_title(
            (_meta or {}).get("title") or "")
        if _map_alias_map:
            _alias_report = canonicalize_map_names(
                loc_info, loc_chapters, loc_role, trajectories,
                constraint_map, _map_alias_map)
            if _alias_report["renamed"]:
                logger.info(
                    "Map alias canonical: %d renamed, %d merged, %d self-loop dropped",
                    len(_alias_report["renamed"]),
                    len(_alias_report["merged_locations"]),
                    len(_alias_report["self_loop_constraints"]),
                )

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
    locations.sort(key=lambda loc: (-loc["mention_count"], loc["name"]))

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

    # Inject travel_path waypoints into trajectories.
    # If character moves A→C and a travel_path exists A→C with waypoints=[B],
    # insert B between A and C so the animation shows the full route.
    travel_paths: dict[tuple[str, str], list[str]] = {}
    for c in constraint_map.values():
        if c["relation_type"] == "travel_path" and c.get("waypoints"):
            travel_paths[(c["source"], c["target"])] = c["waypoints"]
            # Also store reverse direction with reversed waypoints
            travel_paths[(c["target"], c["source"])] = list(reversed(c["waypoints"]))

    if travel_paths:
        for person in list(trajectories.keys()):
            entries = trajectories[person]
            if len(entries) < 2:
                continue
            enriched: list[dict] = [entries[0]]
            for i in range(1, len(entries)):
                prev_loc = entries[i - 1]["location"]
                curr_loc = entries[i]["location"]
                wps = travel_paths.get((prev_loc, curr_loc))
                if wps:
                    # Interpolate chapter numbers for waypoints
                    ch_prev = entries[i - 1]["chapter"]
                    ch_curr = entries[i]["chapter"]
                    for wi, wp in enumerate(wps):
                        # Only insert if waypoint is a known location
                        if wp in loc_info:
                            frac = (wi + 1) / (len(wps) + 1)
                            wp_ch = int(ch_prev + frac * (ch_curr - ch_prev))
                            enriched.append({"location": wp, "chapter": wp_ch, "waypoint": True})
                enriched.append(entries[i])
            trajectories[person] = enriched

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

    # Soft direction priors from cardinal names (东胜神洲→east, 南海→south)
    spatial_constraints = _inject_cardinal_name_priors(spatial_constraints, locations)

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
            # Normalize variant location names in WorldStructure maps so they
            # match the canonical forms used in region definitions (e.g.,
            # 南瞻部洲 → 南赡部洲 matches the overworld region).
            if _LOCATION_NAME_NORMALIZE:
                for variant, canonical in _LOCATION_NAME_NORMALIZE.items():
                    # Fix location_region_map: variant key → remap to canonical region
                    if variant in ws.location_region_map:
                        # If canonical region exists, point variant there
                        overworld_region_names = set()
                        for layer in ws.layers:
                            if layer.layer_id == "overworld":
                                overworld_region_names = {r.name for r in layer.regions}
                                break
                        if canonical in overworld_region_names:
                            ws.location_region_map[variant] = canonical

            # Update location tier/icon from WorldStructure (with heuristic fallback)
            _tier_map = ws.location_tiers if ws else {}
            _icon_map = ws.location_icons if ws else {}
            # _classify_tier is an instance method — create a temporary agent to
            # use it for locations not already in the tier map.
            _agent: WorldStructureAgent | None = None
            for loc in locations:
                name = loc["name"]
                loc_type = loc.get("type", "")
                parent = loc.get("parent")
                level = loc.get("level", 0)
                tier = _tier_map.get(name, "")
                if not tier:
                    if _agent is None:
                        _agent = WorldStructureAgent.__new__(WorldStructureAgent)
                        _agent.structure = ws
                    tier = _agent._classify_tier(name, loc_type, parent, level)
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
                    for _loc_name_r, region_name_r in ws.location_region_map.items():
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

    # ── Canvas the layout + terrain bake below must agree on ──
    # Every layer is solved in SPATIAL_SCALE_CANVAS[spatial_scale]
    # (compute_layered_layout), and the terrain raster has to be baked in that
    # same space or its influence points land off the edge of the image. This
    # was assigned only *inside* the `active_regions` branch above, so a world
    # structure with no regions left it unbound and the layout call below raised
    # UnboundLocalError, killing the whole /map request for that novel
    # (regression introduced with the ccee9c3ad terrain-canvas change).
    if ws is not None:
        _ws_cw, _ws_ch = SPATIAL_SCALE_CANVAS.get(
            ws.spatial_scale or "", (CANVAS_WIDTH, CANVAS_HEIGHT)
        )
    else:
        _ws_cw, _ws_ch = CANVAS_WIDTH, CANVAS_HEIGHT

    # ── Phase A: Constraint enhancement (no LLM, real-time) ──
    _completed_rels = ws.completed_spatial_relations if ws else None
    spatial_constraints = _enhance_constraints(
        spatial_constraints, dict(trajectories), locations,
        completed_relations=_completed_rels,
    )
    # completed_spatial_relations 是 ws 存量原始名(如 敕建宝林寺),
    # enhance 后再过一遍 canonical,避免别名端点 dangling
    # (别名节点已被归并/摘除,约束引用必须与展示节点一致)。
    if _map_alias_map:
        spatial_constraints = canonicalize_constraint_list(
            spatial_constraints, _map_alias_map)

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
    # Invalidate stale cache: if world_structure says geographic but cache says otherwise.
    # Also handle historical/wuxia genre override: geo_type may be cached as "fantasy"
    # but the genre override will upgrade it to "mixed".
    _effective_geo_type = ws.geo_type if ws else None
    if (
        _effective_geo_type
        and _effective_geo_type not in ("realistic", "mixed")
        and ws
        and ws.novel_genre_hint
        and ws.novel_genre_hint.lower() in ("historical", "wuxia")
    ):
        _effective_geo_type = "mixed"
    if (
        cached_layer is not None
        and target_layer == "overworld"
        and ws and _effective_geo_type in ("realistic", "mixed")
        and cached_layer["layout_mode"] != "geographic"
    ):
        _geo_failed = await world_structure_store.get_geo_failed(novel_id)
        if not _geo_failed:
            _GEO_RETRY_DONE.discard(novel_id)
            logger.info("Invalidating stale overworld cache (geo_type=%s/%s but cached as %s)",
                        ws.geo_type, _effective_geo_type, cached_layer["layout_mode"])
            cached_layer = None
        elif (
            novel_id not in _GEO_RETRY_DONE
            and not _prefers_fantasy_layers(ws)
            and _has_sea_realm_layer(ws)
        ):
            # 旧版 _prefers_fantasy_layers 把现实题材的「海底/龙宫」层当成幻想
            # 世界，geo 从未真正尝试过，却留下了 geo_failed 标记。判定修复后
            # 放行一次地理布局重试；若真的失败，标记会重新置位，本进程不再重试。
            logger.info(
                "Retrying geographic layout for %s: sea realm layer no longer "
                "forces fantasy mode", novel_id,
            )
            _GEO_RETRY_DONE.add(novel_id)
            cached_layer = None

    # The mirror case: the cache IS geographic but the world has real realm
    # layers, so it has to become layered. Without this the cached geographic
    # layout would be served forever and the realms would stay invisible — the
    # cache short-circuits the choice that the geo block below would make.
    if (
        cached_layer is not None
        and target_layer == "overworld"
        and cached_layer["layout_mode"] == "geographic"
        and _prefers_fantasy_layers(ws)
    ):
        logger.info(
            "Invalidating geographic overworld cache for %s: world has "
            "substantial non-earth realm layers, which a real-world basemap "
            "cannot show", novel_id,
        )
        cached_layer = None

    # Compute canvas_size for API response (per-layer scale if available).
    # Resolved here rather than further down only because the terrain rebake
    # below needs the layout's own space: `ws` is not mutated on this path
    # (`geo_type` aside), so hoisting this changes nothing downstream.
    _ws_scale = ws.spatial_scale if ws else None
    if ws and layer_id and layer_id != "overworld" and ws.layer_spatial_scales.get(layer_id):
        _effective_scale = ws.layer_spatial_scales[layer_id]
    else:
        _effective_scale = _ws_scale
    _resp_cw, _resp_ch = SPATIAL_SCALE_CANVAS.get(
        _effective_scale or "", (CANVAS_WIDTH, CANVAS_HEIGHT)
    ) if ws else (CANVAS_WIDTH, CANVAS_HEIGHT)

    satisfaction: dict | None = None  # populated only by constraint solver path
    if cached_layer is not None:
        layout_data = cached_layer["layout"]
        layout_mode = cached_layer["layout_mode"]
        # A baked PNG exists on disk for non-geographic modes. The path is the
        # versioned one from `terrain_path_for` — the old unversioned check
        # looked for `terrain.png`, which after a recipe bump is the artifact the
        # previous recipe produced, so the map advertised a terrain URL that the
        # route would have 404'd on.
        terrain_png = terrain_path_for(novel_id)
        if layout_mode != "geographic" and not terrain_png.is_file():
            # The layout cache outlives the terrain it produced. A terrain
            # recipe bump orphans the PNG under the old name while the cached
            # layout is still perfectly good, so the honest thing is to rebake
            # the ground from the cached layout instead of making the user pay
            # for a full re-solve of something they already have. Terrain is a
            # pure function of (locations, layout), both of which are in hand.
            logger.info(
                "Rebaking terrain for %s: cached layout present, no "
                "terrain.v%s on disk", novel_id, _TERRAIN_VERSION,
            )
            try:
                cached_coords = {
                    item["name"]: (item["x"], item["y"])
                    for item in layout_data
                    if isinstance(item, dict) and "name" in item
                }
                if len(cached_coords) >= 3:
                    await asyncio.to_thread(
                        generate_terrain, locations, cached_coords, novel_id,
                        canvas_width=_resp_cw, canvas_height=_resp_ch,
                    )
            except Exception:
                logger.warning(
                    "Terrain rebake from cached layout failed for %s",
                    novel_id, exc_info=True,
                )
        terrain_url = terrain_url_for(novel_id) if (
            layout_mode != "geographic" and terrain_png.is_file()
        ) else None
        # Restore geo_coords for cached geographic layouts (coords are not in cache)
        if layout_mode == "geographic" and target_layer == "overworld" and ws:
            # Fast path: persisted geo_coords artifact skips the full
            # geo_auto_resolve (geonames index rebuild + whole-book resolution)
            try:
                _coords_art = await world_structure_store.load_geo_artifacts(
                    novel_id, target_layer, ch_hash,
                )
            except Exception:
                logger.warning("Failed to load geo coords artifact", exc_info=True)
                _coords_art = None
            if _coords_art is not None and _coords_art.get("geo_coords") is not None:
                geo_coords_raw = _coords_art["geo_coords"]
            else:
                try:
                    all_names = [loc["name"] for loc in locations]
                    loc_parent_map = {
                        loc["name"]: loc.get("parent")
                        for loc in locations
                    }
                    loc_type_map = {
                        loc["name"]: loc.get("type")
                        for loc in locations
                    }
                    _scope, _gtype, _resolver, resolved = await geo_auto_resolve(
                        ws.novel_genre_hint, all_names, all_names, loc_parent_map,
                        known_geo_type=ws.geo_type, type_map=loc_type_map,
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
                # Backfill the artifact so the next cold process hits the fast path
                if geo_coords_raw:
                    try:
                        await world_structure_store.save_geo_coords(
                            novel_id, target_layer, ch_hash,
                            json.dumps(geo_coords_raw, ensure_ascii=False),
                        )
                    except Exception:
                        logger.warning("Failed to persist geo coords artifact", exc_info=True)
    else:
        # ── Geographic layout: real-world coordinates via GeoNames ──
        # Only attempt for overworld layer (sub-layers are fictional internal spaces)
        geo_resolved = False
        # A world with substantial non-earth realms is drawn layered instead: its
        # 天界/冥界 locations have no coordinate on a real basemap and would simply
        # be dropped. Skipping geo here leaves geo_resolved False, and the block
        # below then builds the layered layout.
        if ws and target_layer == "overworld" and not _prefers_fantasy_layers(ws):
            try:
                # 只把真正能落在世界地图上的地点交给 GeoNames：舱室/构件/
                # 界域/经纬线没有坐标，混进来会被估到船或父节点的同一个点；
                # 其它层（海底/龙宫等）的地点也不属于主世界底图（≤1 个地点的
                # 合并层仍显示在主世界，与分层布局的规则保持一致）。
                _allowed_geo_layers = _allowed_map_layers(ws, target_layer)
                geo_locations = [
                    loc for loc in locations
                    if _is_geographic_map_location(loc)
                    and (ws.location_layer_map.get(loc["name"], "overworld")
                         in _allowed_geo_layers)
                ]
                all_names = [loc["name"] for loc in geo_locations]
                major_names = [
                    loc["name"] for loc in geo_locations
                    if loc.get("level", 0) <= 3
                ]
                loc_parent_map = {
                    loc["name"]: loc.get("parent")
                    for loc in locations
                }
                loc_type_map = {
                    loc["name"]: loc.get("type")
                    for loc in geo_locations
                }
                if ws.geo_type:
                    # Use cached geo_type — skip re-detection to avoid
                    # oscillation when chapter range changes the location subset.
                    # Pass through auto_resolve even for non-realistic types,
                    # because auto_resolve applies genre-based overrides
                    # (e.g., historical novels with cached "fantasy" → "mixed").
                    _geo_scope, geo_type, resolver, resolved = await geo_auto_resolve(
                        ws.novel_genre_hint, all_names, major_names, loc_parent_map,
                        known_geo_type=ws.geo_type, type_map=loc_type_map,
                    )
                    # Persist the (possibly overridden) geo_type back to cache
                    if geo_type != ws.geo_type:
                        ws.geo_type = geo_type
                        await world_structure_store.save(novel_id, ws)
                else:
                    # First-time detection — run full detection and persist
                    _geo_scope, geo_type, resolver, resolved = await geo_auto_resolve(
                        ws.novel_genre_hint, all_names, major_names, loc_parent_map,
                        type_map=loc_type_map,
                    )
                    ws.geo_type = geo_type
                    await world_structure_store.save(novel_id, ws)

                if resolver and resolved and geo_type in ("realistic", "mixed"):
                    # Guard: for "mixed" novels, if too few locations actually
                    # resolve to real coordinates, the geographic layout is
                    # misleading (fictional locations cluster near the few real
                    # ones). Fall back to hierarchy layout instead.
                    # Historical/wuxia/adventure novels use a lower threshold
                    # because they have many micro-locations that dilute the ratio.
                    # Default 0.15 is lenient: geo_type=mixed already confirms
                    # real-world signal from detect_geo_type().
                    if geo_type == "mixed":
                        resolve_ratio = len(resolved) / max(len(all_names), 1)
                        genre = (ws.novel_genre_hint or "").lower() if ws else ""
                        min_ratio = 0.10 if genre in ("historical", "wuxia", "realistic", "adventure") else 0.15
                        if resolve_ratio < min_ratio:
                            logger.info(
                                "geo_type=mixed but only %d/%d (%.0f%%) locations "
                                "resolved (min %.0f%%) — falling back to hierarchy layout",
                                len(resolved), len(all_names), resolve_ratio * 100,
                                min_ratio * 100,
                            )
                            resolver, resolved = None, None

                if resolver and resolved and geo_type in ("realistic", "mixed"):
                    # Store raw lat/lng for Leaflet frontend
                    geo_coords_raw = {
                        name: {"lat": coord[0], "lng": coord[1]}
                        for name, coord in resolved.items()
                    }
                    layout_data = resolver.project_to_canvas(
                        resolved, geo_locations, _cw_hash, _ch_hash,
                    )

                    # Place unresolved names near their resolved neighbors
                    resolved_names = set(resolved.keys())
                    unresolved_names = [
                        loc["name"] for loc in geo_locations
                        if loc["name"] not in resolved_names
                    ]
                    if unresolved_names:
                        parent_map = {
                            loc["name"]: loc.get("parent")
                            for loc in locations
                        }
                        extra = place_unresolved_near_neighbors(
                            unresolved_names, layout_data, geo_locations,
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
                    # Persist resolved geo coords (pre-override final value,
                    # unresolved estimates merged) so cold processes skip
                    # re-running geo_auto_resolve. User lat/lng overrides are
                    # still applied last at response assembly.
                    if geo_coords_raw:
                        try:
                            await world_structure_store.save_geo_coords(
                                novel_id, target_layer, ch_hash,
                                json.dumps(geo_coords_raw, ensure_ascii=False),
                            )
                        except Exception:
                            logger.warning(
                                "Failed to persist geo coords artifact", exc_info=True,
                            )
                    # 地理布局成功：清掉历史失败标记（旧版可能在 geo 从未尝试时
                    # 误置），否则后续 stale-cache 自失效会被永久压制。
                    try:
                        await world_structure_store.clear_geo_failed(novel_id)
                    except Exception:
                        logger.warning("Failed to clear geo_failed marker", exc_info=True)
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
                # Generate terrain for layered mode too
                terrain_url = None
                if layout_data and len(layout_data) >= 3:
                    layer_coords = {
                        item["name"]: (item["x"], item["y"])
                        for item in layout_data
                        if not item.get("is_portal")
                    }
                    if len(layer_coords) >= 3:
                        t_path = await asyncio.to_thread(
                            generate_terrain, locations, layer_coords, novel_id,
                            # The canvas the client lays this image over. Every
                            # layer solves in `SPATIAL_SCALE_CANVAS[spatial_scale]`
                            # (compute_layered_layout), so `_ws_cw/_ws_ch` is the
                            # layout's own space and the influence points land on
                            # the raster instead of off its edge.
                            canvas_width=_ws_cw, canvas_height=_ws_ch,
                        )
                        terrain_url = terrain_url_for(novel_id) if t_path else None
            else:
                # Global solve (backward compatible path)
                layout_data, layout_mode, terrain_url, satisfaction = await _compute_or_load_layout(
                    novel_id, ch_hash, locations, spatial_constraints,
                    first_chapter_map,
                    location_region_bounds=location_region_bounds,
                    canvas_width=_ws_cw, canvas_height=_ws_ch,
                )

        # Geo was attempted (effective type realistic/mixed) but the final
        # overworld layout came out non-geographic — both fall-through paths
        # (exception fallback / insufficient resolution) land here. Persist a
        # one-shot failure marker so the stale-cache check above stops
        # invalidating the layer cache on every cold process. Not set when
        # geo was never attempted (ws/geo_type missing).
        if (
            not geo_resolved
            and target_layer == "overworld"
            and ws is not None
            and _effective_geo_type in ("realistic", "mixed")
        ):
            try:
                await world_structure_store.set_geo_failed(novel_id)
            except Exception:
                logger.warning("Failed to persist geo_failed marker", exc_info=True)

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

    # ── Detect location/direction/distance conflicts (reuse loaded facts, no extra DB query) ──
    location_conflicts: list[dict] = []
    try:
        parsed_for_conflicts = [
            (f.chapter_id, f.model_dump()) for f in facts
        ]
        alias_map = await build_alias_map(novel_id)
        raw_conflicts = _detect_location_conflicts(parsed_for_conflicts)
        raw_conflicts.extend(_detect_direction_conflicts(parsed_for_conflicts, alias_map))
        raw_conflicts.extend(_detect_distance_conflicts(parsed_for_conflicts, alias_map))
        location_conflicts = [c.to_dict() for c in raw_conflicts]
    except Exception:
        logger.warning("Failed to detect location conflicts for map", exc_info=True)

    # Generate landmass contours FIRST (river generation needs land_mask)
    # Note: generate for overworld layer too (layer_id == "overworld" or None)
    # Skip for underwater layers (no land masses under water)
    _is_overworld_like = not layer_id or layer_id == "overworld"
    _is_underwater = False
    if ws and layer_id:
        for _layer_obj in ws.layers:
            if _layer_obj.layer_id == layer_id and _layer_obj.layer_type == LayerType.underwater:
                _is_underwater = True
                break
    landmass_result: dict = {}
    roads: list[dict] = []
    rivers: list[dict] = []

    # ── Persisted geo artifacts (map_geo_artifacts) ──
    # Stored AFTER the sea-orphan snap (final state) on the cold path, so the
    # read path uses them directly without re-running generation or the snap.
    _geo_artifacts: dict | None = None
    if layout_mode != "geographic" and _is_overworld_like:
        try:
            _geo_artifacts = await world_structure_store.load_geo_artifacts(
                novel_id, target_layer, ch_hash,
            )
        except Exception:
            logger.warning("Failed to load map geo artifacts", exc_info=True)
            _geo_artifacts = None

    if _geo_artifacts is not None:
        landmass_result = {
            "landmasses": _geo_artifacts["landmasses"],
            "shelves": _geo_artifacts["shelves"],
            "shelf_depth": _geo_artifacts.get("shelf_depth", []),
        }
        rivers = _geo_artifacts["rivers"]
        roads = _geo_artifacts["roads"]
        logger.debug("Using persisted map geo artifacts for %s/%s", novel_id, target_layer)
    else:
        if layout_mode != "geographic" and len(layout_data) >= 3 and _is_overworld_like and not _is_underwater:
            _lrm = ws.location_region_map if ws else None
            _layout_mutated = False
            try:
                # Move seas that landed inside land clusters out to open water
                # BEFORE the mask is built — hole carving alone can only give a
                # misplaced sea an invisible puddle (南海, 南洋苦海, …).
                _layout_mutated = bool(
                    _relocate_misplaced_seas(layout_data, locations, _resp_cw, _resp_ch)
                )
                landmass_result = generate_landmasses(
                    locations, layout_data, novel_id,
                    canvas_width=_resp_cw, canvas_height=_resp_ch,
                    location_region_map=_lrm,
                )
                # Two continent anchors on one landmass means the layout fused
                # their subtrees (东胜神洲 vs the mainland). Translate the
                # smaller subtree away and rebuild, bounded at 3 rounds.
                for _sep_round in range(3):
                    _moved = _separate_merged_continents(
                        layout_data, locations, landmass_result, _resp_cw, _resp_ch,
                    )
                    if not _moved:
                        break
                    _layout_mutated = True
                    landmass_result = generate_landmasses(
                        locations, layout_data, novel_id,
                        canvas_width=_resp_cw, canvas_height=_resp_ch,
                        location_region_map=_lrm,
                    )
                    logger.info("Regenerated landmasses after continent separation round %d", _sep_round + 1)
            except Exception:
                logger.warning("Failed to generate landmasses", exc_info=True)
            if _layout_mutated:
                # Persist relocated/translated positions so cached-layout reads
                # stay consistent with the geo artifacts saved below.
                try:
                    if cached_layer is not None or layout_mode == "layered":
                        await _save_cached_layer_layout(
                            novel_id, target_layer, ch_hash, layout_data, layout_mode,
                        )
                    else:
                        conn = await get_connection()
                        try:
                            await conn.execute(
                                "UPDATE map_layouts SET layout_json = ? WHERE novel_id = ? AND chapter_hash = ?",
                                (json.dumps(layout_data, ensure_ascii=False), novel_id, ch_hash),
                            )
                            await conn.commit()
                        finally:
                            await conn.close()
                except Exception:
                    logger.warning("Failed to write back relocated layout", exc_info=True)

        # ── Snap sea-orphan locations to nearest land ──
        # After landmass generation, some unconstrained locations may visually sit
        # in the ocean. Snap non-ocean locations back to the nearest land cell.
        if landmass_result and "_land_mask" in landmass_result:
            _snap_count = _snap_sea_orphans_to_land(
                layout_data, locations, landmass_result, spatial_constraints,
            )
            if _snap_count:
                logger.info("Snapped %d sea-orphan locations to nearest land", _snap_count)
                # Persist snapped positions so cached-layout reads stay consistent
                # with the post-snap geo artifacts saved below
                try:
                    if cached_layer is not None or layout_mode == "layered":
                        await _save_cached_layer_layout(
                            novel_id, target_layer, ch_hash, layout_data, layout_mode,
                        )
                    else:
                        conn = await get_connection()
                        try:
                            await conn.execute(
                                "UPDATE map_layouts SET layout_json = ? WHERE novel_id = ? AND chapter_hash = ?",
                                (json.dumps(layout_data, ensure_ascii=False), novel_id, ch_hash),
                            )
                            await conn.commit()
                        finally:
                            await conn.close()
                except Exception:
                    logger.warning("Failed to write back snapped layout", exc_info=True)
                # Regenerate landmass only if significant snaps occurred (>= 3)
                # to cover new positions. Minor snaps are already near land.
                if _snap_count >= 3:
                    try:
                        landmass_result = generate_landmasses(
                            locations, layout_data, novel_id,
                            canvas_width=_resp_cw, canvas_height=_resp_ch,
                            location_region_map=_lrm,
                        )
                        logger.debug("Regenerated landmasses after sea-orphan snap")
                    except Exception:
                        logger.warning("Failed to regenerate landmasses after snap", exc_info=True)

        # Generate river network AFTER landmasses (clip rivers to land)
        if layout_mode != "geographic" and len(layout_data) >= 3 and _is_overworld_like:
            # Pass land_mask so rivers terminate at coastline
            _land_mask_info = None
            if "_land_mask" in landmass_result:
                _land_mask_info = {
                    "_land_mask": landmass_result["_land_mask"],
                    "_cell_size": landmass_result["_cell_size"],
                }
            rivers = generate_rivers(
                locations, layout_data, novel_id,
                canvas_width=_resp_cw, canvas_height=_resp_ch,
                land_mask_info=_land_mask_info,
            )
            roads = generate_roads(locations, layout_data, land_mask_info=_land_mask_info)

            # Persist final (post-snap) geo artifacts for restart-surviving reuse
            try:
                await world_structure_store.save_geo_artifacts(
                    novel_id, target_layer, ch_hash,
                    json.dumps(landmass_result.get("landmasses", []), ensure_ascii=False),
                    json.dumps(landmass_result.get("shelves", []), ensure_ascii=False),
                    json.dumps(rivers, ensure_ascii=False),
                    json.dumps(roads, ensure_ascii=False),
                    # Keyword args, not positional: `geo_coords_json` was inserted
                    # ahead of `shelf_depth_json` in the store's signature and this
                    # call was not updated, so the depth array was written into the
                    # *geo coords* column while `shelf_depth_json` stayed NULL.
                    # The reader then returned [], and the client painted all five
                    # shelf bands in the shallow colour — a huge pale halo around
                    # every landmass. Silently, because the insert still succeeded.
                    shelf_depth_json=json.dumps(
                        landmass_result.get("shelf_depth", []), ensure_ascii=False
                    ),
                )
            except Exception:
                logger.warning("Failed to persist map geo artifacts", exc_info=True)

    # ── Snap sea bodies that ended up on land back to water ──
    # Mirror of the sea-orphan snap above, for the opposite failure (a sea
    # anchored on land — see _snap_sea_bodies_to_water). Runs on the
    # persisted-artifacts path too: it reads the coastline polygons, not
    # `_land_mask`, so a cached map still gets its drifted seas moved.
    if (
        layout_mode != "geographic"
        and _is_overworld_like
        and landmass_result
        and landmass_result.get("landmasses")
    ):
        try:
            _sea_snap = _snap_sea_bodies_to_water(
                layout_data, locations, landmass_result, _resp_cw, _resp_ch,
            )
            if _sea_snap:
                logger.info("Snapped %d sea bodies from land to water", _sea_snap)
                # Persist so cached-layout reads stay consistent, same as the
                # sea-orphan snap write-back above.
                try:
                    if cached_layer is not None or layout_mode == "layered":
                        await _save_cached_layer_layout(
                            novel_id, target_layer, ch_hash, layout_data, layout_mode,
                        )
                    else:
                        conn = await get_connection()
                        try:
                            await conn.execute(
                                "UPDATE map_layouts SET layout_json = ? WHERE novel_id = ? AND chapter_hash = ?",
                                (json.dumps(layout_data, ensure_ascii=False), novel_id, ch_hash),
                            )
                            await conn.commit()
                        finally:
                            await conn.close()
                except Exception:
                    logger.warning("Failed to write back sea-snapped layout", exc_info=True)
        except Exception:
            logger.warning("Failed to snap sea bodies to water", exc_info=True)

    # ── Fill missing layout coordinates ──
    # Some locations (sub-sites, buildings) may not get layout positions from
    # the layout engine. Assign them coordinates near their parent using
    # sunflower-seed angular distribution for organic spread.
    #
    # The fill must stay inside this response's layer: `layout_data` is the
    # layout of `target_layer`, but `locations` spans EVERY layer, and the
    # client renders whatever it is handed. Without the membership gate the
    # celestial/underworld locations (which have no overworld coordinates by
    # design) were scattered around 天下 and painted onto the main world.
    # Merged layers (≤1 location, non-overworld) are the one exception: they
    # have no tab of their own and are meant to show in the main world — this
    # fill is in fact the mechanism that places them.
    _loc_layer: dict[str, str] = dict(ws.location_layer_map) if ws else {}
    _allowed_layer_ids: set[str] = _allowed_map_layers(ws, target_layer)
    _layout_map = {item["name"]: item for item in layout_data}
    _loc_map = {loc["name"]: loc for loc in locations}
    _missing = [
        loc for loc in locations
        if loc["name"] not in _layout_map
        and _loc_layer.get(loc["name"], "overworld") in _allowed_layer_ids
    ]

    if _missing:
        import math as _math
        _golden_angle = _math.pi * (3 - _math.sqrt(5))  # ~137.5°
        # Group missing locations by their nearest ancestor that has coords
        _parent_children: dict[str, list[dict]] = {}
        for loc in _missing:
            # Walk up parent chain to find an ancestor with layout coords
            ancestor = loc.get("parent")
            visited: set[str] = set()
            while ancestor and ancestor not in _layout_map and ancestor not in visited:
                visited.add(ancestor)
                p = _loc_map.get(ancestor)
                ancestor = p.get("parent") if p else None
            if ancestor and ancestor in _layout_map:
                _parent_children.setdefault(ancestor, []).append(loc)

        for parent_name, children in _parent_children.items():
            p_item = _layout_map[parent_name]
            px, py = p_item["x"], p_item["y"]
            base_r = 60  # base radius for child placement
            for i, child in enumerate(children):
                angle = _golden_angle * i
                r = base_r + 15 * (i // 6)  # expand radius for many children
                cx = px + r * _math.cos(angle)
                cy = py + r * _math.sin(angle)
                new_item = {
                    "name": child["name"],
                    "x": round(cx, 1),
                    "y": round(cy, 1),
                    "level": p_item.get("level", 0) + 1,
                }
                layout_data.append(new_item)
                _layout_map[child["name"]] = new_item

        if _missing:
            logger.debug(
                "Filled %d/%d missing layout positions via parent fallback",
                len(_missing) - len([loc for loc in locations if loc["name"] not in _layout_map]),
                len(_missing),
            )

    # Response-boundary layer filter. The fill above stops injecting foreign
    # locations now, but a layout loaded from a stale cache (solved before
    # layered layout existed, or before a location changed layers) can still
    # carry their coordinates — and the client renders `layout` verbatim.
    # Portal markers stay: they are the cross-layer affordance by design.
    if _loc_layer:
        layout_data = [
            it for it in layout_data
            if it.get("is_portal")
            or _loc_layer.get(it.get("name"), "overworld") in _allowed_layer_ids
        ]
        _layout_map = {item["name"]: item for item in layout_data}

    # Add placement_confidence to each location
    constrained_names: set[str] = set()
    if satisfaction and "constrained_location_names" in satisfaction:
        constrained_names = set(satisfaction["constrained_location_names"])
    for loc in locations:
        loc["placement_confidence"] = "constrained" if loc["name"] in constrained_names else "unconstrained"

    # ── Space theme detection (per-layer) ──────────────
    # Space theme only for cosmic layers, NOT for overworld (earth surface).
    _SPACE_LAYER_IDS = {"galaxy", "solarsystem", "trisolaris", "trisolaris-game"}
    space_theme = bool(layer_id and layer_id in _SPACE_LAYER_IDS)

    # 响应边界兜底过滤:DB/内存缓存的布局可能仍含被隐藏地点的坐标
    if removed_locations:
        layout_data = [
            it for it in layout_data if it.get("name") not in removed_locations
        ]
        for lid, litems in layer_layouts.items():
            layer_layouts[lid] = [
                it for it in litems if it.get("name") not in removed_locations
            ]
        if geo_coords_raw:
            for name in removed_locations:
                geo_coords_raw.pop(name, None)
        revealed_names = [n for n in revealed_names if n not in removed_locations]
        for gc in geo_context:
            gc["entries"] = [
                e for e in gc["entries"]
                if e.get("type") != "location" or e.get("name") not in removed_locations
            ]
        location_conflicts = [
            c for c in location_conflicts
            if c.get("entity") not in removed_locations
        ]

    # ── Layout geometry quality (2026-09-25) ──────────────────────────────
    # Absolute parent-child distance caliber. `quality_metrics` below is the
    # solver's constraint satisfaction — a different question on a different
    # scale, so this gets its own field rather than being merged into it.
    # See src/utils/layout_metrics.py for why the sibling-outlier ratio used by
    # the containment baseline was rejected (it has a denominator effect).
    layout_quality = None
    try:
        from src.utils.layout_metrics import compute_layout_metrics

        _lq_source = locals().get("layer_layouts") or {}
        if not _lq_source and layout_data:
            _lq_source = {target_layer: layout_data}
        _lq_coords: dict[str, tuple[float, float]] = {}
        _lq_layers: dict[str, str] = {}
        for _lid, _items in _lq_source.items():
            for _item in _items or []:
                _n = _item.get("name")
                if _n is None or _item.get("x") is None:
                    continue
                _lq_coords[_n] = (_item["x"], _item["y"])
                _lq_layers[_n] = _lid
        layout_quality = compute_layout_metrics(
            _lq_coords,
            {loc["name"]: loc["parent"] for loc in locations if loc.get("parent")},
            {loc["name"]: loc.get("tier", "") for loc in locations},
            _lq_layers,
            (float(_resp_cw), float(_resp_ch)),
        )
    except Exception:
        logger.warning("Layout quality metrics failed", exc_info=True)

    # ── Layout plausibility audit (read-only) ───────────────────────
    # The pipeline used to end at rendering: the solver's own satisfaction
    # score cannot see semantic absurdities (three continents piled at one
    # point, a sea on land, a kingdom 3000 px from its capital). The audit
    # runs on the final layout and reports them; it mutates nothing and a
    # failure here must never break map serving.
    plausibility = None
    try:
        from src.services.map_plausibility import audit_map_layout

        plausibility = audit_map_layout(
            locations, layout_data, spatial_constraints,
            landmass_result.get("landmasses", []) if landmass_result else [],
            float(_resp_cw), float(_resp_ch),
        )
        if not plausibility["ok"]:
            logger.info(
                "Map plausibility: %d violations %s",
                plausibility["violation_count"], plausibility["summary"],
            )
    except Exception:
        logger.warning("Map plausibility audit failed", exc_info=True)

    result: dict = {
        "locations": locations,
        "trajectories": dict(trajectories),
        "spatial_constraints": spatial_constraints,
        "layout": layout_data,
        "layout_mode": layout_mode,
        "quality_metrics": satisfaction,
        "layout_quality": layout_quality,
        "plausibility": plausibility,
        "terrain_url": terrain_url if not layer_id else None,
        "rivers": rivers,
        "roads": roads,
        "landmasses": landmass_result.get("landmasses", []),
        "shelves": landmass_result.get("shelves", []),
        # Depth band per shelf contour, 0 = nearest the shore. Short or empty on
        # artifacts written before the two-band shelf landed; the client treats
        # that as "no depth data, use one fill".
        "shelf_depth": landmass_result.get("shelf_depth", []),
        "region_boundaries": region_boundaries,
        "portals": portals_response,
        "revealed_location_names": revealed_names,
        "spatial_scale": _ws_scale,
        "layer_spatial_scales": ws.layer_spatial_scales if ws else {},
        "canvas_size": {"width": _resp_cw, "height": _resp_ch},
        "geography_context": geo_context,
        "location_conflicts": location_conflicts,
        "max_mention_count": max((loc["mention_count"] for loc in locations), default=1),
        "suggested_min_mentions": 3 if len(locations) > 300 else (2 if len(locations) > 150 else 1),
        "space_theme": space_theme,
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

    # Cache result
    _map_cache[cache_key] = (_time.time(), result)
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
        await conn.execute(
            "DELETE FROM map_geo_artifacts WHERE novel_id = ?", (novel_id,),
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
    """Invalidate layout cache when chapter facts are updated.

    Preserves the satisfaction baseline for quality regression comparison.
    """
    conn = await get_connection()
    try:
        # Save the old satisfaction as baseline before deleting the cache
        cursor = await conn.execute(
            "SELECT satisfaction_json FROM map_layouts WHERE novel_id = ? ORDER BY created_at DESC LIMIT 1",
            (novel_id,),
        )
        row = await cursor.fetchone()
        old_satisfaction = row["satisfaction_json"] if row and row["satisfaction_json"] else None

        await conn.execute(
            "DELETE FROM map_layouts WHERE novel_id = ?", (novel_id,),
        )
        await conn.execute(
            "DELETE FROM map_geo_artifacts WHERE novel_id = ?", (novel_id,),
        )

        # Store baseline in a sentinel row that will be overwritten on next compute
        if old_satisfaction:
            await conn.execute(
                """INSERT INTO map_layouts (novel_id, chapter_hash, layout_json, layout_mode, satisfaction_json, created_at)
                   VALUES (?, '__baseline__', '[]', 'baseline', ?, datetime('now'))""",
                (novel_id, old_satisfaction),
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
    canvas_width: int = CANVAS_WIDTH,
    canvas_height: int = CANVAS_HEIGHT,
) -> tuple[list[dict], str, str | None, dict | None]:
    """Load cached layout or compute a new one.

    Returns (layout_list, layout_mode, terrain_url, satisfaction_or_None).

    `canvas_width` / `canvas_height` are the layout's own coordinate range —
    `SPATIAL_SCALE_CANVAS[spatial_scale]`, the same value the response reports
    as `canvas_size` and the client stretches the terrain image over. They are
    passed on to `generate_terrain`, which needs them to map influence points
    onto the raster.
    """
    # Try loading from cache
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT layout_json, layout_mode, terrain_path, satisfaction_json FROM map_layouts WHERE novel_id = ? AND chapter_hash = ?",
            (novel_id, chapter_hash),
        )
        row = await cursor.fetchone()
        if row:
            layout_data = json.loads(row["layout_json"])
            terrain_path = row["terrain_path"]
            terrain_url = terrain_url_for(novel_id) if terrain_path else None
            cached_satisfaction = None
            if row["satisfaction_json"]:
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    cached_satisfaction = json.loads(row["satisfaction_json"])
            return layout_data, row["layout_mode"], terrain_url, cached_satisfaction
    finally:
        await conn.close()

    if not locations:
        return [], "hierarchy", None, None

    # Load user overrides
    user_overrides = await _load_user_overrides(novel_id)

    # Compute layout in thread pool to avoid blocking the event loop
    solver = ConstraintSolver(
        locations, spatial_constraints, user_overrides,
        first_chapter=first_chapter,
        location_region_bounds=location_region_bounds,
    )
    layout_coords, layout_mode, satisfaction = await asyncio.to_thread(solver.solve)
    layout_data = layout_to_list(layout_coords, locations)

    # Generate terrain image in thread pool (all non-geographic modes)
    terrain_path = None
    if len(layout_coords) >= 3:
        terrain_path = await asyncio.to_thread(
            generate_terrain, locations, layout_coords, novel_id,
            canvas_width=canvas_width, canvas_height=canvas_height,
        )

    terrain_url = terrain_url_for(novel_id) if terrain_path else None

    # Load quality baseline (from previous analysis) and compute diff
    satisfaction_json = json.dumps(satisfaction, ensure_ascii=False) if satisfaction else None
    conn = await get_connection()
    try:
        # Check for baseline row saved during invalidation
        cursor = await conn.execute(
            "SELECT satisfaction_json FROM map_layouts WHERE novel_id = ? AND chapter_hash = '__baseline__'",
            (novel_id,),
        )
        baseline_row = await cursor.fetchone()
        if baseline_row and baseline_row["satisfaction_json"] and satisfaction:
            try:
                baseline = json.loads(baseline_row["satisfaction_json"])
                # Compute quality diff
                old_sat = baseline.get("total_satisfaction", 0)
                new_sat = satisfaction.get("total_satisfaction", 0)
                old_cnt = baseline.get("total_constraints", 0)
                new_cnt = satisfaction.get("total_constraints", 0)
                satisfaction["quality_baseline"] = {
                    "previous_satisfaction": old_sat,
                    "previous_constraints": old_cnt,
                    "satisfaction_delta": round(new_sat - old_sat, 4),
                    "constraints_delta": new_cnt - old_cnt,
                }
            except (json.JSONDecodeError, TypeError):
                pass
            # Clean up baseline row
            await conn.execute(
                "DELETE FROM map_layouts WHERE novel_id = ? AND chapter_hash = '__baseline__'",
                (novel_id,),
            )

        # Cache the result (including satisfaction for quality baseline tracking)
        # Re-serialize after adding baseline diff
        satisfaction_json = json.dumps(satisfaction, ensure_ascii=False) if satisfaction else None
        await conn.execute(
            """INSERT INTO map_layouts (novel_id, chapter_hash, layout_json, layout_mode, terrain_path, satisfaction_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT (novel_id, chapter_hash)
               DO UPDATE SET layout_json=excluded.layout_json, layout_mode=excluded.layout_mode,
                            terrain_path=excluded.terrain_path, satisfaction_json=excluded.satisfaction_json,
                            created_at=datetime('now')""",
            (novel_id, chapter_hash, json.dumps(layout_data, ensure_ascii=False), layout_mode, terrain_path, satisfaction_json),
        )
        await conn.commit()
    finally:
        await conn.close()

    return layout_data, layout_mode, terrain_url, satisfaction


# ── Timeline (Events) ────────────────────────────


_ITEM_NOISE_ACTIONS = {"出现", "存在", "提及"}
_MIN_APPEARANCE_CHAPTERS = 3  # characters must appear in ≥ N chapters to get a 登场 event
_MAJOR_PARTICIPANT_THRESHOLD = 5  # ≥ N participants → is_major


async def get_timeline_data(
    novel_id: str, chapter_start: int, chapter_end: int
) -> dict:
    facts = await _load_facts_in_range(novel_id, chapter_start, chapter_end)

    events: list[dict] = []
    swimlanes: dict[str, list[int]] = defaultdict(list)
    seen_characters: set[str] = set()

    event_id = 0

    def _add(summary: str, etype: str, importance: str,
             participants: list[str], location: str | None, ch: int,
             extra: dict | None = None) -> None:
        nonlocal event_id
        is_major = len(participants) >= _MAJOR_PARTICIPANT_THRESHOLD
        if is_major and importance == "medium":
            importance = "high"
        entry: dict = {
            "id": event_id,
            "chapter": ch,
            "summary": summary,
            "type": etype,
            "importance": importance,
            "participants": participants,
            "location": location,
            "is_major": is_major,
        }
        if extra:
            entry.update(extra)
        events.append(entry)
        for p in participants:
            swimlanes[p].append(event_id)
        event_id += 1

    # ── Pre-compute character chapter counts (for 登场 filtering) ──
    char_chapters: dict[str, set[int]] = defaultdict(set)
    for fact in facts:
        for char in fact.characters:
            char_chapters[char.name].add(fact.chapter_id)

    # ── Pre-compute relationship history for change detection ──
    prev_relations: dict[tuple[str, str], str] = {}  # (a, b) → last relation_type

    # ── Load scene data for emotional_tone linking ──
    from src.db import chapter_fact_store
    scene_tone_map: dict[int, list[dict]] = {}  # chapter → [{characters, tone}]
    try:
        all_scenes = await chapter_fact_store.get_all_scenes(novel_id)
        for scene in all_scenes:
            ch_id = scene.get("chapter", 0)
            if ch_id < chapter_start or ch_id > chapter_end:
                continue
            scene_tone_map.setdefault(ch_id, []).append({
                "characters": set(scene.get("characters", [])),
                "tone": scene.get("emotional_tone", ""),
                "location": scene.get("location", ""),
            })
    except Exception:
        pass  # scene data unavailable, continue without

    def _match_scene_tone(ch: int, participants: list[str]) -> str | None:
        """Find best-matching scene emotional_tone for an event."""
        scenes = scene_tone_map.get(ch)
        if not scenes:
            return None
        p_set = set(participants)
        best_overlap = 0
        best_tone = None
        for s in scenes:
            overlap = len(p_set & s["characters"])
            if overlap > best_overlap and s["tone"]:
                best_overlap = overlap
                best_tone = s["tone"]
        return best_tone

    for fact in facts:
        ch = fact.chapter_id

        # ── Original events (战斗/成长/社交/旅行/其他) ──
        for ev in fact.events:
            tone = _match_scene_tone(ch, ev.participants)
            _add(ev.summary, ev.type, ev.importance,
                 ev.participants, ev.location, ch,
                 {"emotional_tone": tone} if tone else None)

        # ── Derived: 角色登场 (character first appearance) ──
        # Only emit for characters appearing in ≥ N chapters (filters one-timers)
        for char in fact.characters:
            if char.name not in seen_characters:
                seen_characters.add(char.name)
                if len(char_chapters.get(char.name, set())) >= _MIN_APPEARANCE_CHAPTERS:
                    loc = char.locations_in_chapter[0] if char.locations_in_chapter else None
                    _add(f"{char.name} 首次登场", "角色登场", "medium",
                         [char.name], loc, ch)

        # ── Derived: 物品交接 (item events) ──
        # Filter noise actions (出现/存在/提及)
        for ie in fact.item_events:
            if ie.action in _ITEM_NOISE_ACTIONS:
                continue
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

        # ── Derived: 关系变化 (relationship changes) ──
        for rel in fact.relationships:
            key = (min(rel.person_a, rel.person_b), max(rel.person_a, rel.person_b))
            old_type = prev_relations.get(key)
            prev_relations[key] = rel.relation_type
            # Only emit when explicitly new or changed
            if rel.is_new and old_type is None:
                summary = f"{rel.person_a} 与 {rel.person_b} 建立{rel.relation_type}关系"
                if rel.evidence:
                    summary += f"（{rel.evidence[:30]}）"
                _add(summary, "关系变化", "medium",
                     [rel.person_a, rel.person_b], None, ch)
            elif rel.previous_type and rel.previous_type != rel.relation_type:
                summary = f"{rel.person_a} 与 {rel.person_b} 关系变化：{rel.previous_type}→{rel.relation_type}"
                _add(summary, "关系变化", "high",
                     [rel.person_a, rel.person_b], None, ch)

    # ── Compute suggested defaults ──
    suggested_hidden_types = ["角色登场", "物品交接"]
    suggested_min_swimlane = 5 if len(swimlanes) > 100 else 3 if len(swimlanes) > 30 else 1

    return {
        "events": events,
        "swimlanes": dict(swimlanes),
        "suggested_hidden_types": suggested_hidden_types,
        "suggested_min_swimlane": suggested_min_swimlane,
        "total_swimlanes": len(swimlanes),
    }


# ── Factions (Organization Network) ──────────────

# Location types that indicate an organization.
# issue #70 (D4): 收窄词表 —— 「国/府/会/阁/堂/殿/院」同时是地点形态词
# (王国/府邸/会馆/宫殿/院落),会把非组织地点误判为 org,已移除。
_ORG_TYPE_KEYWORDS = ("门", "派", "宗", "帮", "教", "盟", "军", "朝", "族")

# 单字「门」的歧义排除:这些 type 里「门」指建筑(城门/宫门/营门),
# 不是门派。三国演义实测:19+ 个城门/宫门类地点(嘉德门/东门/北掖门…)
# 经 Source 2 被误判为 org。「城门口」由「城门」子串覆盖。
_ORG_TYPE_GATE_KEYWORDS = ("城门", "宫门", "营门")

# Source 4(概念路径)白名单:只有 category 明确是组织/势力/门派/政权类
# 的概念才可作为 org。军事计谋/战术/制度/器械/编制等一律排除 ——
# 三国演义实测 73 个概念 org 中 70 个是此类噪音(军令状/诈死计/掎角之势…)。
# 命中的真实例:宗教组织(五斗米道/太平道)、军事组织(御林军)。
_ORG_CONCEPT_CATEGORY_KEYWORDS = (
    "组织", "势力", "门派", "政权", "朝廷", "同盟", "帮派", "教派", "宗门",
    "家族",
)


def _is_org_type(loc_type: str) -> bool:
    """Check whether a location type represents an organization."""
    if any(kw in loc_type for kw in _ORG_TYPE_GATE_KEYWORDS):
        return False
    return any(kw in loc_type for kw in _ORG_TYPE_KEYWORDS)


def _is_org_concept_category(category: str) -> bool:
    """概念 category 是否明确指组织/势力(Source 4 白名单)。"""
    return any(kw in category for kw in _ORG_CONCEPT_CATEGORY_KEYWORDS)


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
    # Many sects/factions appear as locations (type="门派"/"帮派" etc.).
    # Characters visiting these locations are recorded as visitors (Source 3),
    # not members. 建筑类「门」(城门/宫门/营门)由 _is_org_type 排除。
    org_locations: set[str] = set()  # canonical location names that are orgs
    for fact in facts:
        for loc in fact.locations:
            loc_canonical = alias_map.get(loc.name, loc.name)
            if _is_org_type(loc.type) and loc_canonical not in org_info:
                org_info[loc_canonical] = {"name": loc_canonical, "type": loc.type}
            if _is_org_type(loc.type):
                org_locations.add(loc_canonical)

    # ── Source 3: characters at org-locations → visitors (NOT members) ──
    # issue #70 (D4): 到访 ≠ 成员。人物出现在 org 类地点只说明到访,
    # 输出到独立的 visitors 字段,不进成员列表、不计 member_count。
    # 已有 org_event 成员记录的人物不重复记为访客。
    org_visitors: dict[str, Counter] = defaultdict(Counter)  # org → person → visit count
    for fact in facts:
        for char in fact.characters:
            char_canonical = alias_map.get(char.name, char.name)
            for loc_name in char.locations_in_chapter:
                loc_canonical = alias_map.get(loc_name, loc_name)
                if loc_canonical in org_locations:
                    if char_canonical in org_members.get(loc_canonical, {}):
                        continue
                    org_visitors[loc_canonical][char_canonical] += 1

    # ── Source 4: new_concepts about org systems ──
    # 概念 category 是自由文本,军事计谋/战术/制度等噪音极大(实测占绝
    # 大多数),改用白名单:仅明确组织/势力/门派/政权类才进。
    for fact in facts:
        for concept in fact.new_concepts:
            cat = concept.category
            if _is_org_concept_category(cat) and concept.name not in org_info:
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

    visitors = {
        org: [
            {"person": person, "chapters": count}
            for person, count in counts.most_common()
        ]
        for org, counts in org_visitors.items()
    }

    return {
        "orgs": orgs,
        "relations": org_relations,
        "members": members,
        "visitors": visitors,
    }
