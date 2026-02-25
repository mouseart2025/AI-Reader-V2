"""Map layout engine: constraint-based coordinate solver + terrain generation.

Uses scipy.optimize.differential_evolution to find (x, y) coordinates for each
location that satisfy spatial constraints extracted from the novel text.
Falls back to hierarchical circular layout when constraints are insufficient.

Key features:
- Voronoi region layout: regions are tessellated using Lloyd-relaxed Voronoi
  cells based on cardinal direction seed points.
- Uniform spread energy: repulsion term prevents clustering while allowing
  2D distribution within regions.
- Non-geographic location handling: celestial/underworld locations placed in
  dedicated zones outside the main geographic map area.
- Chapter-proximity placement: remaining locations placed near co-chapter
  neighbors with isotropic circular scatter.
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

# Canvas coordinate range (16:9 aspect ratio)
CANVAS_WIDTH = 1600
CANVAS_HEIGHT = 900
CANVAS_MIN_X = 50
CANVAS_MAX_X = CANVAS_WIDTH - 50
CANVAS_MIN_Y = 50
CANVAS_MAX_Y = CANVAS_HEIGHT - 50

# Spatial scale → canvas size mapping (width, height) — 16:9 ratio
SPATIAL_SCALE_CANVAS: dict[str, tuple[int, int]] = {
    "cosmic": (8000, 4500),
    "continental": (4800, 2700),
    "national": (3200, 1800),
    "urban": (1600, 900),
    "local": (800, 450),
}

# Minimum spacing between any two locations (pixels)
MIN_SPACING = 50

# Direction margin — how far A must exceed B in the expected axis
DIRECTION_MARGIN = 50

# Containment radius for parent locations
PARENT_RADIUS = 120
PARENT_RADIUS_BY_TIER: dict[str, float] = {
    "continent": 300,
    "kingdom": 200,
    "region": 150,
    "city": 100,
    "site": 60,
    "building": 40,
}

# Separation minimum distance
SEPARATION_DIST = 150

# Adjacent target distance
ADJACENT_DIST = 80

# Default distance for unquantified "near" references
DEFAULT_NEAR_DIST = 60
DEFAULT_FAR_DIST = 300

# Confidence priority for conflict resolution
_CONF_RANK = {"high": 3, "medium": 2, "low": 1}

# ── Non-geographic location detection ──────────────
# Keywords that indicate celestial / underworld / metaphysical locations.
_CELESTIAL_KEYWORDS = ("天宫", "天庭", "天门", "天界", "三十三天", "大罗天",
                       "离恨天", "兜率宫", "凌霄殿", "蟠桃园", "瑶池",
                       "灵霄宝殿", "南天门", "北天门", "东天门", "西天门",
                       "九天应元府")
_UNDERWORLD_KEYWORDS = ("地府", "冥界", "幽冥", "阴司", "阴曹", "黄泉",
                        "奈何桥", "阎罗殿", "森罗殿", "枉死城")

# Celestial locations placed in top zone, underworld in bottom zone
_CELESTIAL_Y_RANGE = (CANVAS_MAX_Y - 30, CANVAS_MAX_Y)
_UNDERWORLD_Y_RANGE = (CANVAS_MIN_Y, CANVAS_MIN_Y + 30)

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

# Direction → bounding box zone (x1, y1, x2, y2) on 1600×900 canvas.
# Convention: +x = east (right), +y = north (up).
DIRECTION_ZONES: dict[str, tuple[float, float, float, float]] = {
    "east":   (960, 180, 1550, 720),
    "west":   (50, 180, 640, 720),
    "south":  (320, 50, 1280, 315),
    "north":  (320, 585, 1280, 850),
    "center": (480, 270, 1120, 630),
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


def _compute_region_seeds(
    regions: list[dict],
    canvas_width: int = CANVAS_WIDTH,
    canvas_height: int = CANVAS_HEIGHT,
) -> list[tuple[float, float]]:
    """Compute Voronoi seed points for regions based on cardinal direction hints.

    Each region gets a seed point biased towards its cardinal_direction.
    Multiple regions sharing the same direction are spread within that sector.

    Returns list of (x, y) seed points in the same order as *regions*.
    """
    _DIR_BASE: dict[str, tuple[float, float]] = {
        "east":   (0.75, 0.50),
        "west":   (0.25, 0.50),
        "north":  (0.50, 0.75),
        "south":  (0.50, 0.25),
        "center": (0.50, 0.50),
    }

    margin_x = canvas_width * 0.08
    margin_y = canvas_height * 0.08
    usable_w = canvas_width - 2 * margin_x
    usable_h = canvas_height - 2 * margin_y

    # Group indices by direction
    dir_groups: dict[str, list[int]] = {}
    for i, r in enumerate(regions):
        d = r.get("cardinal_direction") or "center"
        if d not in _DIR_BASE:
            d = "center"
        dir_groups.setdefault(d, []).append(i)

    seeds: list[tuple[float, float]] = [(0.0, 0.0)] * len(regions)

    for direction, indices in dir_groups.items():
        bx, by = _DIR_BASE[direction]
        n = len(indices)
        if n == 1:
            seeds[indices[0]] = (margin_x + bx * usable_w, margin_y + by * usable_h)
        else:
            # Spread seeds in a small arc around the base point
            spread = 0.18  # arc radius in normalized coords
            for k, idx in enumerate(indices):
                angle = 2 * math.pi * k / n
                ox = bx + spread * math.cos(angle)
                oy = by + spread * math.sin(angle)
                # Clamp to [0.05, 0.95] normalized
                ox = max(0.05, min(0.95, ox))
                oy = max(0.05, min(0.95, oy))
                seeds[idx] = (margin_x + ox * usable_w, margin_y + oy * usable_h)

    return seeds


def _lloyd_relax(
    seeds: list[tuple[float, float]],
    canvas_width: int,
    canvas_height: int,
    iterations: int = 2,
) -> list[tuple[float, float]]:
    """Apply Lloyd relaxation to make Voronoi cells more uniform.

    Moves each seed towards the centroid of its Voronoi cell, clipped to canvas.
    """
    if len(seeds) < 2:
        return seeds

    pts = np.array(seeds, dtype=np.float64)
    cw = float(canvas_width)
    ch = float(canvas_height)

    for _ in range(iterations):
        # Mirror points across boundaries for bounded Voronoi
        mirrored = np.vstack([
            pts,
            np.column_stack([-pts[:, 0], pts[:, 1]]),
            np.column_stack([2 * cw - pts[:, 0], pts[:, 1]]),
            np.column_stack([pts[:, 0], -pts[:, 1]]),
            np.column_stack([pts[:, 0], 2 * ch - pts[:, 1]]),
        ])
        vor = Voronoi(mirrored)

        new_pts = pts.copy()
        for i in range(len(pts)):
            region_idx = vor.point_region[i]
            region = vor.regions[region_idx]
            if not region or -1 in region:
                continue
            verts = np.array([vor.vertices[vi] for vi in region])
            # Clip vertices to canvas
            verts[:, 0] = np.clip(verts[:, 0], 0, cw)
            verts[:, 1] = np.clip(verts[:, 1], 0, ch)
            # Compute centroid
            new_pts[i] = verts.mean(axis=0)

        # Clamp to canvas with margin
        margin_x = cw * 0.05
        margin_y = ch * 0.05
        new_pts[:, 0] = np.clip(new_pts[:, 0], margin_x, cw - margin_x)
        new_pts[:, 1] = np.clip(new_pts[:, 1], margin_y, ch - margin_y)
        pts = new_pts

    return [(float(pts[i, 0]), float(pts[i, 1])) for i in range(len(seeds))]


def _layout_regions(
    regions: list[dict],
    canvas_width: int = CANVAS_WIDTH,
    canvas_height: int = CANVAS_HEIGHT,
) -> dict[str, dict]:
    """Compute bounding boxes for world regions using Voronoi tessellation.

    Seeds are placed based on cardinal_direction hints, then Lloyd-relaxed
    for more uniform cell areas.  Each region's bounds come from its
    Voronoi cell's bounding box.

    Args:
        regions: list of dicts with at least "name" and optional "cardinal_direction".
        canvas_width: canvas width.
        canvas_height: canvas height.

    Returns:
        dict mapping region name to {"bounds": (x1, y1, x2, y2), "color": str}.
    """
    if not regions:
        return {}

    # Compute seed points and relax
    seeds = _compute_region_seeds(regions, canvas_width, canvas_height)
    seeds = _lloyd_relax(seeds, canvas_width, canvas_height, iterations=2)

    cw = float(canvas_width)
    ch = float(canvas_height)
    margin_x = canvas_width * 0.05
    margin_y = canvas_height * 0.05

    if len(regions) == 1:
        # Single region → full canvas
        direction = regions[0].get("cardinal_direction") or "center"
        color = _REGION_COLORS.get(direction, _REGION_COLOR_FALLBACK)
        return {
            regions[0]["name"]: {
                "bounds": (margin_x, margin_y, cw - margin_x, ch - margin_y),
                "color": color,
            }
        }

    # Build Voronoi with mirror points
    pts = np.array(seeds, dtype=np.float64)
    n_orig = len(pts)
    mirrored = np.vstack([
        pts,
        np.column_stack([-pts[:, 0], pts[:, 1]]),
        np.column_stack([2 * cw - pts[:, 0], pts[:, 1]]),
        np.column_stack([pts[:, 0], -pts[:, 1]]),
        np.column_stack([pts[:, 0], 2 * ch - pts[:, 1]]),
    ])
    vor = Voronoi(mirrored)

    result: dict[str, dict] = {}
    for i, r in enumerate(regions):
        direction = r.get("cardinal_direction") or "center"
        color = _REGION_COLORS.get(direction, _REGION_COLOR_FALLBACK)

        region_idx = vor.point_region[i]
        region = vor.regions[region_idx]

        if not region or -1 in region:
            # Fallback: box around seed
            sx, sy = seeds[i]
            half_w = cw * 0.15
            half_h = ch * 0.15
            result[r["name"]] = {
                "bounds": (
                    max(margin_x, sx - half_w),
                    max(margin_y, sy - half_h),
                    min(cw - margin_x, sx + half_w),
                    min(ch - margin_y, sy + half_h),
                ),
                "color": color,
            }
            continue

        verts = np.array([vor.vertices[vi] for vi in region])
        # Clip to canvas
        verts[:, 0] = np.clip(verts[:, 0], 0, cw)
        verts[:, 1] = np.clip(verts[:, 1], 0, ch)
        x1, y1 = float(verts[:, 0].min()), float(verts[:, 1].min())
        x2, y2 = float(verts[:, 0].max()), float(verts[:, 1].max())

        # Ensure minimum size
        min_size_x = cw * 0.08
        min_size_y = ch * 0.08
        if x2 - x1 < min_size_x:
            cx = (x1 + x2) / 2
            x1, x2 = cx - min_size_x / 2, cx + min_size_x / 2
        if y2 - y1 < min_size_y:
            cy = (y1 + y2) / 2
            y1, y2 = cy - min_size_y / 2, cy + min_size_y / 2

        result[r["name"]] = {
            "bounds": (round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)),
            "color": color,
        }

    return result


# ── Voronoi boundary generation ──────────────────


def _clip_polygon_to_canvas(
    polygon: list[tuple[float, float]],
    canvas_width: int = CANVAS_WIDTH,
    canvas_height: int = CANVAS_HEIGHT,
) -> list[tuple[float, float]]:
    """Clip a polygon to the [0, canvas_width] x [0, canvas_height] rectangle using Sutherland-Hodgman."""

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

    cw = float(canvas_width)
    ch = float(canvas_height)
    # Proper CCW clip rectangle edges
    clip_edges = [
        ((0.0, 0.0), (cw, 0.0)),    # bottom: left→right
        ((cw, 0.0), (cw, ch)),      # right: bottom→top
        ((cw, ch), (0.0, ch)),      # top: right→left
        ((0.0, ch), (0.0, 0.0)),    # left: top→bottom
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


def _distort_polygon_edges(
    polygon: list[tuple[float, float]],
    canvas_width: int = CANVAS_WIDTH,
    canvas_height: int = CANVAS_HEIGHT,
    num_segments: int = 16,
    seed: int = 0,
) -> list[tuple[float, float]]:
    """Apply simplex noise distortion to polygon edges for a hand-drawn look.

    Each edge is subdivided into `num_segments` segments.  Intermediate points
    are displaced perpendicular to the edge by an amount controlled by simplex
    noise.  The displacement tapers to zero at vertices via sin(t*pi) so that
    adjacent polygons sharing an edge produce identical distortions (no gaps).

    The noise anchor is derived from the canonical (lexicographically sorted)
    edge midpoint, ensuring two polygons that share an edge get the same curve.
    """
    from opensimplex import OpenSimplex

    if len(polygon) < 3:
        return polygon

    amplitude = min(canvas_width, canvas_height) * 0.01
    noise_gen = OpenSimplex(seed=seed)

    result: list[tuple[float, float]] = []
    n = len(polygon)

    for i in range(n):
        p0 = polygon[i]
        p1 = polygon[(i + 1) % n]

        # Canonical edge key: sort endpoints lexicographically so both
        # adjacent polygons use the same noise anchor for this edge.
        canonical = (p0[0], p0[1]) <= (p1[0], p1[1])
        anchor_x = (p0[0] + p1[0]) / 2
        anchor_y = (p0[1] + p1[1]) / 2
        # Direction sign: compensates for the perpendicular vector flipping
        # when the edge is traversed in non-canonical order.
        direction = 1.0 if canonical else -1.0

        # Edge direction and perpendicular
        ex = p1[0] - p0[0]
        ey = p1[1] - p0[1]
        edge_len = math.sqrt(ex * ex + ey * ey)
        if edge_len < 1e-6:
            result.append(p0)
            continue
        # Unit perpendicular (rotated 90 degrees CCW)
        nx = -ey / edge_len
        ny = ex / edge_len

        # Add the start vertex (no displacement)
        result.append(p0)

        # Subdivide and displace intermediate points
        for seg in range(1, num_segments):
            t = seg / num_segments
            # Linear interpolation along edge
            ix = p0[0] + t * ex
            iy = p0[1] + t * ey

            # sin(t*pi) envelope: zero at endpoints, max at midpoint
            envelope = math.sin(t * math.pi)

            # Use canonical t for noise sampling so both polygons sharing
            # this edge sample the same noise values at each physical point.
            # When traversing in non-canonical direction, t maps to (1-t).
            canonical_t = t if canonical else (1.0 - t)

            # Noise input: use anchor + canonical parameter for deterministic curve
            noise_val = noise_gen.noise2(
                anchor_x * 0.05 + canonical_t * 3.0,
                anchor_y * 0.05,
            )
            # direction compensates for perpendicular flip in non-canonical
            # traversal, so the physical displacement is identical.
            displacement = noise_val * amplitude * envelope * direction

            result.append((ix + nx * displacement, iy + ny * displacement))

    return result


def generate_voronoi_boundaries(
    region_layout: dict[str, dict],
    canvas_width: int = CANVAS_WIDTH,
    canvas_height: int = CANVAS_HEIGHT,
) -> dict[str, dict]:
    """Generate Voronoi polygon boundaries from region layout centers.

    Args:
        region_layout: Output of _layout_regions(), mapping name → {"bounds", "color"}.
        canvas_width: Canvas width.
        canvas_height: Canvas height.

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
    cw = float(canvas_width)
    ch = float(canvas_height)
    n_orig = len(points)

    # Add 4 mirror points per seed, reflected across canvas boundaries
    for cx, cy in centers:
        points.append((-cx, cy))             # mirror across left edge
        points.append((2 * cw - cx, cy))     # mirror across right edge
        points.append((cx, -cy))             # mirror across bottom edge
        points.append((cx, 2 * ch - cy))     # mirror across top edge

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
        clipped = _clip_polygon_to_canvas(verts, canvas_width, canvas_height)
        if len(clipped) < 3:
            # Degenerate — fallback to rectangle
            rd = region_layout[name]
            x1, y1, x2, y2 = rd["bounds"]
            clipped = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]

        # Distort edges for hand-drawn look.
        # Use a fixed seed (not per-region) so adjacent polygons sharing
        # an edge produce identical distortions with no gaps.
        clipped = _distort_polygon_edges(
            clipped,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            seed=42,
        )

        # Round coordinates
        clipped = [(round(x, 1), round(y, 1)) for x, y in clipped]

        result[name] = {
            "polygon": clipped,
            "center": (round(centers[i][0], 1), round(centers[i][1], 1)),
            "color": colors[i],
        }

    return result


