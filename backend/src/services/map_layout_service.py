"""Map layout engine: constraint-based coordinate solver + terrain generation.

Uses scipy.optimize.differential_evolution to find (x, y) coordinates for each
location that satisfy spatial constraints extracted from the novel text.
Falls back to hierarchical circular layout when constraints are insufficient.

Key features:
- Narrative-axis energy: spreads locations along the story's travel direction
  based on their first chapter appearance (e.g., eastward → westward for 西游记).
- Non-geographic location handling: celestial/underworld locations placed in
  dedicated zones outside the main geographic map area.
- Chapter-proximity placement: remaining locations placed near co-chapter
  neighbors instead of a spiral pattern.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from pathlib import Path

import numpy as np
from scipy.optimize import differential_evolution
from scipy.spatial import Voronoi

from src.infra.config import DATA_DIR

logger = logging.getLogger(__name__)

# Canvas coordinate range
CANVAS_SIZE = 1000
CANVAS_MIN = 50  # margin (wider to leave room for non-geo zones)
CANVAS_MAX = CANVAS_SIZE - 50

# Spatial scale → canvas size mapping
SPATIAL_SCALE_CANVAS: dict[str, int] = {
    "cosmic": 5000,
    "continental": 3000,
    "national": 2000,
    "urban": 1000,
    "local": 500,
}

# Minimum spacing between any two locations (pixels)
MIN_SPACING = 50

# Direction margin — how far A must exceed B in the expected axis
DIRECTION_MARGIN = 50

# Containment radius for parent locations
PARENT_RADIUS = 120

# Separation minimum distance
SEPARATION_DIST = 150

# Adjacent target distance
ADJACENT_DIST = 80

# Default distance for unquantified "near" references
DEFAULT_NEAR_DIST = 60
DEFAULT_FAR_DIST = 300

# Confidence priority for conflict resolution
_CONF_RANK = {"high": 3, "medium": 2, "low": 1}

# ── Narrative axis weight ───────────────────────────
# How strongly the chapter-order progression influences layout.
# Higher = locations spread more along the narrative travel axis.
NARRATIVE_AXIS_WEIGHT = 0.4

# ── Non-geographic location detection ──────────────
# Keywords that indicate celestial / underworld / metaphysical locations.
_CELESTIAL_KEYWORDS = ("天宫", "天庭", "天门", "天界", "三十三天", "大罗天",
                       "离恨天", "兜率宫", "凌霄殿", "蟠桃园", "瑶池",
                       "灵霄宝殿", "南天门", "北天门", "东天门", "西天门",
                       "九天应元府")
_UNDERWORLD_KEYWORDS = ("地府", "冥界", "幽冥", "阴司", "阴曹", "黄泉",
                        "奈何桥", "阎罗殿", "森罗殿", "枉死城")

# Celestial locations placed in top zone, underworld in bottom zone
_CELESTIAL_Y_RANGE = (CANVAS_MAX - 30, CANVAS_MAX)
_UNDERWORLD_Y_RANGE = (CANVAS_MIN, CANVAS_MIN + 30)

# ── Direction mapping ───────────────────────────────

_DIRECTION_VECTORS: dict[str, tuple[int, int]] = {
    # (dx_sign, dy_sign): +x = east, +y = north (screen y inverted later)
    "north_of": (0, 1),
    "south_of": (0, -1),
    "east_of": (1, 0),
    "west_of": (-1, 0),
    "northeast_of": (1, 1),
    "northwest_of": (-1, 1),
    "southeast_of": (1, -1),
    "southwest_of": (-1, -1),
}

# ── Region layout ─────────────────────────────────

# Direction → bounding box zone (x1, y1, x2, y2) on 1000×1000 canvas.
# Convention: +x = east (right), +y = north (up).
DIRECTION_ZONES: dict[str, tuple[float, float, float, float]] = {
    "east":   (600, 200, 950, 800),
    "west":   (50, 200, 400, 800),
    "south":  (200, 50, 800, 350),
    "north":  (200, 650, 800, 950),
    "center": (300, 300, 700, 700),
}

# Pastel palette for region boundary rendering (direction → RGBA-like hex)
_REGION_COLORS: dict[str, str] = {
    "east":   "#6699CC",  # steel blue
    "west":   "#CC9966",  # warm tan
    "south":  "#CC6666",  # soft red
    "north":  "#66AA99",  # teal
    "center": "#9966AA",  # purple
}
_REGION_COLOR_FALLBACK = "#999999"


def _layout_regions(
    regions: list[dict],
    canvas_size: int = CANVAS_SIZE,
) -> dict[str, dict]:
    """Compute bounding boxes for world regions based on cardinal direction.

    Args:
        regions: list of dicts with at least "name" and optional "cardinal_direction".
        canvas_size: canvas dimension (square).

    Returns:
        dict mapping region name to {"bounds": (x1, y1, x2, y2), "color": str}.
    """
    if not regions:
        return {}

    # Scale factor: DIRECTION_ZONES are defined for a 1000×1000 canvas
    scale = canvas_size / 1000.0

    # Group regions by direction
    direction_groups: dict[str, list[str]] = {}
    for r in regions:
        direction = r.get("cardinal_direction") or "center"
        if direction not in DIRECTION_ZONES:
            direction = "center"
        direction_groups.setdefault(direction, []).append(r["name"])

    result: dict[str, dict] = {}

    for direction, names in direction_groups.items():
        zone = DIRECTION_ZONES[direction]
        x1, y1, x2, y2 = zone[0] * scale, zone[1] * scale, zone[2] * scale, zone[3] * scale
        n = len(names)
        color = _REGION_COLORS.get(direction, _REGION_COLOR_FALLBACK)

        if n == 1:
            result[names[0]] = {"bounds": (x1, y1, x2, y2), "color": color}
        else:
            # Subdivide: use the longer axis to split
            w = x2 - x1
            h = y2 - y1
            if w >= h:
                # Split horizontally (along x)
                step = w / n
                for i, name in enumerate(names):
                    result[name] = {
                        "bounds": (
                            round(x1 + i * step, 1),
                            y1,
                            round(x1 + (i + 1) * step, 1),
                            y2,
                        ),
                        "color": color,
                    }
            else:
                # Split vertically (along y)
                step = h / n
                for i, name in enumerate(names):
                    result[name] = {
                        "bounds": (
                            x1,
                            round(y1 + i * step, 1),
                            x2,
                            round(y1 + (i + 1) * step, 1),
                        ),
                        "color": color,
                    }

    return result


# ── Voronoi boundary generation ──────────────────


def _clip_polygon_to_canvas(
    polygon: list[tuple[float, float]],
    canvas_size: int = CANVAS_SIZE,
) -> list[tuple[float, float]]:
    """Clip a polygon to the [0, canvas_size] rectangle using Sutherland-Hodgman."""

    def _inside(p: tuple[float, float], edge_start: tuple[float, float], edge_end: tuple[float, float]) -> bool:
        return (edge_end[0] - edge_start[0]) * (p[1] - edge_start[1]) - \
               (edge_end[1] - edge_start[1]) * (p[0] - edge_start[0]) >= 0

    def _intersect(
        p1: tuple[float, float], p2: tuple[float, float],
        e1: tuple[float, float], e2: tuple[float, float],
    ) -> tuple[float, float]:
        x1, y1 = p1
        x2, y2 = p2
        x3, y3 = e1
        x4, y4 = e2
        denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(denom) < 1e-10:
            return p2
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))

    # Clip edges: left, bottom, right, top (counter-clockwise winding)
    cs = float(canvas_size)
    clip_edges = [
        ((0.0, 0.0), (0.0, cs)),    # left
        ((0.0, 0.0), (cs, 0.0)),    # bottom (reversed for CCW)
        ((cs, 0.0), (cs, cs)),      # right
        ((0.0, cs), (cs, cs)),      # top
    ]
    # Proper CCW clip rectangle edges
    clip_edges = [
        ((0.0, 0.0), (cs, 0.0)),    # bottom: left→right
        ((cs, 0.0), (cs, cs)),      # right: bottom→top
        ((cs, cs), (0.0, cs)),      # top: right→left
        ((0.0, cs), (0.0, 0.0)),    # left: top→bottom
    ]

    output = list(polygon)
    for e_start, e_end in clip_edges:
        if not output:
            break
        inp = output
        output = []
        for i in range(len(inp)):
            current = inp[i]
            prev = inp[i - 1]
            curr_in = _inside(current, e_start, e_end)
            prev_in = _inside(prev, e_start, e_end)
            if curr_in:
                if not prev_in:
                    output.append(_intersect(prev, current, e_start, e_end))
                output.append(current)
            elif prev_in:
                output.append(_intersect(prev, current, e_start, e_end))

    return output


def generate_voronoi_boundaries(
    region_layout: dict[str, dict],
    canvas_size: int = CANVAS_SIZE,
) -> dict[str, dict]:
    """Generate Voronoi polygon boundaries from region layout centers.

    Args:
        region_layout: Output of _layout_regions(), mapping name → {"bounds", "color"}.
        canvas_size: Canvas dimension (square).

    Returns:
        dict mapping region name → {"polygon": [(x,y),...], "center": (cx,cy), "color": str}.
    """
    if not region_layout:
        return {}

    names = list(region_layout.keys())
    centers: list[tuple[float, float]] = []
    colors: list[str] = []

    for name in names:
        rd = region_layout[name]
        x1, y1, x2, y2 = rd["bounds"]
        centers.append(((x1 + x2) / 2, (y1 + y2) / 2))
        colors.append(rd["color"])

    # Fallback for < 2 regions: convert bounds to rectangle polygon
    if len(names) < 2:
        result: dict[str, dict] = {}
        for i, name in enumerate(names):
            rd = region_layout[name]
            x1, y1, x2, y2 = rd["bounds"]
            result[name] = {
                "polygon": [(x1, y1), (x2, y1), (x2, y2), (x1, y2)],
                "center": centers[i],
                "color": colors[i],
            }
        return result

    # Build Voronoi with mirror points to ensure edge regions are closed
    points = list(centers)
    cs = float(canvas_size)
    n_orig = len(points)

    # Add 4 mirror points per seed, reflected across canvas boundaries
    for cx, cy in centers:
        points.append((-cx, cy))             # mirror across left edge
        points.append((2 * cs - cx, cy))     # mirror across right edge
        points.append((cx, -cy))             # mirror across bottom edge
        points.append((cx, 2 * cs - cy))     # mirror across top edge

    point_arr = np.array(points, dtype=np.float64)
    vor = Voronoi(point_arr)

    result = {}
    for i, name in enumerate(names):
        region_idx = vor.point_region[i]
        region = vor.regions[region_idx]

        if not region or -1 in region:
            # Open region — fallback to rectangle
            rd = region_layout[name]
            x1, y1, x2, y2 = rd["bounds"]
            result[name] = {
                "polygon": [(x1, y1), (x2, y1), (x2, y2), (x1, y2)],
                "center": centers[i],
                "color": colors[i],
            }
            continue

        # Extract Voronoi cell vertices
        verts = [(float(vor.vertices[vi][0]), float(vor.vertices[vi][1]))
                 for vi in region]

        # Clip to canvas
        clipped = _clip_polygon_to_canvas(verts, canvas_size)
        if len(clipped) < 3:
            # Degenerate — fallback to rectangle
            rd = region_layout[name]
            x1, y1, x2, y2 = rd["bounds"]
            clipped = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]

        # Round coordinates
        clipped = [(round(x, 1), round(y, 1)) for x, y in clipped]

        result[name] = {
            "polygon": clipped,
            "center": (round(centers[i][0], 1), round(centers[i][1], 1)),
            "color": colors[i],
        }

    return result


# ── Layered layout engine (Story 7.7) ─────────────


# Canvas sizes for non-overworld layers
_LAYER_CANVAS_SIZES: dict[str, int] = {
    "pocket": 300,
    "sky": 600,
    "underground": 600,
    "sea": 600,
    "spirit": 400,
}


def _solve_region(
    region_name: str,
    region_bounds: tuple[float, float, float, float],
    locations: list[dict],
    constraints: list[dict],
    user_overrides: dict[str, tuple[float, float]] | None = None,
    first_chapter: dict[str, int] | None = None,
) -> dict[str, tuple[float, float]]:
    """Run ConstraintSolver for a single region's locations within its bounding box.

    Returns layout dict: name → (x, y).
    """
    if not locations:
        return {}

    loc_names = {loc["name"] for loc in locations}

    # Filter constraints to only those referencing locations in this region
    region_constraints = [
        c for c in constraints
        if c["source"] in loc_names and c["target"] in loc_names
    ]

    solver = ConstraintSolver(
        locations,
        region_constraints,
        user_overrides=user_overrides,
        first_chapter=first_chapter,
        canvas_bounds=region_bounds,
    )
    coords, _ = solver.solve()
    return coords


def _solve_layer(
    layer_id: str,
    layer_type: str,
    locations: list[dict],
    constraints: list[dict],
    user_overrides: dict[str, tuple[float, float]] | None = None,
    first_chapter: dict[str, int] | None = None,
) -> dict[str, tuple[float, float]]:
    """Run layout for a non-overworld layer using an independent canvas.

    Returns layout dict: name → (x, y) in the layer's local coordinate system.
    """
    if not locations:
        return {}

    canvas_size = _LAYER_CANVAS_SIZES.get(layer_type, 400)
    margin = max(10, canvas_size // 20)
    bounds = (margin, margin, canvas_size - margin, canvas_size - margin)

    loc_names = {loc["name"] for loc in locations}
    layer_constraints = [
        c for c in constraints
        if c["source"] in loc_names and c["target"] in loc_names
    ]

    solver = ConstraintSolver(
        locations,
        layer_constraints,
        user_overrides=user_overrides,
        first_chapter=first_chapter,
        canvas_bounds=bounds,
    )
    coords, _ = solver.solve()
    return coords


def _annotate_portals(
    overworld_layout: dict[str, tuple[float, float]],
    portals: list[dict],
) -> list[dict]:
    """Generate portal marker items positioned at their source_location.

    Each portal item contains: name, x, y, source_layer, target_layer, is_portal=True.
    If source_location is not in the layout, falls back to nearest laid-out location.
    """
    if not portals or not overworld_layout:
        return []

    markers: list[dict] = []
    for portal in portals:
        src_loc = portal.get("source_location", "")
        if src_loc in overworld_layout:
            x, y = overworld_layout[src_loc]
        else:
            # Fallback: place near the nearest known location
            if overworld_layout:
                nearest = min(
                    overworld_layout.values(),
                    key=lambda pos: pos[0] ** 2 + pos[1] ** 2,
                )
                x, y = nearest[0] + 15, nearest[1] + 15
            else:
                continue

        markers.append({
            "name": portal.get("name", ""),
            "x": round(x, 1),
            "y": round(y, 1),
            "source_layer": portal.get("source_layer", ""),
            "target_layer": portal.get("target_layer", ""),
            "is_portal": True,
        })

    return markers


def compute_layered_layout(
    world_structure: dict,
    all_locations: list[dict],
    all_constraints: list[dict],
    user_overrides: dict[str, tuple[float, float]] | None = None,
    first_chapter: dict[str, int] | None = None,
    spatial_scale: str | None = None,
) -> dict[str, list[dict]]:
    """Compute per-layer layouts using region-aware solving.

    Args:
        world_structure: WorldStructure.model_dump() dict.
        all_locations: All location dicts from the map data pipeline.
        all_constraints: All spatial constraint dicts.
        user_overrides: User-adjusted coordinates.
        first_chapter: Location name → first chapter appearance.
        spatial_scale: SpatialScale value for dynamic canvas sizing.

    Returns:
        { layer_id: [{"name", "x", "y", "radius", ...}, ...] }
        The "overworld" layer also includes portal markers.
    """
    layers = world_structure.get("layers", [])
    portals = world_structure.get("portals", [])
    location_layer_map = world_structure.get("location_layer_map", {})
    location_region_map = world_structure.get("location_region_map", {})

    # Dynamic canvas size for overworld based on spatial scale
    canvas_size = SPATIAL_SCALE_CANVAS.get(spatial_scale or "", CANVAS_SIZE)
    canvas_margin = max(50, canvas_size // 20)
    overworld_bounds = (canvas_margin, canvas_margin, canvas_size - canvas_margin, canvas_size - canvas_margin)

    if not layers:
        return {}

    # Build location lookup
    loc_by_name: dict[str, dict] = {loc["name"]: loc for loc in all_locations}

    # Partition locations by layer
    layer_locations: dict[str, list[dict]] = {layer["layer_id"]: [] for layer in layers}
    unassigned: list[dict] = []

    for loc in all_locations:
        name = loc["name"]
        layer_id = location_layer_map.get(name, "overworld")
        if layer_id in layer_locations:
            layer_locations[layer_id].append(loc)
        else:
            # Instance layers or unknown → create bucket
            layer_locations.setdefault(layer_id, []).append(loc)

    result: dict[str, list[dict]] = {}

    for layer in layers:
        layer_id = layer["layer_id"]
        layer_type = layer.get("layer_type", "pocket")
        locs = layer_locations.get(layer_id, [])

        if not locs:
            result[layer_id] = []
            continue

        if layer_id == "overworld":
            # ── Overworld: solve per-region then merge ──
            regions = layer.get("regions", [])
            if regions:
                layout_coords = _solve_overworld_by_region(
                    regions, locs, all_constraints, location_region_map,
                    user_overrides=user_overrides,
                    first_chapter=first_chapter,
                    canvas_size=canvas_size,
                )
            else:
                # No regions → global solve
                solver = ConstraintSolver(
                    locs, all_constraints,
                    user_overrides=user_overrides,
                    first_chapter=first_chapter,
                    canvas_bounds=overworld_bounds,
                )
                layout_coords, _ = solver.solve()

            layout_list = layout_to_list(layout_coords, locs)

            # Annotate portals
            portal_dicts = [
                {
                    "name": p.get("name", ""),
                    "source_layer": p.get("source_layer", ""),
                    "source_location": p.get("source_location", ""),
                    "target_layer": p.get("target_layer", ""),
                    "target_location": p.get("target_location", ""),
                    "is_bidirectional": p.get("is_bidirectional", True),
                }
                for p in portals
            ]
            portal_markers = _annotate_portals(layout_coords, portal_dicts)
            layout_list.extend(portal_markers)

            result[layer_id] = layout_list
        else:
            # ── Non-overworld layers: independent canvas ──
            layout_coords = _solve_layer(
                layer_id, layer_type, locs, all_constraints,
                user_overrides=user_overrides,
                first_chapter=first_chapter,
            )
            result[layer_id] = layout_to_list(layout_coords, locs)

    # Handle any extra instance layers not in world_structure.layers
    known_layer_ids = {layer["layer_id"] for layer in layers}
    for layer_id, locs in layer_locations.items():
        if layer_id not in known_layer_ids and locs:
            layout_coords = _solve_layer(
                layer_id, "pocket", locs, all_constraints,
                user_overrides=user_overrides,
                first_chapter=first_chapter,
            )
            result[layer_id] = layout_to_list(layout_coords, locs)

    return result


def _solve_overworld_by_region(
    regions: list[dict],
    locations: list[dict],
    constraints: list[dict],
    location_region_map: dict[str, str],
    user_overrides: dict[str, tuple[float, float]] | None = None,
    first_chapter: dict[str, int] | None = None,
    canvas_size: int = CANVAS_SIZE,
) -> dict[str, tuple[float, float]]:
    """Solve overworld layout by partitioning into regions.

    Locations assigned to a region are solved within that region's bounding box.
    Unassigned locations go through a global fallback solve.
    """
    # Compute region bounding boxes
    region_dicts = [
        {
            "name": r.get("name", ""),
            "cardinal_direction": r.get("cardinal_direction"),
        }
        for r in regions
    ]
    region_layout = _layout_regions(region_dicts, canvas_size=canvas_size)

    # Partition locations by region
    region_locs: dict[str, list[dict]] = {r["name"]: [] for r in region_dicts}
    unassigned_locs: list[dict] = []

    for loc in locations:
        region_name = location_region_map.get(loc["name"])
        if region_name and region_name in region_locs:
            region_locs[region_name].append(loc)
        else:
            unassigned_locs.append(loc)

    # Solve each region independently
    merged_layout: dict[str, tuple[float, float]] = {}

    for region_name, rlocs in region_locs.items():
        if not rlocs:
            continue
        bounds = region_layout[region_name]["bounds"]
        coords = _solve_region(
            region_name, bounds, rlocs, constraints,
            user_overrides=user_overrides,
            first_chapter=first_chapter,
        )
        merged_layout.update(coords)

    # Solve unassigned locations with the full canvas, but with per-location
    # region bounds for any that happen to belong to a region
    if unassigned_locs:
        loc_region_bounds: dict[str, tuple[float, float, float, float]] = {}
        for loc in unassigned_locs:
            rn = location_region_map.get(loc["name"])
            if rn and rn in region_layout:
                loc_region_bounds[loc["name"]] = region_layout[rn]["bounds"]

        margin = max(50, canvas_size // 20)
        fallback_bounds = (margin, margin, canvas_size - margin, canvas_size - margin)
        solver = ConstraintSolver(
            unassigned_locs, constraints,
            user_overrides=user_overrides,
            first_chapter=first_chapter,
            location_region_bounds=loc_region_bounds,
            canvas_bounds=fallback_bounds,
        )
        coords, _ = solver.solve()
        merged_layout.update(coords)

    return merged_layout


# ── Distance parsing ───────────────────────────────

# Travel speed in canvas-units per day
_SPEED_MAP = {
    "步行": 30, "走": 30, "行走": 30,
    "骑马": 60, "骑": 60, "马": 60,
    "飞行": 200, "飞": 200, "御剑": 200, "遁光": 200,
    "传送": 0,
}

_CHINESE_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                   "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
                   "百": 100, "千": 1000, "万": 10000, "数": 3, "几": 3}

_DAY_PATTERN = re.compile(
    r"([一二三四五六七八九十百千万数几\d]+)\s*[天日]"
)
_LI_PATTERN = re.compile(
    r"([一二三四五六七八九十百千万数几\d]+)\s*[里]"
)


def _parse_chinese_number(s: str) -> float:
    """Parse simple Chinese number strings like '三', '十五', '百' to float."""
    if s.isdigit():
        return float(s)
    # Try direct lookup
    if s in _CHINESE_DIGITS:
        return float(_CHINESE_DIGITS[s])
    # Handle compound like 三十, 十五, 三百
    total = 0.0
    current = 0.0
    for ch in s:
        if ch in _CHINESE_DIGITS:
            val = _CHINESE_DIGITS[ch]
            if val >= 10:  # multiplier
                if current == 0:
                    current = 1
                total += current * val
                current = 0
            else:
                current = val
        elif ch.isdigit():
            current = current * 10 + int(ch)
    total += current
    return total if total > 0 else 3.0  # fallback


def parse_distance(value: str) -> float:
    """Convert a distance description to canvas units.

    Examples:
      "三天路程（步行）" → 3 * 30 = 90
      "百里" → 100 * 0.5 = 50
      "very_near" → 60
      "数日飞行" → 3 * 200 = 600 → clamped to 400
    """
    if not value:
        return DEFAULT_NEAR_DIST

    # Check for keywords
    lower = value.lower()
    if "very_near" in lower or "很近" in value:
        return DEFAULT_NEAR_DIST
    if "near" in lower or "近" in value:
        return DEFAULT_NEAR_DIST
    if "far" in lower or "远" in value or "遥远" in value:
        return DEFAULT_FAR_DIST

    # Try to detect travel mode
    speed = 30  # default: walking
    for keyword, spd in _SPEED_MAP.items():
        if keyword in value:
            speed = spd
            break

    # Try day-based pattern: "三天", "5日"
    m = _DAY_PATTERN.search(value)
    if m:
        days = _parse_chinese_number(m.group(1))
        dist = days * speed
        return min(dist, 400)  # clamp to prevent dominating the canvas

    # Try li-based pattern: "百里", "三千里"
    m = _LI_PATTERN.search(value)
    if m:
        li = _parse_chinese_number(m.group(1))
        # 1 里 ≈ 0.5 canvas units (scaled for reasonable map)
        dist = li * 0.5
        return min(dist, 400)

    return DEFAULT_NEAR_DIST


# ── Conflict detection ─────────────────────────────


def _detect_and_remove_conflicts(
    constraints: list[dict],
) -> list[dict]:
    """Remove conflicting direction constraints, keeping higher confidence."""
    # Group direction constraints by (source, target) pair (unordered)
    direction_map: dict[tuple[str, str], list[dict]] = {}
    non_direction = []

    for c in constraints:
        if c["relation_type"] == "direction":
            key = tuple(sorted([c["source"], c["target"]]))
            direction_map.setdefault(key, []).append(c)
        else:
            non_direction.append(c)

    kept_directions = []
    for key, group in direction_map.items():
        if len(group) == 1:
            kept_directions.append(group[0])
            continue

        # Check for conflicts: e.g., A north_of B AND B north_of A
        # (which means A south_of B conflict)
        best = max(group, key=lambda c: _CONF_RANK.get(c["confidence"], 1))
        # Check if there are contradictory directions
        has_conflict = False
        for c in group:
            if c is best:
                continue
            # If same pair has opposite directions, it's a conflict
            if _are_opposing(best, c):
                has_conflict = True
                logger.warning(
                    "Spatial conflict: %s %s %s vs %s %s %s — keeping higher confidence",
                    best["source"], best["value"], best["target"],
                    c["source"], c["value"], c["target"],
                )
        if has_conflict:
            kept_directions.append(best)
        else:
            kept_directions.extend(group)

    return non_direction + kept_directions


def _are_opposing(c1: dict, c2: dict) -> bool:
    """Check if two direction constraints are contradictory."""
    opposites = {
        "north_of": "south_of", "south_of": "north_of",
        "east_of": "west_of", "west_of": "east_of",
        "northeast_of": "southwest_of", "southwest_of": "northeast_of",
        "northwest_of": "southeast_of", "southeast_of": "northwest_of",
    }
    v1 = c1["value"]
    v2 = c2["value"]
    # Direct opposition
    if v1 in opposites and opposites[v1] == v2:
        # Check if it's the same directional assertion
        if c1["source"] == c2["source"] and c1["target"] == c2["target"]:
            return True
        # Or reversed pair with same direction
        if c1["source"] == c2["target"] and c1["target"] == c2["source"]:
            return True
    # Same direction but reversed pair: A north_of B AND B north_of A
    if v1 == v2 and c1["source"] == c2["target"] and c1["target"] == c2["source"]:
        return True
    return False


# ── Constraint Solver ──────────────────────────────


# Maximum number of locations to send into the constraint solver.
# Locations beyond this are placed via hierarchy layout relative to solved anchors.
MAX_SOLVER_LOCATIONS = 100


def _is_celestial(name: str) -> bool:
    """Check if a location name indicates a celestial/heavenly place."""
    return any(kw in name for kw in _CELESTIAL_KEYWORDS)


def _is_underworld(name: str) -> bool:
    """Check if a location name indicates an underworld place."""
    return any(kw in name for kw in _UNDERWORLD_KEYWORDS)


def _is_non_geographic(name: str) -> bool:
    """Check if a location is not a physical geographic place."""
    return _is_celestial(name) or _is_underworld(name)


def _detect_narrative_axis(
    constraints: list[dict],
    first_chapter: dict[str, int],
    locations: list[dict] | None = None,
) -> tuple[float, float]:
    """Detect the dominant travel direction of the story.

    Strategy:
    1. Look at large-scale geographic locations (洲/国/域/界) with directional
       names (东/西/南/北) to find the continental-level travel axis.
    2. Use the protagonist's trajectory (location visit order) correlated with
       contains/direction relationships.
    3. Fall back to direction constraints weighted by chapter separation.

    Returns a unit vector (dx, dy) pointing in the travel direction.
    """
    if not first_chapter:
        return (-1.0, 0.0)

    # ── Strategy 1: Large-scale geographic name analysis ──
    # Only consider significant locations (level 0-1, or macro types like 洲/国/域)
    _MACRO_TYPE_KW = ("洲", "国", "域", "界", "大陆", "大海", "海", "部洲")

    loc_lookup: dict[str, dict] = {}
    if locations:
        loc_lookup = {loc["name"]: loc for loc in locations}

    def is_macro(name: str) -> bool:
        """Is this a macro-scale geographic entity?"""
        info = loc_lookup.get(name, {})
        loc_type = info.get("type", "")
        level = info.get("level", 99)
        if level <= 1:
            return True
        if any(kw in loc_type for kw in _MACRO_TYPE_KW):
            return True
        if any(kw in name for kw in _MACRO_TYPE_KW):
            return True
        return False

    east_chapters: list[int] = []
    west_chapters: list[int] = []

    for name, ch in first_chapter.items():
        if _is_non_geographic(name):
            continue
        if not is_macro(name):
            continue
        if "东" in name:
            east_chapters.append(ch)
        if "西" in name:
            west_chapters.append(ch)

    net_dx, net_dy = 0.0, 0.0

    if east_chapters and west_chapters:
        logger.info(
            "Macro east-locations: %s, west-locations: %s",
            [(n, first_chapter[n]) for n in first_chapter
             if "东" in n and is_macro(n) and not _is_non_geographic(n)],
            [(n, first_chapter[n]) for n in first_chapter
             if "西" in n and is_macro(n) and not _is_non_geographic(n)],
        )

    # ── Strategy 2: Use contains hierarchy to find starting region ──
    # If location A contains the earliest-appearing locations, A is the start.
    # Check if contains constraints link early locations to 东/西 regions.
    start_region_dir = 0  # +1 = east start, -1 = west start
    earliest_locs = sorted(
        [(ch, name) for name, ch in first_chapter.items()
         if ch > 0 and not _is_non_geographic(name)],
        key=lambda x: x[0],
    )[:10]  # top 10 earliest locations
    earliest_names = {name for _, name in earliest_locs}

    for c in constraints:
        if c["relation_type"] != "contains":
            continue
        parent_name = c["source"]
        child_name = c["target"]
        # If a 东-named region contains an early location, east is the start
        if child_name in earliest_names or parent_name in earliest_names:
            region = parent_name  # the containing region
            if "东" in region:
                start_region_dir += 1
            elif "西" in region:
                start_region_dir -= 1

    # Also check parent fields directly
    for _, name in earliest_locs:
        info = loc_lookup.get(name, {})
        parent = info.get("parent", "")
        if parent and "东" in parent:
            start_region_dir += 1
        elif parent and "西" in parent:
            start_region_dir -= 1

    if start_region_dir > 0:
        # East is the starting region → journey goes east to west
        net_dx = -1.0
        logger.info("Contains hierarchy: east is start region (score=%d) → westward", start_region_dir)
    elif start_region_dir < 0:
        net_dx = 1.0
        logger.info("Contains hierarchy: west is start region (score=%d) → eastward", start_region_dir)

    if abs(net_dx) > 0.01 or abs(net_dy) > 0.01:
        magnitude = math.sqrt(net_dx ** 2 + net_dy ** 2)
        return (net_dx / magnitude, net_dy / magnitude)

    # ── Strategy 3: Direction constraints weighted by chapter separation ──
    for c in constraints:
        if c["relation_type"] != "direction":
            continue
        vec = _DIRECTION_VECTORS.get(c["value"])
        if vec is None:
            continue

        src_ch = first_chapter.get(c["source"], 0)
        tgt_ch = first_chapter.get(c["target"], 0)
        if src_ch == 0 or tgt_ch == 0:
            continue

        ch_diff = src_ch - tgt_ch
        if abs(ch_diff) < 10:
            continue

        weight = 1.0 if abs(ch_diff) < 20 else 2.0
        if ch_diff > 0:
            net_dx += vec[0] * weight
            net_dy += vec[1] * weight
        else:
            net_dx -= vec[0] * weight
            net_dy -= vec[1] * weight

    if abs(net_dx) > 0.5 or abs(net_dy) > 0.5:
        magnitude = math.sqrt(net_dx ** 2 + net_dy ** 2)
        return (net_dx / magnitude, net_dy / magnitude)

    return (-1.0, 0.0)  # default: westward


class ConstraintSolver:
    """Compute (x, y) layout for locations using spatial constraints."""

    def __init__(
        self,
        locations: list[dict],
        constraints: list[dict],
        user_overrides: dict[str, tuple[float, float]] | None = None,
        first_chapter: dict[str, int] | None = None,
        location_region_bounds: dict[str, tuple[float, float, float, float]] | None = None,
        canvas_bounds: tuple[float, float, float, float] | None = None,
    ):
        self.all_locations = locations
        self.constraints = _detect_and_remove_conflicts(constraints)
        self.user_overrides = user_overrides or {}
        self.first_chapter = first_chapter or {}
        # Per-location region bounds: name -> (x1, y1, x2, y2)
        self._location_region_bounds = location_region_bounds or {}
        # Custom canvas bounds: (x_min, y_min, x_max, y_max)
        if canvas_bounds is not None:
            self._canvas_min_x = canvas_bounds[0]
            self._canvas_min_y = canvas_bounds[1]
            self._canvas_max_x = canvas_bounds[2]
            self._canvas_max_y = canvas_bounds[3]
        else:
            self._canvas_min_x = CANVAS_MIN
            self._canvas_min_y = CANVAS_MIN
            self._canvas_max_x = CANVAS_MAX
            self._canvas_max_y = CANVAS_MAX

        # Convenience canvas helpers
        self._canvas_cx = (self._canvas_min_x + self._canvas_max_x) / 2
        self._canvas_cy = (self._canvas_min_y + self._canvas_max_y) / 2

        # Dynamic min spacing proportional to canvas size
        canvas_w = self._canvas_max_x - self._canvas_min_x
        self._min_spacing = max(MIN_SPACING, canvas_w * 0.02)

        # Detect narrative travel axis (uses original locations before celestial/underworld split)
        self._narrative_axis = _detect_narrative_axis(
            self.constraints, self.first_chapter, locations,
        )
        logger.info("Narrative axis: (%.2f, %.2f)", *self._narrative_axis)

        # Compute chapter range for normalization
        chapters = [ch for ch in self.first_chapter.values() if ch > 0]
        self._min_chapter = min(chapters) if chapters else 1
        self._max_chapter = max(chapters) if chapters else 1

        # Compute direction hints for locations (weak positional preferences)
        from src.services.location_hint_service import batch_extract_direction_hints
        self._direction_hints = batch_extract_direction_hints(locations)

        # Separate non-geographic locations
        self._celestial: list[dict] = []
        self._underworld: list[dict] = []
        geo_locations = []
        for loc in locations:
            name = loc["name"]
            if _is_celestial(name):
                self._celestial.append(loc)
            elif _is_underworld(name):
                self._underworld.append(loc)
            else:
                geo_locations.append(loc)

        if self._celestial:
            logger.info("Separated %d celestial locations", len(self._celestial))
        if self._underworld:
            logger.info("Separated %d underworld locations", len(self._underworld))

        self.all_locations = geo_locations  # only geographic for solver

        # Build parent -> children mapping (for all locations including non-geo)
        self._parent_map: dict[str, str | None] = {}
        for loc in locations:
            self._parent_map[loc["name"]] = loc.get("parent")

        self.children: dict[str, list[str]] = {}
        self.roots: list[str] = []
        all_names = {loc["name"] for loc in locations}
        for name, parent in self._parent_map.items():
            if _is_non_geographic(name):
                continue
            if parent and parent in all_names and not _is_non_geographic(parent):
                self.children.setdefault(parent, []).append(name)
            else:
                self.roots.append(name)

        # Select locations for the solver: keep the most important ones
        self._select_solver_locations()

    def _select_solver_locations(self) -> None:
        """Choose which locations go into the constraint solver vs hierarchy placement."""
        # Collect names referenced in constraints
        constrained_names: set[str] = set()
        for c in self.constraints:
            constrained_names.add(c["source"])
            constrained_names.add(c["target"])

        # Score each location: constrained > user-overridden > high-mention > others
        scored: list[tuple[float, dict]] = []
        for loc in self.all_locations:
            name = loc["name"]
            score = loc.get("mention_count", 0)
            if name in constrained_names:
                score += 10000  # always include constrained locations
            if name in self.user_overrides:
                score += 5000
            # Bonus for root/high-level locations (they anchor the layout)
            level = loc.get("level", 0)
            if level == 0:
                score += 100
            elif level == 1:
                score += 50
            scored.append((score, loc))

        scored.sort(key=lambda x: -x[0])

        # Take top N
        solver_locs = [loc for _, loc in scored[:MAX_SOLVER_LOCATIONS]]

        self.locations = solver_locs
        self.loc_names = [loc["name"] for loc in solver_locs]
        self.loc_index = {name: i for i, name in enumerate(self.loc_names)}
        self.n = len(self.loc_names)

        # Remaining locations to be placed via hierarchy
        solver_set = set(self.loc_names)
        self._remaining = [loc for loc in self.all_locations if loc["name"] not in solver_set]

        logger.info(
            "Selected %d / %d locations for solver (%d constrained, %d remaining)",
            self.n, len(self.all_locations), len(constrained_names), len(self._remaining),
        )

    def solve(self) -> tuple[dict[str, tuple[float, float]], str]:
        """Solve layout. Returns (name->coords, layout_mode)."""
        if len(self.constraints) < 3 or self.n < 2:
            logger.info(
                "Insufficient constraints (%d) or locations (%d), using hierarchy layout",
                len(self.constraints), self.n,
            )
            layout = self._hierarchy_layout()
            self._place_remaining(layout)
            return layout, "hierarchy"

        logger.info(
            "Solving layout for %d locations with %d constraints",
            self.n, len(self.constraints),
        )

        # Build bounds: each location has (x, y) within canvas or region bounds.
        # User-overridden locations are fixed (narrow bounds).
        # Locations in a region are constrained to the region bounding box.
        bounds = []
        for name in self.loc_names:
            if name in self.user_overrides:
                ox, oy = self.user_overrides[name]
                bounds.extend([(ox - 0.1, ox + 0.1), (oy - 0.1, oy + 0.1)])
            elif name in self._location_region_bounds:
                rx1, ry1, rx2, ry2 = self._location_region_bounds[name]
                bounds.extend([(rx1, rx2), (ry1, ry2)])
            else:
                bounds.extend([
                    (self._canvas_min_x, self._canvas_max_x),
                    (self._canvas_min_y, self._canvas_max_y),
                ])

        # Filter constraints to only those referencing solver locations
        valid_constraints = [
            c for c in self.constraints
            if c["source"] in self.loc_index and c["target"] in self.loc_index
        ]

        if len(valid_constraints) < 3:
            logger.info("Only %d valid constraints after filtering, using hierarchy", len(valid_constraints))
            layout = self._hierarchy_layout()
            self._place_remaining(layout)
            return layout, "hierarchy"

        # Scale solver budget based on problem size
        # With 100 locations (200 params), we need enough iterations but not excessive
        maxiter = max(80, min(300, 5000 // max(self.n, 1)))
        popsize = max(5, min(12, 400 // max(self.n, 1)))

        try:
            result = differential_evolution(
                self._energy,
                bounds=bounds,
                args=(valid_constraints,),
                maxiter=maxiter,
                popsize=popsize,
                tol=1e-4,
                seed=42,
                polish=False,
            )
            coords = result.x.reshape(-1, 2)
            layout = {
                name: (float(coords[i, 0]), float(coords[i, 1]))
                for i, name in enumerate(self.loc_names)
            }
            logger.info("Constraint solver converged: energy=%.2f, iter=%d", result.fun, result.nit)
            self._place_remaining(layout)
            return layout, "constraint"
        except Exception:
            logger.exception("Constraint solver failed, falling back to hierarchy")
            layout = self._hierarchy_layout()
            self._place_remaining(layout)
            return layout, "hierarchy"

    def _place_remaining(self, layout: dict[str, tuple[float, float]]) -> None:
        """Place locations not included in the solver using chapter-proximity heuristics.

        Strategy:
        1. User overrides take priority.
        2. If parent is in layout: jitter around parent.
        3. Otherwise: find solved locations from the same or nearby chapters
           and place near their centroid, offset along the narrative axis.
        4. Last resort: interpolate position along narrative axis based on chapter number.
        """
        # Build chapter->solved_locations lookup for proximity placement
        chapter_locs: dict[int, list[str]] = {}
        for name in layout:
            ch = self.first_chapter.get(name, 0)
            if ch > 0:
                chapter_locs.setdefault(ch, []).append(name)

        orphan_idx = 0  # for jittering orphans that share positions

        for loc in self._remaining:
            name = loc["name"]
            if name in layout:
                continue
            if name in self.user_overrides:
                layout[name] = self.user_overrides[name]
                continue

            parent = self._parent_map.get(name)
            if parent and parent in layout:
                px, py = layout[parent]
                children_here = self.children.get(parent, [])
                idx = children_here.index(name) if name in children_here else 0
                angle = 2 * math.pi * idx / max(len(children_here), 1)
                r = 30 + 8 * (loc.get("level", 0))
                x = max(self._canvas_min_x, min(self._canvas_max_x, px + r * math.cos(angle)))
                y = max(self._canvas_min_y, min(self._canvas_max_y, py + r * math.sin(angle)))
                layout[name] = (x, y)
                continue

            # Chapter-proximity: find solved locations from same or nearby chapters
            ch = self.first_chapter.get(name, 0)
            centroid = self._find_chapter_centroid(ch, layout, chapter_locs)

            if centroid is not None:
                cx, cy = centroid
                # Jitter to avoid exact overlap
                jitter_angle = orphan_idx * 2.4  # golden angle
                jitter_r = 15 + 5 * (orphan_idx % 8)
                x = cx + jitter_r * math.cos(jitter_angle)
                y = cy + jitter_r * math.sin(jitter_angle)
            else:
                # Last resort: interpolate along narrative axis based on chapter
                x, y = self._interpolate_on_axis(ch, name)
                jitter_angle = orphan_idx * 2.4
                jitter_r = 10 + 5 * (orphan_idx % 6)
                x += jitter_r * math.cos(jitter_angle)
                y += jitter_r * math.sin(jitter_angle)

            layout[name] = (
                max(self._canvas_min_x, min(self._canvas_max_x, x)),
                max(self._canvas_min_y, min(self._canvas_max_y, y)),
            )
            orphan_idx += 1

        # Place non-geographic locations in dedicated zones
        self._place_non_geographic(layout)

    def _find_chapter_centroid(
        self,
        chapter: int,
        layout: dict[str, tuple[float, float]],
        chapter_locs: dict[int, list[str]],
    ) -> tuple[float, float] | None:
        """Find the centroid of solved locations from the same or nearby chapters."""
        if chapter <= 0:
            return None

        # Search in expanding window: same chapter, then +/-1, +/-2, etc.
        for window in range(0, 6):
            nearby = []
            for ch in range(chapter - window, chapter + window + 1):
                for loc_name in chapter_locs.get(ch, []):
                    if loc_name in layout:
                        nearby.append(layout[loc_name])
            if nearby:
                cx = sum(p[0] for p in nearby) / len(nearby)
                cy = sum(p[1] for p in nearby) / len(nearby)
                return (cx, cy)
        return None

    def _interpolate_on_axis(self, chapter: int, name: str = "") -> tuple[float, float]:
        """Interpolate position along narrative axis based on chapter number.

        Includes a hash-based perpendicular offset to avoid axis-aligned placement.
        """
        if self._max_chapter <= self._min_chapter or chapter <= 0:
            return (self._canvas_cx, self._canvas_cy)

        t = (chapter - self._min_chapter) / (self._max_chapter - self._min_chapter)
        ax, ay = self._narrative_axis

        # Place along narrative axis line through center
        w = self._canvas_max_x - self._canvas_min_x
        h = self._canvas_max_y - self._canvas_min_y
        cx = self._canvas_cx + (t - 0.5) * w * 0.8 * ax
        cy = self._canvas_cy + (t - 0.5) * h * 0.8 * ay

        # Add hash-based perpendicular offset to scatter across both axes
        if name:
            hv = (hash(name) & 0x7FFFFFFF) % 10000 / 10000.0  # [0, 1)
            perp_offset = (hv - 0.5) * 0.6  # [-0.3, 0.3]
            # Perpendicular direction: (-ay, ax)
            cx += perp_offset * w * (-ay)
            cy += perp_offset * h * ax

        return (cx, cy)

    def _place_non_geographic(self, layout: dict[str, tuple[float, float]]) -> None:
        """Place celestial and underworld locations in dedicated zones."""
        w = self._canvas_max_x - self._canvas_min_x
        # Celestial: top of map
        for i, loc in enumerate(self._celestial):
            name = loc["name"]
            if name in self.user_overrides:
                layout[name] = self.user_overrides[name]
                continue
            x = self._canvas_min_x + (i + 1) * w / (len(self._celestial) + 1)
            y = self._canvas_max_y - 15
            layout[name] = (x, y)

        # Underworld: bottom of map
        for i, loc in enumerate(self._underworld):
            name = loc["name"]
            if name in self.user_overrides:
                layout[name] = self.user_overrides[name]
                continue
            x = self._canvas_min_x + (i + 1) * w / (len(self._underworld) + 1)
            y = self._canvas_min_y + 15
            layout[name] = (x, y)

    def _energy(self, coords_flat: np.ndarray, constraints: list[dict]) -> float:
        """Energy function to minimize."""
        coords = coords_flat.reshape(-1, 2)
        e = 0.0

        for c in constraints:
            si = self.loc_index.get(c["source"])
            ti = self.loc_index.get(c["target"])
            if si is None or ti is None:
                continue

            rtype = c["relation_type"]
            value = c["value"]
            weight = _CONF_RANK.get(c.get("confidence", "medium"), 2)

            if rtype == "direction":
                e += self._e_direction(coords, si, ti, value) * weight
            elif rtype == "distance":
                e += self._e_distance(coords, si, ti, value) * weight
            elif rtype == "contains":
                e += self._e_contains(coords, si, ti) * weight
            elif rtype == "adjacent":
                e += self._e_adjacent(coords, si, ti) * weight
            elif rtype == "separated_by":
                e += self._e_separated(coords, si, ti) * weight
            elif rtype == "in_between":
                # source=A (middle), target=B (endpoint1), value=C name (endpoint2)
                ci = self.loc_index.get(value)
                if ci is not None:
                    e += self._e_in_between(coords, si, ti, ci) * weight

        # Anti-overlap penalty (vectorized)
        e += self._e_overlap(coords)

        # Narrative axis: encourage locations to spread along the travel direction
        # proportional to their chapter appearance order
        e += self._e_narrative_axis(coords) * NARRATIVE_AXIS_WEIGHT

        # Cross-axis scatter: spread locations perpendicular to narrative axis
        e += self._e_cross_axis_scatter(coords) * 0.3

        # Direction hints: weak preference for locations with directional names
        e += self._e_direction_hints(coords) * 0.3

        return e

    def _e_narrative_axis(self, coords: np.ndarray) -> float:
        """Narrative axis energy: locations should spread along the travel direction
        based on their first chapter appearance.

        E.g., for a westward journey (-1,0), locations from early chapters should
        have LOW projection (east/high x gives low -x projection) and locations
        from late chapters should have HIGH projection (west/low x gives high -x).
        """
        if self._max_chapter <= self._min_chapter:
            return 0.0

        ax, ay = self._narrative_axis
        ch_range = self._max_chapter - self._min_chapter

        # Compute projection range for the narrative axis direction.
        corners = [
            self._canvas_min_x * ax + self._canvas_min_y * ay,
            self._canvas_min_x * ax + self._canvas_max_y * ay,
            self._canvas_max_x * ax + self._canvas_min_y * ay,
            self._canvas_max_x * ax + self._canvas_max_y * ay,
        ]
        proj_min = min(corners)
        proj_max = max(corners)
        proj_range = proj_max - proj_min
        if proj_range < 1.0:
            return 0.0

        # Vectorized: compute all projections at once
        projections = coords[:, 0] * ax + coords[:, 1] * ay
        proj_normalized = (projections - proj_min) / proj_range  # [0, 1]

        penalty = 0.0
        n_with_chapter = 0
        for i, name in enumerate(self.loc_names):
            ch = self.first_chapter.get(name, 0)
            if ch <= 0:
                continue
            n_with_chapter += 1

            # Expected normalized position: t=0 for earliest, t=1 for latest
            t = (ch - self._min_chapter) / ch_range

            diff = proj_normalized[i] - t
            penalty += diff ** 2

        # Scale to be competitive with constraint energies.
        # Each constraint violation is ~DIRECTION_MARGIN^2 * weight ≈ 7500.
        # We want narrative energy to be ~20-30% of total constraint energy.
        if n_with_chapter > 0:
            penalty = penalty / n_with_chapter * DIRECTION_MARGIN ** 2 * 20

        return penalty

    def _e_cross_axis_scatter(self, coords: np.ndarray) -> float:
        """Scatter locations perpendicular to the narrative axis.

        Without this, locations collapse to a band along the narrative axis
        because the perpendicular direction has no energy contribution.
        Uses a hash of each location name to compute a deterministic
        pseudo-random expected perpendicular position, creating natural spread.
        """
        ax, ay = self._narrative_axis
        # Perpendicular direction
        px, py = -ay, ax
        perp_magnitude = math.sqrt(px * px + py * py)
        if perp_magnitude < 0.01:
            return 0.0

        # Compute perpendicular range on canvas
        corners_perp = [
            self._canvas_min_x * px + self._canvas_min_y * py,
            self._canvas_min_x * px + self._canvas_max_y * py,
            self._canvas_max_x * px + self._canvas_min_y * py,
            self._canvas_max_x * px + self._canvas_max_y * py,
        ]
        perp_min = min(corners_perp)
        perp_max = max(corners_perp)
        perp_range = perp_max - perp_min
        if perp_range < 1.0:
            return 0.0
        perp_center = (perp_min + perp_max) / 2

        # Perpendicular projections
        perp_proj = coords[:, 0] * px + coords[:, 1] * py

        penalty = 0.0
        count = 0
        for i, name in enumerate(self.loc_names):
            # Skip locations with explicit direction hints to avoid conflict
            if name in self._direction_hints:
                continue
            # Hash-based pseudo-random expected position [-0.4, 0.4] of range
            h = (hash(name) & 0x7FFFFFFF) % 10000 / 10000.0  # [0, 1)
            expected_perp = perp_center + (h - 0.5) * perp_range * 0.7
            diff = perp_proj[i] - expected_perp
            penalty += diff ** 2
            count += 1

        if count > 0:
            penalty = penalty / count * DIRECTION_MARGIN ** 2 * 15

        return penalty

    def _e_direction_hints(self, coords: np.ndarray) -> float:
        """Weak energy term: locations with directional names prefer the expected zone.

        E.g., "东海" prefers the east half of the canvas, "西域" prefers the west half.
        This is a soft hint, not a hard constraint.
        """
        if not self._direction_hints:
            return 0.0

        # Map direction to expected normalized position (0-1)
        # x: 0=west, 1=east; y: 0=south, 1=north
        _HINT_TARGETS: dict[str, tuple[float, float]] = {
            "east": (0.75, 0.5),
            "west": (0.25, 0.5),
            "south": (0.5, 0.25),
            "north": (0.5, 0.75),
            "center": (0.5, 0.5),
        }

        w = self._canvas_max_x - self._canvas_min_x
        h = self._canvas_max_y - self._canvas_min_y
        if w < 1 or h < 1:
            return 0.0

        penalty = 0.0
        count = 0
        for i, name in enumerate(self.loc_names):
            hint = self._direction_hints.get(name)
            if hint is None:
                continue
            target = _HINT_TARGETS.get(hint)
            if target is None:
                continue

            # Normalize current position
            nx = (coords[i, 0] - self._canvas_min_x) / w
            ny = (coords[i, 1] - self._canvas_min_y) / h

            # Only penalize the axis relevant to the hint
            tx, ty = target
            if hint in ("east", "west"):
                penalty += (nx - tx) ** 2
            elif hint in ("north", "south"):
                penalty += (ny - ty) ** 2
            else:
                penalty += (nx - tx) ** 2 + (ny - ty) ** 2
            count += 1

        if count > 0:
            penalty = penalty / count * DIRECTION_MARGIN ** 2 * 5

        return penalty

    def _e_direction(
        self, coords: np.ndarray, si: int, ti: int, value: str
    ) -> float:
        """Direction penalty: source should be in the specified direction from target."""
        vec = _DIRECTION_VECTORS.get(value)
        if vec is None:
            return 0.0

        dx = coords[si, 0] - coords[ti, 0]
        dy = coords[si, 1] - coords[ti, 1]

        penalty = 0.0
        if vec[0] != 0:  # x-axis constraint
            expected_sign = vec[0]
            violation = -expected_sign * dx + DIRECTION_MARGIN
            if violation > 0:
                penalty += violation ** 2
        if vec[1] != 0:  # y-axis constraint
            expected_sign = vec[1]
            violation = -expected_sign * dy + DIRECTION_MARGIN
            if violation > 0:
                penalty += violation ** 2

        return penalty

    def _e_distance(
        self, coords: np.ndarray, si: int, ti: int, value: str
    ) -> float:
        """Distance penalty: actual distance should match parsed target distance."""
        target_dist = parse_distance(value)
        if target_dist <= 0:
            return 0.0
        actual = np.linalg.norm(coords[si] - coords[ti])
        return ((actual - target_dist) / target_dist) ** 2 * 100

    def _e_contains(self, coords: np.ndarray, si: int, ti: int) -> float:
        """Containment penalty: target (child) should be within parent radius."""
        dist = np.linalg.norm(coords[si] - coords[ti])
        violation = max(0.0, dist - PARENT_RADIUS)
        return violation ** 2

    def _e_adjacent(self, coords: np.ndarray, si: int, ti: int) -> float:
        """Adjacency penalty: locations should be relatively close."""
        dist = np.linalg.norm(coords[si] - coords[ti])
        return ((dist - ADJACENT_DIST) / ADJACENT_DIST) ** 2 * 50

    def _e_separated(self, coords: np.ndarray, si: int, ti: int) -> float:
        """Separation penalty: locations should be far enough apart."""
        dist = np.linalg.norm(coords[si] - coords[ti])
        violation = max(0.0, SEPARATION_DIST - dist)
        return violation ** 2

    def _e_in_between(
        self, coords: np.ndarray, ai: int, bi: int, ci: int
    ) -> float:
        """In-between penalty: A should lie near the midpoint of B and C."""
        midpoint = (coords[bi] + coords[ci]) / 2.0
        dist = np.linalg.norm(coords[ai] - midpoint)
        return (dist / max(ADJACENT_DIST, 1.0)) ** 2 * 50

    def _e_overlap(self, coords: np.ndarray) -> float:
        """Anti-overlap: penalize locations that are too close (vectorized)."""
        if self.n < 2:
            return 0.0
        # Pairwise distances via broadcasting
        diff = coords[:, np.newaxis, :] - coords[np.newaxis, :, :]  # (n, n, 2)
        dist = np.sqrt((diff ** 2).sum(axis=2))  # (n, n)
        # Upper triangle only (avoid double-counting and self-distance)
        triu_idx = np.triu_indices(self.n, k=1)
        violations = np.maximum(0.0, self._min_spacing - dist[triu_idx])
        return float(np.sum(violations ** 2))

    def _hierarchy_layout(self) -> dict[str, tuple[float, float]]:
        """Fallback: concentric circle layout based on parent-child hierarchy."""
        layout: dict[str, tuple[float, float]] = {}

        if not self.loc_names:
            return layout

        # Use user overrides first
        for name, (x, y) in self.user_overrides.items():
            if name in self.loc_index:
                layout[name] = (x, y)

        # Place roots in a circle around center
        unplaced_roots = [r for r in self.roots if r not in layout]
        if not unplaced_roots and not layout:
            # No hierarchy at all — place everything in a spiral
            return self._spiral_layout()

        w = self._canvas_max_x - self._canvas_min_x
        h = self._canvas_max_y - self._canvas_min_y
        radius = min(w, h) * 0.2
        for i, name in enumerate(unplaced_roots):
            angle = 2 * math.pi * i / max(len(unplaced_roots), 1)
            x = self._canvas_cx + radius * math.cos(angle)
            y = self._canvas_cy + radius * math.sin(angle)
            layout[name] = (x, y)

        # Place children around their parents
        self._place_children(layout, self.roots, child_radius=radius * 0.5)

        # Place any remaining unplaced locations
        unplaced = [n for n in self.loc_names if n not in layout]
        if unplaced:
            angle_step = 2 * math.pi / max(len(unplaced), 1)
            r = min(w, h) * 0.35
            for i, name in enumerate(unplaced):
                angle = angle_step * i
                layout[name] = (self._canvas_cx + r * math.cos(angle), self._canvas_cy + r * math.sin(angle))

        return layout

    def _place_children(
        self,
        layout: dict[str, tuple[float, float]],
        parents: list[str],
        child_radius: float,
    ) -> None:
        """Recursively place children around their parent positions."""
        for parent in parents:
            children = self.children.get(parent, [])
            if not children:
                continue
            px, py = layout.get(parent, (self._canvas_cx, self._canvas_cy))
            for i, child in enumerate(children):
                if child in layout:
                    continue
                angle = 2 * math.pi * i / len(children)
                cx = px + child_radius * math.cos(angle)
                cy = py + child_radius * math.sin(angle)
                # Clamp to canvas
                cx = max(self._canvas_min_x, min(self._canvas_max_x, cx))
                cy = max(self._canvas_min_y, min(self._canvas_max_y, cy))
                layout[child] = (cx, cy)
            self._place_children(layout, children, child_radius * 0.6)

    def _spiral_layout(self) -> dict[str, tuple[float, float]]:
        """Place all locations in a spiral pattern from center."""
        layout: dict[str, tuple[float, float]] = {}
        for i, name in enumerate(self.loc_names):
            if name in self.user_overrides:
                layout[name] = self.user_overrides[name]
                continue
            angle = i * 2.4  # golden angle
            r = 30 + 15 * math.sqrt(i)
            x = self._canvas_cx + r * math.cos(angle)
            y = self._canvas_cy + r * math.sin(angle)
            layout[name] = (
                max(self._canvas_min_x, min(self._canvas_max_x, x)),
                max(self._canvas_min_y, min(self._canvas_max_y, y)),
            )
        return layout


# ── Terrain Generation ─────────────────────────────

# Biome colors based on location type keywords
_BIOME_COLORS: list[tuple[list[str], tuple[int, int, int]]] = [
    (["山", "峰", "岭", "崖", "岩"], (139, 119, 101)),    # brown mountain
    (["河", "湖", "海", "泉", "潭", "溪", "池"], (70, 130, 180)),  # steel blue water
    (["林", "森", "丛", "木"], (34, 139, 34)),              # forest green
    (["城", "镇", "村", "坊", "集"], (222, 208, 169)),      # sandy settlement
    (["沙", "漠", "荒"], (210, 180, 140)),                   # tan desert
    (["沼", "泽"], (85, 107, 47)),                           # dark olive swamp
]
_DEFAULT_BIOME = (144, 194, 144)  # light green plains


def _biome_for_type(loc_type: str) -> tuple[int, int, int]:
    for keywords, color in _BIOME_COLORS:
        for kw in keywords:
            if kw in loc_type:
                return color
    return _DEFAULT_BIOME


def generate_terrain(
    locations: list[dict],
    layout: dict[str, tuple[float, float]],
    novel_id: str,
    size: int = 1024,
    canvas_size: int = CANVAS_SIZE,
) -> str | None:
    """Generate a terrain PNG based on Voronoi regions + simplex noise.

    Uses fully vectorized numpy operations for performance.
    Returns the file path or None on failure.
    """
    try:
        from PIL import Image
        from opensimplex import OpenSimplex
    except ImportError:
        logger.warning("Pillow or opensimplex not installed, skipping terrain generation")
        return None

    if len(layout) < 2:
        return None

    # Scale layout coordinates from [0, canvas_size] canvas to [0, size] image
    scale = size / canvas_size
    points = []
    biome_colors = []

    for loc in locations:
        name = loc["name"]
        if name not in layout:
            continue
        x, y = layout[name]
        # Flip y: canvas y=0 is bottom, image y=0 is top
        px = x * scale
        py = (canvas_size - y) * scale
        points.append([px, py])
        biome_colors.append(_biome_for_type(loc.get("type", "")))

    if len(points) < 2:
        return None

    point_arr = np.array(points, dtype=np.float64)  # (N, 2)
    biome_arr = np.array(biome_colors, dtype=np.uint8)  # (N, 3)

    # Generate noise field for boundary displacement (vectorized via opensimplex)
    noise_gen = OpenSimplex(seed=hash(novel_id) % (2**31))

    # Create coordinate grids
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float64)

    # Add simplex noise displacement for natural Voronoi boundaries
    # Process in rows for noise2 (opensimplex doesn't have vectorized 2D)
    noise_scale = 0.005
    displacement = np.zeros((size, size), dtype=np.float64)
    for row in range(0, size, 4):  # sample every 4th row, interpolate
        for col in range(0, size, 4):
            displacement[row, col] = noise_gen.noise2(col * noise_scale, row * noise_scale)

    # Bilinear upsample the sparse noise grid
    from scipy.ndimage import zoom
    sparse = displacement[::4, ::4]
    displacement = zoom(sparse, 4, order=1)[:size, :size]

    xs_displaced = xs + displacement * 40
    ys_displaced = ys + displacement * 40

    # Find nearest point for each pixel (vectorized)
    # Process in row-blocks to manage memory (~size * n_points per block)
    block_size = 128
    nearest = np.zeros((size, size), dtype=np.int32)

    for row_start in range(0, size, block_size):
        row_end = min(row_start + block_size, size)
        bx = xs_displaced[row_start:row_end]  # (block, size)
        by = ys_displaced[row_start:row_end]

        # Compute distances to each point: (block, size, n_points)
        dx = bx[:, :, np.newaxis] - point_arr[:, 0]  # broadcast
        dy = by[:, :, np.newaxis] - point_arr[:, 1]
        dist_sq = dx ** 2 + dy ** 2
        nearest[row_start:row_end] = np.argmin(dist_sq, axis=2)

    # Build RGB image from nearest indices
    rgb = biome_arr[nearest]  # (size, size, 3)

    # Add color variation noise
    detail_noise = np.zeros((size, size), dtype=np.float64)
    for row in range(0, size, 2):
        for col in range(0, size, 2):
            detail_noise[row, col] = noise_gen.noise2(col * 0.02, row * 0.02)
    sparse_detail = detail_noise[::2, ::2]
    detail_noise = zoom(sparse_detail, 2, order=1)[:size, :size]

    variation = (detail_noise * 15).astype(np.int16)
    rgb = np.clip(rgb.astype(np.int16) + variation[:, :, np.newaxis], 0, 255).astype(np.uint8)

    # Save
    img = Image.fromarray(rgb, "RGB")
    maps_dir = DATA_DIR / "maps" / novel_id
    maps_dir.mkdir(parents=True, exist_ok=True)
    out_path = maps_dir / "terrain.png"
    img.save(str(out_path), "PNG")
    logger.info("Terrain image saved: %s (%dx%d)", out_path, size, size)
    return str(out_path)


# ── Layout caching helpers ─────────────────────────


# Bump this when solver algorithm changes to invalidate layout cache
_LAYOUT_VERSION = 2

def compute_chapter_hash(
    chapter_start: int, chapter_end: int, canvas_size: int = CANVAS_SIZE,
) -> str:
    """Deterministic hash for a chapter range + canvas size + layout version."""
    key = f"{chapter_start}-{chapter_end}-cs{canvas_size}-v{_LAYOUT_VERSION}"
    return hashlib.md5(key.encode()).hexdigest()[:16]


def layout_to_list(
    layout: dict[str, tuple[float, float]],
    locations: list[dict],
) -> list[dict]:
    """Convert layout dict to API-friendly list with radius info."""
    result = []
    for loc in locations:
        name = loc["name"]
        if name not in layout:
            continue
        x, y = layout[name]
        # Estimate radius based on hierarchy level and mention count
        mention = loc.get("mention_count", 1)
        level = loc.get("level", 0)
        radius = max(15, min(60, 10 + mention * 2 + (3 - level) * 5))
        result.append({
            "name": name,
            "x": round(x, 1),
            "y": round(y, 1),
            "radius": radius,
        })
    return result