# ── Layered layout engine (Story 7.7) ─────────────


# Canvas sizes for non-overworld layers (width, height) — 16:9 ratio
_LAYER_CANVAS_SIZES: dict[str, tuple[int, int]] = {
    "pocket": (480, 270),
    "sky": (960, 540),
    "underground": (960, 540),
    "sea": (960, 540),
    "spirit": (640, 360),
}


def _distribute_in_bounds(
    locations: list[dict],
    bounds: tuple[float, float, float, float],
    user_overrides: dict[str, tuple[float, float]] | None = None,
) -> dict[str, tuple[float, float]]:
    """Place a small number of locations evenly within bounds without a solver.

    For 1 location: center. For 2-3: spread along the diagonal.
    User overrides take priority.
    """
    x1, y1, x2, y2 = bounds
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    overrides = user_overrides or {}
    result: dict[str, tuple[float, float]] = {}

    for i, loc in enumerate(locations):
        name = loc["name"]
        if name in overrides:
            result[name] = overrides[name]
            continue
        n = len(locations)
        if n == 1:
            result[name] = (cx, cy)
        else:
            t = i / (n - 1)  # 0.0 → 1.0
            margin = min((x2 - x1), (y2 - y1)) * 0.15
            result[name] = (
                x1 + margin + t * (x2 - x1 - 2 * margin),
                y1 + margin + t * (y2 - y1 - 2 * margin),
            )
    return result


# Maximum number of regions to solve individually; beyond this, merge the rest
MAX_SOLVER_REGIONS = 30


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

    # Fast path: few locations → distribute without solver overhead
    if len(locations) <= 3:
        return _distribute_in_bounds(locations, region_bounds, user_overrides)

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

    layer_cw, layer_ch = _LAYER_CANVAS_SIZES.get(layer_type, (640, 360))
    margin = max(10, min(layer_cw, layer_ch) // 20)
    bounds = (margin, margin, layer_cw - margin, layer_ch - margin)

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
    canvas_w, canvas_h = SPATIAL_SCALE_CANVAS.get(
        spatial_scale or "", (CANVAS_WIDTH, CANVAS_HEIGHT)
    )
    margin_x = max(50, canvas_w // 20)
    margin_y = max(50, canvas_h // 20)
    overworld_bounds = (margin_x, margin_y, canvas_w - margin_x, canvas_h - margin_y)

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
                    canvas_width=canvas_w,
                    canvas_height=canvas_h,
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
    canvas_width: int = CANVAS_WIDTH,
    canvas_height: int = CANVAS_HEIGHT,
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
    region_layout = _layout_regions(region_dicts, canvas_width=canvas_width, canvas_height=canvas_height)

    # Partition locations by region
    region_locs: dict[str, list[dict]] = {r["name"]: [] for r in region_dicts}
    unassigned_locs: list[dict] = []

    for loc in locations:
        region_name = location_region_map.get(loc["name"])
        if region_name and region_name in region_locs:
            region_locs[region_name].append(loc)
        else:
            unassigned_locs.append(loc)

    # Count non-empty regions
    non_empty = {rn: locs for rn, locs in region_locs.items() if locs}
    non_empty_count = len(non_empty)

    merged_layout: dict[str, tuple[float, float]] = {}
    margin_x = max(50, canvas_width // 20)
    margin_y = max(50, canvas_height // 20)
    fallback_bounds = (margin_x, margin_y, canvas_width - margin_x, canvas_height - margin_y)

    if non_empty_count > MAX_SOLVER_REGIONS:
        # ── Many regions: use a SINGLE global solver with per-location region bounds ──
        # This avoids creating hundreds of ConstraintSolver instances (each with
        # scipy differential_evolution overhead).
        all_locs = locations  # all locations go into one solver
        loc_region_bounds: dict[str, tuple[float, float, float, float]] = {}
        for loc in all_locs:
            rn = location_region_map.get(loc["name"])
            if rn and rn in region_layout:
                loc_region_bounds[loc["name"]] = region_layout[rn]["bounds"]

        logger.info(
            "Using global solver for %d locations across %d regions "
            "(exceeds MAX_SOLVER_REGIONS=%d); %d have region bounds",
            len(all_locs), non_empty_count, MAX_SOLVER_REGIONS,
            len(loc_region_bounds),
        )

        solver = ConstraintSolver(
            all_locs, constraints,
            user_overrides=user_overrides,
            first_chapter=first_chapter,
            location_region_bounds=loc_region_bounds,
            canvas_bounds=fallback_bounds,
        )
        coords, _ = solver.solve()
        merged_layout.update(coords)
    else:
        # ── Few regions: solve per-region for better quality ──
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

        # Solve unassigned locations with the full canvas
        if unassigned_locs:
            loc_region_bounds_ua: dict[str, tuple[float, float, float, float]] = {}
            for loc in unassigned_locs:
                rn = location_region_map.get(loc["name"])
                if rn and rn in region_layout:
                    loc_region_bounds_ua[loc["name"]] = region_layout[rn]["bounds"]

            solver = ConstraintSolver(
                unassigned_locs, constraints,
                user_overrides=user_overrides,
                first_chapter=first_chapter,
                location_region_bounds=loc_region_bounds_ua,
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
MAX_SOLVER_LOCATIONS = 40


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
            self._canvas_min_x = CANVAS_MIN_X
            self._canvas_min_y = CANVAS_MIN_Y
            self._canvas_max_x = CANVAS_MAX_X
            self._canvas_max_y = CANVAS_MAX_Y

        # Convenience canvas helpers
        self._canvas_cx = (self._canvas_min_x + self._canvas_max_x) / 2
        self._canvas_cy = (self._canvas_min_y + self._canvas_max_y) / 2

        # Dynamic min spacing proportional to canvas size
        canvas_w = self._canvas_max_x - self._canvas_min_x
        self._min_spacing = max(MIN_SPACING, canvas_w * 0.02)

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

        # Pre-compute chapter array for vectorized energy functions
        self._chapter_arr = np.array(
            [self.first_chapter.get(n, 0) for n in self.loc_names],
            dtype=np.float64,
        )

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
        # With 40 locations (80 params), keep budget tight for responsiveness
        maxiter = max(50, min(200, 2000 // max(self.n, 1)))
        popsize = max(4, min(8, 200 // max(self.n, 1)))

        # Generate force-directed seed population
        seed_population = self._force_directed_seed(bounds, valid_constraints, popsize)
        seed_energy = self._energy(seed_population[0], valid_constraints)
        random_energy = self._energy(seed_population[1], valid_constraints) if popsize > 1 else float("inf")
        logger.info(
            "Force-directed seed energy=%.2f, random sample energy=%.2f",
            seed_energy, random_energy,
        )

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
                init=seed_population,
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
           and place near their centroid with isotropic circular scatter.
        4. Last resort: random position within canvas bounds using name hash.
        """
        # Build chapter->solved_locations lookup for proximity placement
        chapter_locs: dict[int, list[str]] = {}
        for name in layout:
            ch = self.first_chapter.get(name, 0)
            if ch > 0:
                chapter_locs.setdefault(ch, []).append(name)

        # Scale jitter radius with canvas size
        canvas_w = self._canvas_max_x - self._canvas_min_x
        canvas_h = self._canvas_max_y - self._canvas_min_y
        base_jitter = max(30, min(canvas_w, canvas_h) * 0.04)

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
                n_children = max(len(children_here), 1)
                # Sunflower seed distribution: golden angle + varying radius
                # fills the circular area organically instead of a ring perimeter
                golden_angle = math.pi * (3 - math.sqrt(5))  # ≈ 137.5°
                frac = (idx + 0.5) / n_children  # 0..1
                r = base_jitter * (0.3 + 0.7 * math.sqrt(frac)) + 8 * loc.get("level", 0)
                angle = idx * golden_angle
                x = max(self._canvas_min_x, min(self._canvas_max_x, px + r * math.cos(angle)))
                y = max(self._canvas_min_y, min(self._canvas_max_y, py + r * math.sin(angle)))
                layout[name] = (x, y)
                continue

            # Chapter-proximity: find solved locations from same or nearby chapters
            ch = self.first_chapter.get(name, 0)
            centroid = self._find_chapter_centroid(ch, layout, chapter_locs)

            if centroid is not None:
                cx, cy = centroid
                # Isotropic circular scatter: golden angle + varying radius
                jitter_angle = orphan_idx * 2.4  # golden angle ≈ 137.5°
                jitter_r = base_jitter + base_jitter * 0.3 * (orphan_idx % 8)
                x = cx + jitter_r * math.cos(jitter_angle)
                y = cy + jitter_r * math.sin(jitter_angle)
            else:
                # Last resort: hash-based position within canvas bounds
                hv = (hash(name) & 0x7FFFFFFF) % 10000 / 10000.0
                hv2 = (hash(name + "_y") & 0x7FFFFFFF) % 10000 / 10000.0
                x = self._canvas_min_x + canvas_w * 0.1 + hv * canvas_w * 0.8
                y = self._canvas_min_y + canvas_h * 0.1 + hv2 * canvas_h * 0.8
                # Small golden-angle jitter to avoid exact overlap with other hash-placed
                jitter_angle = orphan_idx * 2.4
                jitter_r = base_jitter * 0.3
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

        # Uniform spread: repulsion to prevent clustering
        e += self._e_uniform_spread(coords) * 0.5

        # Narrative order: weak tie-breaker for chapter proximity
        e += self._e_narrative_order(coords) * 0.1

        # Direction hints: weak preference for locations with directional names
        e += self._e_direction_hints(coords) * 0.3

        return e

    def _e_uniform_spread(self, coords: np.ndarray) -> float:
        """Uniform spread repulsion: penalize locations closer than the ideal spacing.

        Computes an ideal spacing from the canvas area and number of locations,
        then applies a smooth quadratic penalty for pairs closer than that.
        Longer range and smoother falloff than _e_overlap.
        """
        if self.n < 2:
            return 0.0

        area = (self._canvas_max_x - self._canvas_min_x) * (self._canvas_max_y - self._canvas_min_y)
        ideal = math.sqrt(area / max(self.n, 1)) * 0.8

        # Pairwise distances
        diff = coords[:, np.newaxis, :] - coords[np.newaxis, :, :]
        dist = np.sqrt((diff ** 2).sum(axis=2))

        triu_idx = np.triu_indices(self.n, k=1)
        pairwise = dist[triu_idx]

        # Smooth repulsion: (1 - d/ideal)^2 when d < ideal
        violations = np.maximum(0.0, 1.0 - pairwise / ideal)
        return float(np.sum(violations ** 2)) * DIRECTION_MARGIN ** 2 * 2

    def _e_narrative_order(self, coords: np.ndarray) -> float:
        """Weak narrative order energy: tie-breaker using Euclidean distance (vectorized).

        - Locations appearing in nearby chapters (gap < 5) but placed far apart
          get a light penalty.
        - Locations appearing in distant chapters (gap > total/2) but placed
          very close together get a light penalty.
        """
        if self._max_chapter <= self._min_chapter or self.n < 2:
            return 0.0

        ch_range = self._max_chapter - self._min_chapter
        half_range = ch_range / 2

        canvas_diag = math.sqrt(
            (self._canvas_max_x - self._canvas_min_x) ** 2
            + (self._canvas_max_y - self._canvas_min_y) ** 2
        )
        if canvas_diag < 1.0:
            return 0.0

        # Vectorized pairwise computation
        ch = self._chapter_arr  # pre-computed in __init__
        triu_i, triu_j = np.triu_indices(self.n, k=1)

        # Filter pairs where both have valid chapters
        valid = (ch[triu_i] > 0) & (ch[triu_j] > 0)
        if not np.any(valid):
            return 0.0

        vi, vj = triu_i[valid], triu_j[valid]
        ch_gaps = np.abs(ch[vi] - ch[vj])

        diff = coords[vi] - coords[vj]
        dists = np.sqrt((diff ** 2).sum(axis=1))
        norm_dists = dists / canvas_diag

        # Nearby chapters but far apart
        near_mask = (ch_gaps < 5) & (norm_dists > 0.5)
        penalty_near = np.sum((norm_dists[near_mask] - 0.5) ** 2)

        # Distant chapters but very close
        far_mask = (ch_gaps > half_range) & (norm_dists < 0.1)
        penalty_far = np.sum((0.1 - norm_dists[far_mask]) ** 2)

        count = int(np.sum(near_mask) + np.sum(far_mask))
        if count == 0:
            return 0.0

        return float(penalty_near + penalty_far) / count * DIRECTION_MARGIN ** 2 * 5

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

    def _get_parent_radius(self, si: int) -> float:
        """Get containment radius based on the parent (source) location's tier."""
        tier = self.locations[si].get("tier", "city") if si < len(self.locations) else "city"
        return PARENT_RADIUS_BY_TIER.get(tier, PARENT_RADIUS)

    def _e_contains(self, coords: np.ndarray, si: int, ti: int) -> float:
        """Containment penalty: target (child) should be within parent radius."""
        dist = np.linalg.norm(coords[si] - coords[ti])
        radius = self._get_parent_radius(si)
        violation = max(0.0, dist - radius)
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

        # Place roots using sunflower seed distribution
        unplaced_roots = [r for r in self.roots if r not in layout]
        if not unplaced_roots and not layout:
            # No hierarchy at all — place everything in a spiral
            return self._spiral_layout()

        w = self._canvas_max_x - self._canvas_min_x
        h = self._canvas_max_y - self._canvas_min_y
        radius = min(w, h) * 0.2
        golden_angle = math.pi * (3 - math.sqrt(5))  # ≈ 137.5°
        n_roots = max(len(unplaced_roots), 1)
        for i, name in enumerate(unplaced_roots):
            frac = (i + 0.5) / n_roots
            r = radius * (0.3 + 0.7 * math.sqrt(frac))
            angle = i * golden_angle
            x = self._canvas_cx + r * math.cos(angle)
            y = self._canvas_cy + r * math.sin(angle)
            layout[name] = (x, y)

        # Place children around their parents
        self._place_children(layout, self.roots, child_radius=radius * 0.5)

        # Place any remaining unplaced locations
        unplaced = [n for n in self.loc_names if n not in layout]
        if unplaced:
            r = min(w, h) * 0.35
            n_unplaced = max(len(unplaced), 1)
            for i, name in enumerate(unplaced):
                frac = (i + 0.5) / n_unplaced
                ri = r * (0.3 + 0.7 * math.sqrt(frac))
                angle = i * golden_angle
                layout[name] = (self._canvas_cx + ri * math.cos(angle), self._canvas_cy + ri * math.sin(angle))

        return layout

    def _place_children(
        self,
        layout: dict[str, tuple[float, float]],
        parents: list[str],
        child_radius: float,
    ) -> None:
        """Recursively place children around their parent positions."""
        golden_angle = math.pi * (3 - math.sqrt(5))  # ≈ 137.5°
        for parent in parents:
            children = self.children.get(parent, [])
            if not children:
                continue
            px, py = layout.get(parent, (self._canvas_cx, self._canvas_cy))
            n = len(children)
            for i, child in enumerate(children):
                if child in layout:
                    continue
                # Sunflower seed distribution: fills circle area organically
                frac = (i + 0.5) / n
                r = child_radius * (0.3 + 0.7 * math.sqrt(frac))
                angle = i * golden_angle
                cx = px + r * math.cos(angle)
                cy = py + r * math.sin(angle)
                # Clamp to canvas
                cx = max(self._canvas_min_x, min(self._canvas_max_x, cx))
                cy = max(self._canvas_min_y, min(self._canvas_max_y, cy))
                layout[child] = (cx, cy)
            self._place_children(layout, children, child_radius * 0.6)

    def _force_directed_seed(
        self,
        bounds: list[tuple[float, float]],
        constraints: list[dict],
        popsize: int,
    ) -> np.ndarray:
        """Generate an initial population for DE using force-directed simulation.

        Returns ndarray of shape (popsize, 2*n):
        - Row 0: force-directed result (physics-simulated positions)
        - Rows 1..popsize-1: random positions (to maintain DE diversity)
        """
        n = self.n
        dim = 2 * n

        # Start from hierarchy layout positions
        hierarchy = self._hierarchy_layout()
        positions = np.zeros((n, 2), dtype=np.float64)
        for i, name in enumerate(self.loc_names):
            if name in hierarchy:
                positions[i] = hierarchy[name]
            else:
                # Center of bounds for this location
                positions[i, 0] = (bounds[2 * i][0] + bounds[2 * i][1]) / 2
                positions[i, 1] = (bounds[2 * i + 1][0] + bounds[2 * i + 1][1]) / 2

        # Identify fixed locations (user overrides)
        fixed = np.array(
            [name in self.user_overrides for name in self.loc_names],
            dtype=bool,
        )

        # Compute ideal spacing for repulsion
        area = (self._canvas_max_x - self._canvas_min_x) * (
            self._canvas_max_y - self._canvas_min_y
        )
        ideal_spacing = math.sqrt(area / max(n, 1)) * 0.8

        # Pre-parse constraint pairs with direction vectors
        parsed_constraints: list[tuple[int, int, str, str, float]] = []
        for c in constraints:
            si = self.loc_index.get(c["source"])
            ti = self.loc_index.get(c["target"])
            if si is None or ti is None:
                continue
            weight = _CONF_RANK.get(c.get("confidence", "medium"), 2)
            parsed_constraints.append(
                (si, ti, c["relation_type"], c.get("value", ""), weight)
            )

        # Run 80 iterations of spring-force simulation
        velocities = np.zeros_like(positions)
        damping = 0.85
        dt = 1.0

        for _ in range(80):
            forces = np.zeros_like(positions)

            # ── Attraction: constraints pull locations toward satisfaction ──
            for si, ti, rtype, value, weight in parsed_constraints:
                diff = positions[ti] - positions[si]
                dist = np.linalg.norm(diff)
                if dist < 1e-6:
                    continue
                direction = diff / dist

                if rtype == "contains":
                    # Pull child toward parent if too far
                    radius = self._get_parent_radius(si)
                    if dist > radius:
                        force_mag = (dist - radius) * 0.1 * weight
                        forces[ti] -= direction * force_mag
                        if not fixed[si]:
                            forces[si] += direction * force_mag * 0.3
                elif rtype == "adjacent":
                    # Pull toward ADJACENT_DIST
                    force_mag = (dist - ADJACENT_DIST) * 0.05 * weight
                    forces[si] += direction * force_mag
                    forces[ti] -= direction * force_mag
                elif rtype == "direction":
                    vec = _DIRECTION_VECTORS.get(value)
                    if vec is not None:
                        # Nudge source in expected direction relative to target
                        target_offset = np.array([
                            vec[0] * DIRECTION_MARGIN * 2,
                            vec[1] * DIRECTION_MARGIN * 2,
                        ], dtype=np.float64)
                        desired = positions[ti] + target_offset
                        force = (desired - positions[si]) * 0.03 * weight
                        forces[si] += force
                elif rtype == "separated_by":
                    if dist < SEPARATION_DIST:
                        force_mag = (SEPARATION_DIST - dist) * 0.1 * weight
                        forces[si] -= direction * force_mag
                        forces[ti] += direction * force_mag

            # ── Repulsion: O(n²) pairwise repulsion ──
            for i in range(n):
                for j in range(i + 1, n):
                    diff = positions[j] - positions[i]
                    dist = np.linalg.norm(diff)
                    if dist < 1e-6:
                        dist = 1e-6
                        diff = np.random.randn(2) * 1e-6
                    if dist < ideal_spacing:
                        repulsion = ((ideal_spacing - dist) / ideal_spacing) ** 2
                        force_mag = repulsion * ideal_spacing * 0.1
                        direction = diff / dist
                        forces[i] -= direction * force_mag
                        forces[j] += direction * force_mag

            # Zero out forces on fixed locations
            forces[fixed] = 0.0

            # Update velocities and positions
            velocities = (velocities + forces * dt) * damping
            positions += velocities * dt

            # Boundary clamping
            for i in range(n):
                positions[i, 0] = np.clip(
                    positions[i, 0], bounds[2 * i][0], bounds[2 * i][1]
                )
                positions[i, 1] = np.clip(
                    positions[i, 1], bounds[2 * i + 1][0], bounds[2 * i + 1][1]
                )

        # Build seed population: row 0 = force-directed, rest = random
        seed = np.empty((popsize, dim), dtype=np.float64)
        seed[0] = positions.flatten()

        # Fill remaining rows with random positions within bounds
        rng = np.random.RandomState(42)
        bounds_arr = np.array(bounds)  # (2*n, 2)
        lows = bounds_arr[:, 0]
        highs = bounds_arr[:, 1]
        for row in range(1, popsize):
            seed[row] = lows + rng.random(dim) * (highs - lows)

        return seed

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
    (["山", "峰", "岭", "崖", "岩"], (160, 140, 120)),    # warm stone brown
    (["河", "湖", "海", "泉", "潭", "溪", "池"], (140, 165, 175)),  # pale blue-gray water
    (["林", "森", "丛", "木"], (120, 145, 110)),            # dark olive green
    (["城", "镇", "村", "坊", "集"], (195, 180, 155)),      # pale parchment
    (["沙", "漠", "荒"], (185, 168, 140)),                   # dust sand
    (["沼", "泽"], (110, 125, 100)),                         # dark moss green
]
_DEFAULT_BIOME = (170, 180, 150)  # pale grey-green plains


def _biome_for_type(loc_type: str) -> tuple[int, int, int]:
    for keywords, color in _BIOME_COLORS:
        for kw in keywords:
            if kw in loc_type:
                return color
    return _DEFAULT_BIOME


# ── Whittaker biome matrix (elevation × moisture → color) ────────────
# 5×5 grid, rows = elevation (0.0 → 1.0), cols = moisture (0.0 → 1.0)
_WHITTAKER_GRID: list[list[tuple[int, int, int]]] = [
    # e=0.0  (lowland)
    [(170, 165, 130), (150, 160, 123), (130, 155, 115), (115, 138, 105), (100, 120, 95)],
    # e=0.25
    [(185, 170, 138), (155, 163, 125), (135, 158, 118), (118, 142, 108), (105, 125, 98)],
    # e=0.5
    [(190, 170, 140), (168, 168, 130), (145, 165, 120), (128, 153, 110), (110, 140, 100)],
    # e=0.75
    [(165, 145, 125), (130, 133, 108), (112, 127, 99), (95, 120, 90), (95, 120, 90)],
    # e=1.0  (peak)
    [(250, 248, 245), (250, 248, 245), (250, 248, 245), (250, 248, 245), (250, 248, 245)],
]


def _biome_color_at(elevation: float, moisture: float) -> tuple[int, int, int]:
    """Whittaker matrix lookup with bilinear interpolation for smooth biome transitions."""
    e = max(0.0, min(1.0, elevation)) * 4  # scale to grid range 0-4
    m = max(0.0, min(1.0, moisture)) * 4
    ei = min(3, int(e))
    mi = min(3, int(m))
    ef = e - ei  # fractional part
    mf = m - mi
    c00 = _WHITTAKER_GRID[ei][mi]
    c01 = _WHITTAKER_GRID[ei][mi + 1]
    c10 = _WHITTAKER_GRID[ei + 1][mi]
    c11 = _WHITTAKER_GRID[ei + 1][mi + 1]
    r = int(c00[0] * (1 - ef) * (1 - mf) + c01[0] * (1 - ef) * mf +
            c10[0] * ef * (1 - mf) + c11[0] * ef * mf)
    g = int(c00[1] * (1 - ef) * (1 - mf) + c01[1] * (1 - ef) * mf +
            c10[1] * ef * (1 - mf) + c11[1] * ef * mf)
    b = int(c00[2] * (1 - ef) * (1 - mf) + c01[2] * (1 - ef) * mf +
            c10[2] * ef * (1 - mf) + c11[2] * ef * mf)
    return (r, g, b)


def _elevation_at_img(
    px: float, py: float, noise_gen, img_w: int, img_h: int,
    mountain_pts: list[tuple[float, float]],
    water_pts: list[tuple[float, float]],
) -> float:
    """Compute elevation at image-space coordinates. Returns 0-1."""
    nx, ny = px / img_w, py / img_h
    e = noise_gen.noise2(nx * 3, ny * 3) * 0.5 + 0.5
    e += noise_gen.noise2(nx * 7, ny * 7) * 0.15
    radius = max(img_w, img_h) * 0.15
    for mx, my in mountain_pts:
        d = math.hypot(px - mx, py - my)
        if d < radius:
            e += 0.25 * (1 - d / radius)
    for wx, wy in water_pts:
        d = math.hypot(px - wx, py - wy)
        if d < radius:
            e -= 0.2 * (1 - d / radius)
    return max(0.0, min(1.0, e))


def _moisture_at_img(
    px: float, py: float, moisture_gen, img_w: int, img_h: int,
    mountain_pts: list[tuple[float, float]],
    water_pts: list[tuple[float, float]],
) -> float:
    """Compute moisture at image-space coordinates. Returns 0-1."""
    nx, ny = px / img_w, py / img_h
    m = moisture_gen.noise2(nx * 3, ny * 3) * 0.5 + 0.5
    m += moisture_gen.noise2(nx * 6, ny * 6) * 0.15
    w_radius = max(img_w, img_h) * 0.15
    for wx, wy in water_pts:
        d = math.hypot(px - wx, py - wy)
        if d < w_radius:
            m += 0.3 * (1 - d / w_radius)
    m_radius = max(img_w, img_h) * 0.12
    for mx, my in mountain_pts:
        d = math.hypot(px - mx, py - my)
        if d < m_radius:
            m -= 0.15 * (1 - d / m_radius)
    return max(0.0, min(1.0, m))


def _lloyd_relax(
    points: np.ndarray, w: int, h: int,
    n_fixed: int = 0, iterations: int = 2, max_shift: float = 30.0,
) -> np.ndarray:
    """Lloyd relaxation. First n_fixed points are clamped to ±max_shift total movement."""
    pts = points.copy()
    original_fixed = points[:n_fixed].copy()
    for _ in range(iterations):
        vor = Voronoi(pts)
        for i, region_idx in enumerate(vor.point_region):
            region = vor.regions[region_idx]
            if -1 in region or len(region) == 0:
                continue
            verts = vor.vertices[region]
            centroid = verts.mean(axis=0)
            if i < n_fixed:
                total_delta = centroid - original_fixed[i]
                total_delta = np.clip(total_delta, -max_shift, max_shift)
                pts[i] = original_fixed[i] + total_delta
            else:
                pts[i] = centroid
    return np.clip(pts, [10, 10], [w - 10, h - 10])


def generate_terrain(
    locations: list[dict],
    layout: dict[str, tuple[float, float]],
    novel_id: str,
    size: int = 1024,
    canvas_width: int = CANVAS_WIDTH,
    canvas_height: int = CANVAS_HEIGHT,
) -> str | None:
    """Generate a terrain PNG based on Voronoi regions + simplex noise.

    Uses fully vectorized numpy operations for performance.
    The output image aspect ratio matches the canvas (16:9).
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

    # Compute image dimensions preserving canvas aspect ratio
    aspect = canvas_width / max(canvas_height, 1)
    if aspect >= 1:
        img_w = size
        img_h = max(1, int(size / aspect))
    else:
        img_h = size
        img_w = max(1, int(size * aspect))

    # Scale layout coordinates from canvas to image
    scale_x = img_w / canvas_width
    scale_y = img_h / canvas_height
    points: list[list[float]] = []
    mountain_img_pts: list[tuple[float, float]] = []
    water_img_pts: list[tuple[float, float]] = []

    _MOUNTAIN_SUFFIXES = ("山", "峰", "岭", "崖", "岩")
    _WATER_SUFFIXES = ("河", "湖", "海", "泉", "潭", "溪", "池")

    for loc in locations:
        name = loc["name"]
        if name not in layout:
            continue
        x, y = layout[name]
        px = x * scale_x
        py = (canvas_height - y) * scale_y
        points.append([px, py])
        loc_type = loc.get("type", "")
        if any(s in name or s in loc_type for s in _MOUNTAIN_SUFFIXES):
            mountain_img_pts.append((px, py))
        if any(s in name or s in loc_type for s in _WATER_SUFFIXES):
            water_img_pts.append((px, py))

    if len(points) < 2:
        return None

    n_locations = len(points)

    # Add random fill points to reach max(n_locations, 30) for uniform Voronoi cells
    n_fill = max(0, 30 - n_locations)
    if n_fill > 0:
        fill_rng = np.random.default_rng(hash(novel_id) % (2**31) + 123)
        for _ in range(n_fill):
            points.append([
                float(fill_rng.uniform(20, img_w - 20)),
                float(fill_rng.uniform(20, img_h - 20)),
            ])

    point_arr = np.array(points, dtype=np.float64)

    # Lloyd relaxation: 2 iterations for more regular cell shapes
    if len(point_arr) >= 4:
        point_arr = _lloyd_relax(
            point_arr, img_w, img_h,
            n_fixed=n_locations, iterations=2, max_shift=30.0,
        )

    # Compute biome color per seed point via Whittaker matrix
    noise_gen = OpenSimplex(seed=hash(novel_id) % (2**31))
    moisture_gen = OpenSimplex(seed=hash(novel_id + "moisture") % (2**31))

    biome_colors: list[tuple[int, int, int]] = []
    for i in range(len(point_arr)):
        px, py = float(point_arr[i, 0]), float(point_arr[i, 1])
        elev = _elevation_at_img(px, py, noise_gen, img_w, img_h, mountain_img_pts, water_img_pts)
        moist = _moisture_at_img(px, py, moisture_gen, img_w, img_h, mountain_img_pts, water_img_pts)
        biome_colors.append(_biome_color_at(elev, moist))

    biome_arr = np.array(biome_colors, dtype=np.uint8)

    # Create coordinate grids
    ys, xs = np.mgrid[0:img_h, 0:img_w].astype(np.float64)

    # Add simplex noise displacement for natural Voronoi boundaries
    # Process in rows for noise2 (opensimplex doesn't have vectorized 2D)
    noise_scale = 0.005
    displacement = np.zeros((img_h, img_w), dtype=np.float64)
    for row in range(0, img_h, 4):  # sample every 4th row, interpolate
        for col in range(0, img_w, 4):
            displacement[row, col] = noise_gen.noise2(col * noise_scale, row * noise_scale)

    # Bilinear upsample the sparse noise grid
    from scipy.ndimage import zoom
    sparse = displacement[::4, ::4]
    zoom_y = img_h / max(sparse.shape[0], 1)
    zoom_x = img_w / max(sparse.shape[1], 1)
    displacement = zoom(sparse, (zoom_y, zoom_x), order=1)[:img_h, :img_w]

    xs_displaced = xs + displacement * 40
    ys_displaced = ys + displacement * 40

    # Find nearest point for each pixel (vectorized)
    # Process in row-blocks to manage memory (~img_w * n_points per block)
    block_size = 128
    nearest = np.zeros((img_h, img_w), dtype=np.int32)

    for row_start in range(0, img_h, block_size):
        row_end = min(row_start + block_size, img_h)
        bx = xs_displaced[row_start:row_end]  # (block, img_w)
        by = ys_displaced[row_start:row_end]

        # Compute distances to each point: (block, img_w, n_points)
        dx = bx[:, :, np.newaxis] - point_arr[:, 0]  # broadcast
        dy = by[:, :, np.newaxis] - point_arr[:, 1]
        dist_sq = dx ** 2 + dy ** 2
        nearest[row_start:row_end] = np.argmin(dist_sq, axis=2)

    # Build RGB image from nearest indices
    rgb = biome_arr[nearest]  # (img_h, img_w, 3)

    # Add multi-octave color variation noise for visual depth
    detail_noise = np.zeros((img_h, img_w), dtype=np.float64)
    octaves = [(0.01, 0.5), (0.03, 0.3), (0.08, 0.2)]  # (frequency, weight)
    step = 2
    for freq, weight in octaves:
        layer = np.zeros((img_h, img_w), dtype=np.float64)
        for row in range(0, img_h, step):
            for col in range(0, img_w, step):
                layer[row, col] = noise_gen.noise2(col * freq, row * freq)
        sparse_layer = layer[::step, ::step]
        zy = img_h / max(sparse_layer.shape[0], 1)
        zx = img_w / max(sparse_layer.shape[1], 1)
        layer = zoom(sparse_layer, (zy, zx), order=1)[:img_h, :img_w]
        detail_noise += layer * weight

    variation = (detail_noise * 40).astype(np.int16)  # ±20 amplitude
    rgb = np.clip(rgb.astype(np.int16) + variation[:, :, np.newaxis], 0, 255)

    # Paper grain overlay: high-frequency noise for parchment texture
    paper_noise = np.zeros((img_h, img_w), dtype=np.float64)
    paper_step = 4
    for row in range(0, img_h, paper_step):
        for col in range(0, img_w, paper_step):
            paper_noise[row, col] = noise_gen.noise2(col * 0.15, row * 0.15)
    sparse_paper = paper_noise[::paper_step, ::paper_step]
    pzy = img_h / max(sparse_paper.shape[0], 1)
    pzx = img_w / max(sparse_paper.shape[1], 1)
    paper_noise = zoom(sparse_paper, (pzy, pzx), order=1)[:img_h, :img_w]
    paper_variation = (paper_noise * 16).astype(np.int16)  # ±8 amplitude
    rgb = np.clip(rgb + paper_variation[:, :, np.newaxis], 0, 255)

    # Boundary darkening: darken pixels near region boundaries.
    # Skip darkening when cells are tiny (many points clustered together),
    # which would otherwise produce ugly dense stripes.
    min_cell_dist = min(img_w, img_h) * 0.03  # minimum cell size for darkening to apply
    darken = np.ones((img_h, img_w), dtype=np.float64)
    for row_start in range(0, img_h, block_size):
        row_end = min(row_start + block_size, img_h)
        bx = xs_displaced[row_start:row_end]
        by = ys_displaced[row_start:row_end]
        dx = bx[:, :, np.newaxis] - point_arr[:, 0]
        dy = by[:, :, np.newaxis] - point_arr[:, 1]
        dist_sq = dx ** 2 + dy ** 2
        # Partition to find two smallest distances
        part = np.partition(dist_sq, 1, axis=2)
        d1 = np.sqrt(part[:, :, 0])
        d2 = np.sqrt(part[:, :, 1])
        # Ratio: 1.0 at boundary (d1≈d2), 0.0 deep inside a region
        ratio = np.where(d2 > 0, d1 / d2, 0.0)
        # Map ratio to darkening factor: closer to 1.0 = more darkening (up to 25%)
        # Only apply when d2 is large enough (cell is big enough for visible border)
        edge_factor = np.clip((ratio - 0.75) / 0.25, 0.0, 1.0)
        # Suppress darkening for tiny cells (clustered points)
        cell_mask = np.where(d2 > min_cell_dist, 1.0, 0.0)
        darken[row_start:row_end] = 1.0 - edge_factor * cell_mask * 0.25
    rgb = (rgb * darken[:, :, np.newaxis]).astype(np.int16)

    # Global parchment tint: blend 15% warm cream color
    cream = np.array([235, 220, 195], dtype=np.int16)
    rgb = np.clip((rgb * 85 + cream * 15) // 100, 0, 255).astype(np.uint8)

    # Save
    img = Image.fromarray(rgb, "RGB")
    maps_dir = DATA_DIR / "maps" / novel_id
    maps_dir.mkdir(parents=True, exist_ok=True)
    out_path = maps_dir / "terrain.png"
    img.save(str(out_path), "PNG")
    logger.info("Terrain image saved: %s (%dx%d)", out_path, img_w, img_h)
    return str(out_path)


# ── Layout caching helpers ─────────────────────────


# Bump this when solver algorithm changes to invalidate layout cache
_LAYOUT_VERSION = 10

def compute_chapter_hash(
    chapter_start: int, chapter_end: int,
    canvas_width: int = CANVAS_WIDTH, canvas_height: int = CANVAS_HEIGHT,
) -> str:
    """Deterministic hash for a chapter range + canvas size + layout version."""
    key = f"{chapter_start}-{chapter_end}-cw{canvas_width}-ch{canvas_height}-v{_LAYOUT_VERSION}"
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


def place_unresolved_near_neighbors(
    unresolved_names: list[str],
    resolved_layout: list[dict],
    locations: list[dict],
    parent_map: dict[str, str | None],
    canvas_w: int,
    canvas_h: int,
) -> list[dict]:
    """Place unresolved location names near their resolved neighbors.

    Strategy:
      1. If the unresolved name has a parent that IS resolved, scatter around it.
      2. Otherwise, if any sibling (same parent) is resolved, scatter around the
         sibling's centroid.
      3. Last resort: place near the centroid of all resolved locations.

    Returns layout items for the unresolved names (same format as layout_to_list).
    """
    if not unresolved_names or not resolved_layout:
        return []

    # Build resolved coord lookup
    resolved_coords: dict[str, tuple[float, float]] = {
        item["name"]: (item["x"], item["y"]) for item in resolved_layout
    }

    # Build children-of-parent lookup from resolved locations
    parent_children: dict[str, list[str]] = {}
    for name, parent in parent_map.items():
        if parent and parent in resolved_coords:
            parent_children.setdefault(parent, []).append(name)

    # Compute global centroid as last-resort anchor
    all_xs = [c[0] for c in resolved_coords.values()]
    all_ys = [c[1] for c in resolved_coords.values()]
    global_cx = sum(all_xs) / len(all_xs)
    global_cy = sum(all_ys) / len(all_ys)

    # Location lookup for radius calculation
    loc_by_name = {loc["name"]: loc for loc in locations}

    # Scale jitter with canvas
    base_jitter = max(30, min(canvas_w, canvas_h) * 0.04)

    result: list[dict] = []
    orphan_idx = 0

    for name in unresolved_names:
        if name in resolved_coords:
            continue  # already placed

        anchor: tuple[float, float] | None = None

        # Strategy 1: parent is resolved
        parent = parent_map.get(name)
        if parent and parent in resolved_coords:
            anchor = resolved_coords[parent]
        else:
            # Strategy 2: find resolved sibling (share same parent)
            if parent:
                siblings = [
                    n for n in parent_children.get(parent, [])
                    if n in resolved_coords
                ]
                if siblings:
                    sx = sum(resolved_coords[s][0] for s in siblings) / len(siblings)
                    sy = sum(resolved_coords[s][1] for s in siblings) / len(siblings)
                    anchor = (sx, sy)

        if anchor is None:
            anchor = (global_cx, global_cy)

        # Golden-angle circular scatter around anchor
        ax, ay = anchor
        jitter_angle = orphan_idx * 2.4  # golden angle ≈ 137.5°
        jitter_r = base_jitter + base_jitter * 0.3 * (orphan_idx % 8)
        x = ax + jitter_r * math.cos(jitter_angle)
        y = ay + jitter_r * math.sin(jitter_angle)
        x = max(50, min(canvas_w - 50, x))
        y = max(50, min(canvas_h - 50, y))

        loc = loc_by_name.get(name, {})
        mention = loc.get("mention_count", 1)
        level = loc.get("level", 0)
        radius = max(15, min(60, 10 + mention * 2 + (3 - level) * 5))

        result.append({
            "name": name,
            "x": round(x, 1),
            "y": round(y, 1),
            "radius": radius,
        })
        orphan_idx += 1

    return result


# ── River network generation ────────────────────────────────────────

_WATER_ICONS = {"water", "island"}
_MOUNTAIN_ICONS = {"mountain"}


def _trace_river(
    sx: float, sy: float,
    elevation_fn,
    wiggle_gen,
    canvas_w: int, canvas_h: int,
    step: int = 20,
    max_steps: int = 200,
) -> list[tuple[float, float]]:
    """Trace a single river path via gradient descent with lateral wiggle."""
    path = [(sx, sy)]
    x, y = sx, sy
    for i in range(max_steps):
        cur_e = elevation_fn(x, y)
        best_x, best_y, best_e = x, y, cur_e
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1),
                        (-1, -1), (1, 1), (-1, 1), (1, -1)]:
            nx, ny = x + dx * step, y + dy * step
            if nx < 10 or nx > canvas_w - 10 or ny < 10 or ny > canvas_h - 10:
                continue
            e = elevation_fn(nx, ny)
            if e < best_e:
                best_x, best_y, best_e = nx, ny, e
        if best_x == x and best_y == y:
            break  # local minimum
        # Lateral wiggle perpendicular to flow direction
        fx, fy = best_x - x, best_y - y
        length = max(0.1, math.hypot(fx, fy))
        perp_x, perp_y = -fy / length, fx / length
        wiggle = wiggle_gen.noise2(best_x * 0.01, best_y * 0.01) * 15
        new_x = max(10, min(canvas_w - 10, best_x + wiggle * perp_x))
        new_y = max(10, min(canvas_h - 10, best_y + wiggle * perp_y))
        path.append((new_x, new_y))
        x, y = best_x, best_y  # gradient position (unwiggled) for next step
    return path


def generate_rivers(
    locations: list[dict],
    layout_data: list[dict],
    novel_id: str,
    canvas_width: int = CANVAS_WIDTH,
    canvas_height: int = CANVAS_HEIGHT,
) -> list[dict]:
    """Generate river paths from high-elevation sources toward low areas.

    Returns list of ``{"points": [[x, y], ...], "width": float}`` dicts.
    Returns empty list when no water/mountain locations exist (AC-9).
    """
    from opensimplex import OpenSimplex

    # Build coord lookup from layout
    coords: dict[str, tuple[float, float]] = {}
    for item in layout_data:
        coords[item["name"]] = (item["x"], item["y"])

    # Classify locations by icon
    mountain_pts: list[tuple[float, float]] = []
    water_pts: list[tuple[float, float]] = []
    for loc in locations:
        name = loc.get("name", "")
        icon = loc.get("icon", "generic")
        pt = coords.get(name)
        if not pt:
            continue
        if icon in _MOUNTAIN_ICONS:
            mountain_pts.append(pt)
        elif icon in _WATER_ICONS:
            water_pts.append(pt)

    # AC-9: skip if no relevant terrain features
    if not mountain_pts and not water_pts:
        return []

    # Deterministic noise generators (offset from terrain seed)
    base_seed = hash(novel_id) % (2**31)
    elev_noise = OpenSimplex(seed=base_seed + 42)
    wiggle_noise = OpenSimplex(seed=base_seed + 99)

    # ── Elevation field ──
    def elevation_at(x: float, y: float) -> float:
        nx, ny = x / canvas_width, y / canvas_height
        # Base terrain: two-octave noise
        e = elev_noise.noise2(nx * 3, ny * 3) * 0.5 + 0.5
        e += elev_noise.noise2(nx * 7, ny * 7) * 0.15
        # Mountain attraction: raise elevation near mountains
        for mx, my in mountain_pts:
            d = math.hypot(x - mx, y - my)
            if d < 300:
                e += 0.35 * max(0, 1 - d / 300)
        # Water attraction: lower elevation near water bodies
        for wx, wy in water_pts:
            d = math.hypot(x - wx, y - wy)
            if d < 300:
                e -= 0.35 * max(0, 1 - d / 300)
        return e

    # ── Identify river sources ──
    sources: list[tuple[float, float]] = []
    if mountain_pts:
        for mx, my in mountain_pts:
            angle = elev_noise.noise2(mx * 0.1, my * 0.1) * math.pi
            sx = mx + 40 * math.cos(angle)
            sy = my + 40 * math.sin(angle)
            sx = max(30, min(canvas_width - 30, sx))
            sy = max(30, min(canvas_height - 30, sy))
            sources.append((sx, sy))
    else:
        # No mountains: sample highest-elevation points
        rng = np.random.default_rng(base_seed + 7)
        for _ in range(5):
            best, best_e = (canvas_width / 2, canvas_height / 2), -999.0
            for _ in range(30):
                cx = float(rng.uniform(50, canvas_width - 50))
                cy = float(rng.uniform(50, canvas_height - 50))
                e = elevation_at(cx, cy)
                if e > best_e:
                    best, best_e = (cx, cy), e
            sources.append(best)

    # Limit to 3-8 rivers
    sources = sources[:8]

    # ── Trace rivers ──
    rivers: list[dict] = []
    for sx, sy in sources:
        path = _trace_river(
            sx, sy, elevation_at, wiggle_noise,
            canvas_width, canvas_height,
        )
        if len(path) < 5:
            continue
        width = min(3.0, max(1.0, len(path) / 30))
        rivers.append({
            "points": [[round(px, 1), round(py, 1)] for px, py in path],
            "width": round(width, 1),
        })

    return rivers
