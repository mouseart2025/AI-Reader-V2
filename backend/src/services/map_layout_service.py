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
import logging
import math
import re
from pathlib import Path
from typing import ClassVar

import numpy as np
from scipy.optimize import differential_evolution
from scipy.spatial import Delaunay, Voronoi

from src.infra.config import DATA_DIR
from src.models.chapter_fact import classify_spatial_relation
from src.services.location_influence import influence_classes

logger = logging.getLogger(__name__)

# Canvas coordinate range (16:9 aspect ratio)
CANVAS_WIDTH = 1600
CANVAS_HEIGHT = 900
CANVAS_MIN_X = 100
CANVAS_MAX_X = CANVAS_WIDTH - 100
CANVAS_MIN_Y = 60
CANVAS_MAX_Y = CANVAS_HEIGHT - 60

# Spatial scale → canvas size mapping (width, height) — 16:9 ratio
SPATIAL_SCALE_CANVAS: dict[str, tuple[int, int]] = {
    "interstellar": (12000, 6750),
    "cosmic": (8000, 4500),
    "planetary": (6400, 3600),
    "continental": (4800, 2700),
    "national": (3200, 1800),
    "city": (2400, 1350),
    "district": (1600, 900),
    "building": (1200, 675),
    "room": (800, 450),
    # Legacy aliases (backward compat)
    "urban": (1600, 900),
    "local": (800, 450),
}

# Base minimum spacing between any two locations (pixels).
# Dynamically adjusted in ConstraintSolver.__init__ via canvas_w * 0.015.
MIN_SPACING = 30

# Scatter radius per tier, as a fraction of the canvas short side. Finer tiers hug
# their anchor tighter — the game-scatter "density mask" rule. Replaces the old
# canvas-global jitter constant, which ignored scale entirely and let site/building
# nodes drift thousands of pixels away from their parent.
TIER_SCATTER_RADIUS: dict[str, float] = {
    "continent": 0.030,
    "kingdom": 0.028,
    "region": 0.024,
    "city": 0.018,
    "site": 0.012,
    "building": 0.008,
}
DEFAULT_SCATTER_RADIUS = 0.012

# Floor so tiny canvases (room 800x450) still separate nodes visually.
MIN_SCATTER_RADIUS = 4.0

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

# Cluster grouping target distance
CLUSTER_DIST = 100

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

# Celestial locations placed in top zone (small Y in SVG), underworld in bottom
_CELESTIAL_Y_RANGE = (CANVAS_MIN_Y, CANVAS_MIN_Y + 30)
_UNDERWORLD_Y_RANGE = (CANVAS_MAX_Y - 30, CANVAS_MAX_Y)

# ── Direction mapping ───────────────────────────────

_DIRECTION_VECTORS: dict[str, tuple[int, int]] = {
    # (dx_sign, dy_sign): +x = east (right), -y = north (up in SVG)
    "north_of": (0, -1),
    "south_of": (0, 1),
    "east_of": (1, 0),
    "west_of": (-1, 0),
    "northeast_of": (1, -1),
    "northwest_of": (-1, -1),
    "southeast_of": (1, 1),
    "southwest_of": (-1, 1),
}

# ── Region layout ─────────────────────────────────

# Direction → bounding box zone (x1, y1, x2, y2) on 1600×900 canvas.
# SVG convention: +x = east (right), +y = south (down). North = small Y.
DIRECTION_ZONES: dict[str, tuple[float, float, float, float]] = {
    "east":   (960, 180, 1550, 720),
    "west":   (50, 180, 640, 720),
    "north":  (320, 50, 1280, 315),
    "south":  (320, 585, 1280, 850),
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
        "north":  (0.50, 0.25),   # top of canvas (small Y in SVG)
        "south":  (0.50, 0.75),   # bottom of canvas (large Y in SVG)
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
    margin_x = canvas_width * 0.07
    margin_y = canvas_height * 0.07

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
        # Clip to canvas with margin so locations don't land at the very edge
        verts[:, 0] = np.clip(verts[:, 0], margin_x, cw - margin_x)
        verts[:, 1] = np.clip(verts[:, 1], margin_y, ch - margin_y)
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
    "pocket": (1200, 675),
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
    coords, _, _ = solver.solve()
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
    coords, _, _ = solver.solve()
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
    location_parents_ws = world_structure.get("location_parents", {})

    # Dynamic canvas size for overworld based on spatial scale
    canvas_w, canvas_h = SPATIAL_SCALE_CANVAS.get(
        spatial_scale or "", (CANVAS_WIDTH, CANVAS_HEIGHT)
    )
    margin_x = max(100, int(canvas_w * 0.07))
    margin_y = max(60, int(canvas_h * 0.07))
    overworld_bounds = (margin_x, margin_y, canvas_w - margin_x, canvas_h - margin_y)

    if not layers:
        return {}


    # Partition locations by layer
    layer_locations: dict[str, list[dict]] = {layer["layer_id"]: [] for layer in layers}

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
                    location_parents=location_parents_ws,
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
                layout_coords, _, _ = solver.solve()

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
    location_parents: dict[str, str] | None = None,
    user_overrides: dict[str, tuple[float, float]] | None = None,
    first_chapter: dict[str, int] | None = None,
    canvas_width: int = CANVAS_WIDTH,
    canvas_height: int = CANVAS_HEIGHT,
) -> dict[str, tuple[float, float]]:
    """Solve overworld layout by partitioning into regions.

    Locations assigned to a region are solved within that region's bounding box.
    Unassigned locations go through a global fallback solve.
    """
    # ── Prune & deduplicate regions for Voronoi ──
    # Too many regions (e.g., 152 for 西游记) creates tiny Voronoi cells.
    # Strategy:
    # 1. Deduplicate variant names (南赡部洲 / 南瞻部洲 / 南赡养部洲 → keep best)
    # 2. Score by location count + continent bonus
    # 3. Only continent-scale names (洲 suffix) keep their cardinal_direction for
    #    Voronoi seeding; sub-regions lose it to avoid crowding direction sectors
    _CONTINENT_SUFFIX = "洲"

    # Count locations per region for importance ranking
    region_loc_count: dict[str, int] = {}
    all_region_names = {r.get("name", "") for r in regions}
    for loc in locations:
        name = loc["name"]
        if name in all_region_names:
            region_loc_count[name] = region_loc_count.get(name, 0) + 1
            continue
        rn = location_region_map.get(name)
        if rn:
            region_loc_count[rn] = region_loc_count.get(rn, 0) + 1

    # Import normalization for deduplication
    try:
        from src.extraction.fact_validator import _LOCATION_NAME_NORMALIZE
    except ImportError:
        _LOCATION_NAME_NORMALIZE = {}

    # Deduplicate: group by canonical name, keep the variant with most locations
    canonical_groups: dict[str, list[dict]] = {}
    for r in regions:
        rname = r.get("name", "")
        canon = _LOCATION_NAME_NORMALIZE.get(rname, rname)
        canonical_groups.setdefault(canon, []).append(r)

    deduped: list[dict] = []
    for _canon, group in canonical_groups.items():
        # Pick the variant with the most locations
        best = max(group, key=lambda g: region_loc_count.get(g.get("name", ""), 0))
        # Merge cardinal_direction from any variant
        direction = best.get("cardinal_direction")
        if not direction:
            for g in group:
                if g.get("cardinal_direction"):
                    direction = g["cardinal_direction"]
                    break
        # Sum location counts across all variants
        total_locs = sum(region_loc_count.get(g.get("name", ""), 0) for g in group)
        deduped.append({
            "name": best.get("name", ""),
            "cardinal_direction": direction,
            "_loc_count": total_locs,
        })

    def _region_score(r: dict) -> float:
        rname = r.get("name", "")
        score = float(r.get("_loc_count", region_loc_count.get(rname, 0)))
        # Continent-scale names (ends with 洲) → always include
        if rname.endswith(_CONTINENT_SUFFIX):
            score += 20000
        return score

    scored = sorted(deduped, key=_region_score, reverse=True)
    pruned_regions = scored[:MAX_SOLVER_REGIONS]

    logger.info(
        "Pruned overworld regions from %d (deduped %d) to %d (top: %s)",
        len(regions), len(deduped), len(pruned_regions),
        ", ".join(f"{r.get('name','')}({r.get('cardinal_direction','-')})"
                  for r in pruned_regions[:6]),
    )

    # Build Voronoi inputs: only continent-scale (洲) regions keep cardinal
    # direction for seeding. Sub-regions use None to avoid crowding sectors.
    region_dicts = [
        {
            "name": r.get("name", ""),
            "cardinal_direction": (
                r.get("cardinal_direction")
                if r.get("name", "").endswith(_CONTINENT_SUFFIX)
                else None
            ),
        }
        for r in pruned_regions
    ]
    region_layout = _layout_regions(region_dicts, canvas_width=canvas_width, canvas_height=canvas_height)

    # Build a region name lookup for the pruned set
    pruned_region_name_set = {r["name"] for r in region_dicts}

    # Build parent chain for walking up to an ancestor region in the pruned set.
    # location_region_map maps locations to their direct region, but if that
    # region was pruned, we need to find its parent region. We build this by
    # treating location_region_map transitively: if "花果山" → "傲来国" and
    # "傲来国" → "东胜神洲", then 花果山 should inherit 东胜神洲's bounds.
    def _find_pruned_region(name: str) -> str | None:
        """Walk up the region chain to find an ancestor in the pruned set."""
        visited: set[str] = set()
        current = name
        for _ in range(10):  # max depth to avoid infinite loops
            rn = location_region_map.get(current)
            if rn is None or rn in visited:
                return None
            if rn in pruned_region_name_set:
                return rn
            visited.add(rn)
            current = rn
        return None

    # Identify continent-scale regions (these get cardinal-direction Voronoi cells)
    continent_region_names = {
        r["name"] for r in region_dicts
        if r["name"].endswith(_CONTINENT_SUFFIX)
    }

    def _find_continent_ancestor(name: str) -> str | None:
        """Walk up hierarchy to find a continent-scale ancestor (洲).

        Path 1 (authoritative): location_parents chain.
        Path 2 (fallback): location_region_map chain.
        """
        # Path 1: location_parents (authoritative hierarchy after consolidation)
        if location_parents:
            visited: set[str] = set()
            current = location_parents.get(name)
            while current and current not in visited:
                if current in continent_region_names:
                    return current
                visited.add(current)
                current = location_parents.get(current)

        # Path 2: location_region_map (fallback)
        visited2: set[str] = set()
        current2 = name
        for _ in range(10):
            rn = location_region_map.get(current2)
            if rn is None or rn in visited2:
                return None
            if rn in continent_region_names:
                return rn
            visited2.add(rn)
            current2 = rn
        return None

    # Partition locations by region.
    # Strategy: prefer continent-scale ancestors over intermediate sub-regions
    # so that locations end up in the correct cardinal-direction cell.
    region_locs: dict[str, list[dict]] = {r["name"]: [] for r in region_dicts}
    unassigned_locs: list[dict] = []

    for loc in locations:
        name = loc["name"]
        # Priority 1: continent-scale self-match (e.g., 东胜神洲)
        if name in continent_region_names:
            region_locs[name].append(loc)
            continue

        # Priority 2: find a continent-scale ancestor via parent chain
        # (e.g., 花果山 → 傲来国 → 东胜神洲)
        continent_anc = _find_continent_ancestor(name)
        if continent_anc:
            region_locs[continent_anc].append(loc)
            continue

        # Priority 3: direct region lookup (for locations without continent ancestry)
        region_name = location_region_map.get(name)
        if region_name and region_name in region_locs:
            region_locs[region_name].append(loc)
            continue

        # Priority 4: walk up location_parents chain to find any pruned region
        if location_parents:
            visited_p: set[str] = set()
            cur = location_parents.get(name)
            found_parent_region = False
            while cur and cur not in visited_p:
                if cur in pruned_region_name_set and cur in region_locs:
                    region_locs[cur].append(loc)
                    found_parent_region = True
                    break
                visited_p.add(cur)
                cur = location_parents.get(cur)
            if found_parent_region:
                continue

        # Priority 5: walk up location_region_map chain
        ancestor = _find_pruned_region(name)
        if ancestor:
            region_locs[ancestor].append(loc)
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
        all_locs = locations
        loc_region_bounds: dict[str, tuple[float, float, float, float]] = {}
        for loc in all_locs:
            name = loc["name"]
            # Priority 1: continent-scale self-match
            if name in continent_region_names and name in region_layout:
                loc_region_bounds[name] = region_layout[name]["bounds"]
                continue
            # Priority 2: continent ancestor via parent chain
            ca = _find_continent_ancestor(name)
            if ca and ca in region_layout:
                loc_region_bounds[name] = region_layout[ca]["bounds"]
                continue
            # Priority 3: direct region lookup
            rn = location_region_map.get(name)
            if rn and rn in region_layout:
                loc_region_bounds[name] = region_layout[rn]["bounds"]
                continue
            # Priority 4: any pruned ancestor
            ancestor = _find_pruned_region(name)
            if ancestor and ancestor in region_layout:
                loc_region_bounds[name] = region_layout[ancestor]["bounds"]

        logger.info(
            "Using global solver for %d locations across %d regions "
            "(exceeds MAX_SOLVER_REGIONS=%d); %d have region bounds",
            len(all_locs), non_empty_count, MAX_SOLVER_REGIONS,
            len(loc_region_bounds),
        )

        coords, _, _ = ConstraintSolver.progressive_solve(
            all_locs, constraints,
            user_overrides=user_overrides,
            first_chapter=first_chapter,
            location_region_bounds=loc_region_bounds,
            canvas_bounds=fallback_bounds,
        )
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
                name = loc["name"]
                if name in region_layout:
                    loc_region_bounds_ua[name] = region_layout[name]["bounds"]
                else:
                    rn = location_region_map.get(name)
                    if rn and rn in region_layout:
                        loc_region_bounds_ua[name] = region_layout[rn]["bounds"]
                    else:
                        ancestor = _find_pruned_region(name)
                        if ancestor and ancestor in region_layout:
                            loc_region_bounds_ua[name] = region_layout[ancestor]["bounds"]

            coords, _, _ = ConstraintSolver.progressive_solve(
                unassigned_locs, constraints,
                user_overrides=user_overrides,
                first_chapter=first_chapter,
                location_region_bounds=loc_region_bounds_ua,
                canvas_bounds=fallback_bounds,
            )
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
    for _key, group in direction_map.items():
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
    return bool(v1 == v2 and c1["source"] == c2["target"] and c1["target"] == c2["source"])


# ── Constraint Solver ──────────────────────────────


# Default max solver locations. Dynamically scaled per region in _select_solver_locations.
_DEFAULT_MAX_SOLVER_LOCATIONS = 80


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
        return bool(any(kw in name for kw in _MACRO_TYPE_KW))

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
        if classify_spatial_relation(c["relation_type"]) != "hierarchy":
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

    # ── Strategy 2.5: Aggregate direction constraints (LLM anchor ×3) ──
    # Count direction constraint votes; llm_anchor constraints get 3× weight
    dir_votes: dict[str, float] = {}  # direction → accumulated weight
    for c in constraints:
        if c["relation_type"] != "direction":
            continue
        direction = c.get("value", "")
        if direction not in _DIRECTION_VECTORS:
            continue
        w = 3.0 if c.get("source_type") == "llm_anchor" else 1.0
        dir_votes[direction] = dir_votes.get(direction, 0) + w

    if dir_votes:
        total_votes = sum(dir_votes.values())
        # Check if 70%+ of direction votes point in one direction.
        # Minimum 3 weighted votes to avoid noise from 1-2 stray constraints.
        for direction, count in sorted(dir_votes.items(), key=lambda x: -x[1]):
            if count >= total_votes * 0.7 and count >= 3:
                vec = _DIRECTION_VECTORS[direction]
                logger.info(
                    "Direction constraint majority: %s (%.0f%% of %.0f votes)",
                    direction, count / total_votes * 100, total_votes,
                )
                net_dx += vec[0] * 2.0
                net_dy += vec[1] * 2.0
                break

    if abs(net_dx) > 0.5 or abs(net_dy) > 0.5:
        magnitude = math.sqrt(net_dx ** 2 + net_dy ** 2)
        return (net_dx / magnitude, net_dy / magnitude)

    # ── Strategy 3: Direction constraints weighted by chapter separation ──
    for c in constraints:
        if c["relation_type"] != "direction":
            continue
        vec = _DIRECTION_VECTORS.get(c["value"])
        if vec is None:
            continue

        # LLM anchor constraints get 3× weight even in Strategy 3
        base_w = 3.0 if c.get("source_type") == "llm_anchor" else 1.0

        src_ch = first_chapter.get(c["source"], 0)
        tgt_ch = first_chapter.get(c["target"], 0)
        if src_ch == 0 or tgt_ch == 0:
            # For llm_anchor constraints without chapter info, still count
            if c.get("source_type") == "llm_anchor":
                net_dx += vec[0] * base_w
                net_dy += vec[1] * base_w
            continue

        ch_diff = src_ch - tgt_ch
        if abs(ch_diff) < 10 and c.get("source_type") != "llm_anchor":
            continue

        weight = base_w * (1.0 if abs(ch_diff) < 20 else 2.0)
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
        fixed_positions: dict[str, tuple[float, float]] | None = None,
    ):
        self.all_locations = locations
        self.constraints = _detect_and_remove_conflicts(constraints)
        # Merge fixed_positions into user_overrides (fixed from previous batch)
        self.user_overrides = dict(user_overrides or {})
        if fixed_positions:
            self.user_overrides.update(fixed_positions)
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
        self._min_spacing = max(MIN_SPACING, canvas_w * 0.015)

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
            # Continent-tier roots get very high priority — they define the
            # macro structure and must always be included in the solver.
            level = loc.get("level", 0)
            tier = loc.get("tier", "")
            if level == 0 and tier == "continent":
                score += 20000  # always include continent roots
            elif level == 0:
                score += 2000   # roots anchor the layout
            elif level == 1:
                score += 500
            scored.append((score, loc))

        scored.sort(key=lambda x: -x[0])

        # Dynamic solver capacity: min(80, total) — allows more locations in solver
        max_solver = min(_DEFAULT_MAX_SOLVER_LOCATIONS, len(self.all_locations))
        solver_locs = [loc for _, loc in scored[:max_solver]]

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

    def solve(self) -> tuple[dict[str, tuple[float, float]], str, dict | None]:
        """Solve layout. Returns (name->coords, layout_mode, satisfaction_or_None)."""
        if len(self.constraints) < 3 or self.n < 2:
            logger.info(
                "Insufficient constraints (%d) or locations (%d), using hierarchy layout",
                len(self.constraints), self.n,
            )
            layout = self._hierarchy_layout()
            self._place_remaining(layout)
            return layout, "hierarchy", None

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
            return layout, "hierarchy", None

        # Scale solver budget based on problem size
        # With 80 locations (160 params), keep budget tight for responsiveness
        maxiter = max(50, min(200, 2000 // max(self.n, 1)))
        popsize = max(5, min(8, 200 // max(self.n, 1)))  # DE requires S > 4

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
            satisfaction = self._calculate_satisfaction(coords)
            logger.info(
                "Constraint solver converged: energy=%.2f, iter=%d, satisfaction=%.1f%%",
                result.fun, result.nit, satisfaction["total_satisfaction"] * 100,
            )
            self._place_remaining(layout)
            return layout, "constraint", satisfaction
        except Exception:
            logger.exception("Constraint solver failed, falling back to hierarchy")
            layout = self._hierarchy_layout()
            self._place_remaining(layout)
            return layout, "hierarchy", None

    @staticmethod
    def progressive_solve(
        locations: list[dict],
        constraints: list[dict],
        user_overrides: dict[str, tuple[float, float]] | None = None,
        first_chapter: dict[str, int] | None = None,
        location_region_bounds: dict[str, tuple[float, float, float, float]] | None = None,
        canvas_bounds: tuple[float, float, float, float] | None = None,
    ) -> tuple[dict[str, tuple[float, float]], str, dict | None]:
        """Progressive batched solving for large constraint sets.

        If >_DEFAULT_MAX_SOLVER_LOCATIONS constrained locations exist, solves in batches:
        batch 1 (top priority) → lock positions → batch 2 → ... until all
        constrained locations are solver-optimized.
        """
        # Count constrained locations
        constrained_names: set[str] = set()
        for c in constraints:
            constrained_names.add(c["source"])
            constrained_names.add(c["target"])

        loc_names = {loc["name"] for loc in locations}
        constrained_in_locs = constrained_names & loc_names

        if len(constrained_in_locs) <= _DEFAULT_MAX_SOLVER_LOCATIONS:
            # Single batch is sufficient
            solver = ConstraintSolver(
                locations, constraints,
                user_overrides=user_overrides,
                first_chapter=first_chapter,
                location_region_bounds=location_region_bounds,
                canvas_bounds=canvas_bounds,
            )
            return solver.solve()

        logger.info(
            "Progressive solve: %d constrained locations > cap %d, using batched solving",
            len(constrained_in_locs), _DEFAULT_MAX_SOLVER_LOCATIONS,
        )

        fixed: dict[str, tuple[float, float]] = {}
        final_layout: dict[str, tuple[float, float]] = {}
        final_mode = "hierarchy"
        final_satisfaction = None
        batch = 0

        while True:
            batch += 1
            solver = ConstraintSolver(
                locations, constraints,
                user_overrides=user_overrides,
                first_chapter=first_chapter,
                location_region_bounds=location_region_bounds,
                canvas_bounds=canvas_bounds,
                fixed_positions=fixed if fixed else None,
            )

            # Check if there are still unsolved constrained locations
            solved_names = set(fixed.keys())
            unsolved_constrained = constrained_in_locs - solved_names - set(user_overrides or {})
            if not unsolved_constrained or batch > 5:
                # Final batch: solve and break
                layout, mode, satisfaction = solver.solve()
                final_layout.update(layout)
                final_mode = mode
                final_satisfaction = satisfaction
                break

            layout, mode, satisfaction = solver.solve()
            final_layout.update(layout)
            final_mode = mode
            final_satisfaction = satisfaction

            # Lock newly solved positions for next batch
            new_fixed = {
                name: coords for name, coords in layout.items()
                if name in constrained_in_locs and name not in fixed
            }
            if not new_fixed:
                break  # No progress — avoid infinite loop
            fixed.update(new_fixed)
            logger.info(
                "Progressive batch %d: solved %d, total fixed %d / %d constrained",
                batch, len(new_fixed), len(fixed), len(constrained_in_locs),
            )

        return final_layout, final_mode, final_satisfaction

    @staticmethod
    def _stable_hash(text: str) -> float:
        """Deterministic [0,1) hash — unlike builtin hash(), stable across processes.

        Python randomizes str hashing per process (PYTHONHASHSEED), so the old
        `hash(name)` fallback produced different coordinates on every restart,
        breaking layout reproducibility. md5 matches the deterministic-hash usage
        already present elsewhere in this module (layout cache key, terrain seed).
        """
        return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16) / 0x100000000

    def _resolve_anchor(self, name: str, layout: dict[str, tuple[float, float]]) -> str | None:
        """Walk up the parent chain to the nearest ancestor already placed.

        Game scatter tools always bind placement to a parent surface. Without an
        anchor, a child falls through to an unconstrained fallback branch and can
        land thousands of pixels away (measured max 5860px on 西游记; see
        ai-reader-internal/docs/analysis/containment-violation-baseline-2026-09-20.md).
        """
        seen: set[str] = set()
        cur = self._parent_map.get(name)
        while cur and cur not in seen:
            if cur in layout:
                return cur
            seen.add(cur)
            cur = self._parent_map.get(cur)
        return None

    def _scatter_radius(self, tier: str) -> float:
        """Scatter radius for a tier, proportional to the canvas short side.

        Returns 0.0 when the tier is unknown so callers can fall back to their own
        floor (keeps legacy behaviour for un-tiered data).
        """
        factor = TIER_SCATTER_RADIUS.get(tier)
        if factor is None:
            return 0.0
        short = min(
            self._canvas_max_x - self._canvas_min_x,
            self._canvas_max_y - self._canvas_min_y,
        )
        return max(MIN_SCATTER_RADIUS, short * factor)

    def _place_remaining(self, layout: dict[str, tuple[float, float]]) -> None:
        """Place locations not included in the solver, anchored to their hierarchy.

        Strategy:
        1. User overrides take priority.
        2. Parent — or nearest placed ancestor — in layout: sunflower scatter
           around it, radius driven by the child's own tier.
        3. Otherwise: co-chapter centroid with a tight tier-scaled radius.
        4. Last resort: deterministic scatter near the canvas centre.

        Ordering note: locations are processed shallowest-first so a parent is
        always placed before its children, which lets rule 2 catch most of them.
        """
        # Build chapter->solved_locations lookup for proximity placement
        chapter_locs: dict[int, list[str]] = {}
        for name in layout:
            ch = self.first_chapter.get(name, 0)
            if ch > 0:
                chapter_locs.setdefault(ch, []).append(name)

        canvas_w = self._canvas_max_x - self._canvas_min_x
        canvas_h = self._canvas_max_y - self._canvas_min_y
        # Legacy canvas-global jitter, kept only as a floor for un-tiered data
        base_jitter = max(30, min(canvas_w, canvas_h) * 0.04)
        golden_angle = math.pi * (3 - math.sqrt(5))  # ≈ 137.5°

        # Shallowest-first, name as tie-break: deterministic, and guarantees a
        # parent is placed before its children
        ordered = sorted(self._remaining, key=lambda loc: (loc.get("level", 0), loc["name"]))

        orphan_idx = 0  # for jittering orphans that share positions

        for loc in ordered:
            name = loc["name"]
            if name in layout:
                continue
            if name in self.user_overrides:
                layout[name] = self.user_overrides[name]
                continue

            tier = loc.get("tier", "")
            tier_r = self._scatter_radius(tier) or base_jitter

            # Prefer the direct parent; else climb to the nearest placed ancestor
            parent = self._parent_map.get(name)
            if parent and parent in layout:
                anchor, hops = parent, 1
            else:
                anchor, hops = self._resolve_anchor(name, layout), 2

            if anchor is not None:
                ax, ay = layout[anchor]
                children_here = self.children.get(anchor, [])
                idx = children_here.index(name) if name in children_here else 0
                n_children = max(len(children_here), 1)
                # Sunflower seed distribution: golden angle + varying radius fills
                # the circular area organically instead of a ring perimeter
                frac = (idx + 0.5) / n_children  # 0..1
                # Radius from the child's own tier; a grandchild spreads a bit wider
                adaptive_r = tier_r * max(0.6, math.sqrt(n_children / 8)) * (1.0 + 0.5 * (hops - 1))
                # Cap so a large sibling group still hugs the anchor
                adaptive_r = min(adaptive_r, min(canvas_w, canvas_h) * 0.12)
                r = adaptive_r * (0.3 + 0.7 * math.sqrt(frac))
                angle = idx * golden_angle
                x = max(self._canvas_min_x, min(self._canvas_max_x, ax + r * math.cos(angle)))
                y = max(self._canvas_min_y, min(self._canvas_max_y, ay + r * math.sin(angle)))
                layout[name] = (x, y)
                continue

            # Chapter-proximity: find solved locations from same or nearby chapters
            ch = self.first_chapter.get(name, 0)
            centroid = self._find_chapter_centroid(ch, layout, chapter_locs)

            if centroid is not None:
                cx, cy = centroid
                # Tight tier-scaled scatter around the co-chapter centroid
                jitter_angle = orphan_idx * 2.4
                jitter_r = tier_r * (1.0 + 0.3 * (orphan_idx % 8))
                x = cx + jitter_r * math.cos(jitter_angle)
                y = cy + jitter_r * math.sin(jitter_angle)
            else:
                # Last resort: deterministic scatter near the canvas centre.
                # Never canvas-wide random — that was the source of 5000+px drift.
                h1 = self._stable_hash(name)
                h2 = self._stable_hash(name + "_y")
                spawn_r = min(canvas_w, canvas_h) * (0.02 + 0.06 * h2)
                spawn_angle = h1 * 2 * math.pi
                x = self._canvas_cx + spawn_r * math.cos(spawn_angle)
                y = self._canvas_cy + spawn_r * math.sin(spawn_angle)

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
        # Celestial: top of map (small Y in SVG)
        for i, loc in enumerate(self._celestial):
            name = loc["name"]
            if name in self.user_overrides:
                layout[name] = self.user_overrides[name]
                continue
            x = self._canvas_min_x + (i + 1) * w / (len(self._celestial) + 1)
            y = self._canvas_min_y + 15
            layout[name] = (x, y)

        # Underworld: bottom of map (large Y in SVG)
        for i, loc in enumerate(self._underworld):
            name = loc["name"]
            if name in self.user_overrides:
                layout[name] = self.user_overrides[name]
                continue
            x = self._canvas_min_x + (i + 1) * w / (len(self._underworld) + 1)
            y = self._canvas_max_y - 15
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
            # Dual-track confidence: prefer numeric score, fall back to string rank
            cs = c.get("confidence_score")
            weight = max(cs * 3.0, 0.3) if cs is not None else _CONF_RANK.get(c.get("confidence", "medium"), 2)

            if rtype == "direction":
                e += self._e_direction(coords, si, ti, value) * weight
            elif rtype == "distance":
                e += self._e_distance(coords, si, ti, value, c.get("distance_class")) * weight
            elif classify_spatial_relation(rtype) == "hierarchy":
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
            elif rtype == "travel_path":
                wps = c.get("waypoints") or []
                indices = [si]
                for wp in wps:
                    wi = self.loc_index.get(wp)
                    if wi is not None:
                        indices.append(wi)
                indices.append(ti)
                e += self._e_travel_path(coords, indices) * weight
            elif rtype == "travel_sequence":
                e += self._e_adjacent(coords, si, ti) * weight
            elif rtype == "cluster":
                e += self._e_cluster(coords, si, ti) * weight

        # Anti-overlap penalty (vectorized)
        e += self._e_overlap(coords)

        # Uniform spread: repulsion weight scales with location count
        # More locations → stronger repulsion to prevent clustering
        _spread_w = 0.3 + 0.2 * min(1.0, self.n / 100)
        e += self._e_uniform_spread(coords) * _spread_w

        # Narrative order: weight scales with location count
        _narr_w = 0.05 + 0.05 * min(1.0, self.n / 50)
        e += self._e_narrative_order(coords) * _narr_w

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
        # x: 0=west, 1=east; y: 0=north (top), 1=south (bottom) — SVG convention
        _HINT_TARGETS: dict[str, tuple[float, float]] = {
            "east": (0.75, 0.5),
            "west": (0.25, 0.5),
            "north": (0.5, 0.25),
            "south": (0.5, 0.75),
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

    # distance_class → target canvas distance mapping
    _DC_TARGET: ClassVar[dict[str, float]] = {
        "near": 60,      # DEFAULT_NEAR_DIST
        "medium": 150,
        "far": 300,       # DEFAULT_FAR_DIST
        "very_far": 400,
    }

    def _e_distance(
        self, coords: np.ndarray, si: int, ti: int, value: str,
        distance_class: str | None = None,
    ) -> float:
        """Distance penalty: actual distance should match parsed target distance.

        Prefers structured distance_class when available (near/medium/far/very_far)
        over free-text parsing, as it's more reliable.
        """
        if distance_class and distance_class in self._DC_TARGET:
            target_dist = self._DC_TARGET[distance_class]
        else:
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

    def _e_travel_path(self, coords: np.ndarray, indices: list[int]) -> float:
        """Travel path penalty: waypoints should maintain topological order.

        Penalizes backward movement along the path: if a segment (pi→pi+1)
        moves against the overall source→target direction, a quadratic penalty
        is applied.  Returns 0.0 if fewer than 3 points (no intermediate
        waypoints to constrain).
        """
        k = len(indices)
        if k < 3:
            return 0.0
        pts = coords[indices]
        v_main = pts[-1] - pts[0]
        main_len = np.linalg.norm(v_main)
        if main_len < 1e-6:
            return 0.0
        v_norm = v_main / main_len
        penalty = 0.0
        for i in range(k - 1):
            v_seg = pts[i + 1] - pts[i]
            proj = float(np.dot(v_seg, v_norm))
            if proj < 0:
                penalty += (proj / main_len) ** 2
        return penalty * 50

    def _e_cluster(self, coords: np.ndarray, si: int, ti: int) -> float:
        """Cluster penalty: grouped locations should stay close together."""
        dist = float(np.linalg.norm(coords[si] - coords[ti]))
        if dist <= CLUSTER_DIST:
            return 0.0
        return ((dist - CLUSTER_DIST) / CLUSTER_DIST) ** 2 * 50

    # ── Constraint satisfaction checks (bool, for quality metrics) ──

    def _is_satisfied_direction(self, coords: np.ndarray, si: int, ti: int, value: str) -> bool:
        vec = _DIRECTION_VECTORS.get(value)
        if vec is None:
            return True
        dx = coords[si, 0] - coords[ti, 0]
        dy = coords[si, 1] - coords[ti, 1]
        if vec[0] != 0 and vec[0] * dx < -DIRECTION_MARGIN:
            return False
        return not (vec[1] != 0 and vec[1] * dy < -DIRECTION_MARGIN)

    def _is_satisfied_distance(
        self, coords: np.ndarray, si: int, ti: int, value: str,
        distance_class: str | None = None,
    ) -> bool:
        if distance_class and distance_class in self._DC_TARGET:
            target = self._DC_TARGET[distance_class]
        else:
            target = parse_distance(value)
        if target <= 0:
            return True
        actual = float(np.linalg.norm(coords[si] - coords[ti]))
        return abs(actual - target) <= target * 0.3

    def _is_satisfied_contains(self, coords: np.ndarray, si: int, ti: int) -> bool:
        radius = self._get_parent_radius(si)
        dist = float(np.linalg.norm(coords[si] - coords[ti]))
        return dist <= radius * 1.2

    def _is_satisfied_adjacent(self, coords: np.ndarray, si: int, ti: int) -> bool:
        dist = float(np.linalg.norm(coords[si] - coords[ti]))
        return ADJACENT_DIST * 0.5 <= dist <= ADJACENT_DIST * 1.5

    def _is_satisfied_separated(self, coords: np.ndarray, si: int, ti: int) -> bool:
        dist = float(np.linalg.norm(coords[si] - coords[ti]))
        return dist >= SEPARATION_DIST * 0.8

    def _is_satisfied_in_between(self, coords: np.ndarray, ai: int, bi: int, ci: int) -> bool:
        midpoint = (coords[bi] + coords[ci]) / 2.0
        dist = float(np.linalg.norm(coords[ai] - midpoint))
        return dist <= ADJACENT_DIST

    def _is_satisfied_travel_path(self, coords: np.ndarray, indices: list[int]) -> bool:
        if len(indices) < 3:
            return True
        pts = coords[indices]
        v_main = pts[-1] - pts[0]
        main_len = float(np.linalg.norm(v_main))
        if main_len < 1e-6:
            return True
        v_norm = v_main / main_len
        for i in range(len(indices) - 1):
            if float(np.dot(pts[i + 1] - pts[i], v_norm)) < 0:
                return False
        return True

    def _is_satisfied_cluster(self, coords: np.ndarray, si: int, ti: int) -> bool:
        dist = float(np.linalg.norm(coords[si] - coords[ti]))
        return dist <= CLUSTER_DIST * 1.2

    def _calculate_satisfaction(self, coords_2d: np.ndarray) -> dict:
        """Post-solve constraint satisfaction metrics."""
        by_type: dict[str, dict] = {}
        constrained_locs: set[str] = set()
        satisfied_locs: set[str] = set()

        for c in self.constraints:
            si = self.loc_index.get(c["source"])
            ti = self.loc_index.get(c["target"])
            if si is None or ti is None:
                continue
            rtype = c["relation_type"]

            if rtype not in by_type:
                by_type[rtype] = {"total": 0, "satisfied": 0}
            by_type[rtype]["total"] += 1
            constrained_locs.add(c["source"])
            constrained_locs.add(c["target"])

            # Dispatch satisfaction check by type
            satisfied = False
            if rtype == "direction":
                satisfied = self._is_satisfied_direction(coords_2d, si, ti, c["value"])
            elif rtype == "distance":
                satisfied = self._is_satisfied_distance(coords_2d, si, ti, c["value"], c.get("distance_class"))
            elif classify_spatial_relation(rtype) == "hierarchy":
                satisfied = self._is_satisfied_contains(coords_2d, si, ti)
            elif rtype == "adjacent":
                satisfied = self._is_satisfied_adjacent(coords_2d, si, ti)
            elif rtype == "separated_by":
                satisfied = self._is_satisfied_separated(coords_2d, si, ti)
            elif rtype == "in_between":
                ci = self.loc_index.get(c.get("value", ""))
                if ci is not None:
                    satisfied = self._is_satisfied_in_between(coords_2d, si, ti, ci)
                else:
                    satisfied = True  # missing third point → vacuously satisfied
            elif rtype == "travel_path":
                wps = c.get("waypoints") or []
                indices = [si]
                for wp in wps:
                    wi = self.loc_index.get(wp)
                    if wi is not None:
                        indices.append(wi)
                indices.append(ti)
                satisfied = self._is_satisfied_travel_path(coords_2d, indices)
            elif rtype == "cluster":
                satisfied = self._is_satisfied_cluster(coords_2d, si, ti)

            if satisfied:
                by_type[rtype]["satisfied"] += 1
                satisfied_locs.add(c["source"])
                satisfied_locs.add(c["target"])

        total_constraints = sum(v["total"] for v in by_type.values())
        satisfied_constraints = sum(v["satisfied"] for v in by_type.values())

        for v in by_type.values():
            v["satisfaction"] = v["satisfied"] / v["total"] if v["total"] > 0 else 1.0

        constrained_in_solver = constrained_locs & set(self.loc_names)

        return {
            "total_satisfaction": satisfied_constraints / total_constraints if total_constraints > 0 else 1.0,
            "by_type": by_type,
            "constrained_locations": len(constrained_in_solver),
            "unconstrained_locations": self.n - len(constrained_in_solver),
            "total_constraints": total_constraints,
            "satisfied_constraints": satisfied_constraints,
            "constrained_location_names": list(satisfied_locs),
        }

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

    # Cardinal direction → canvas position (normalized 0-1 coords)
    _CARDINAL_POS: ClassVar[dict[str, tuple[float, float]]] = {
        "east":  (0.80, 0.50),
        "west":  (0.20, 0.50),
        "north": (0.50, 0.20),
        "south": (0.50, 0.80),
    }

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

        # Cardinal direction-aware placement: roots with direction hints
        # (e.g., 东胜神洲→east) are placed at cardinal canvas positions
        # instead of a tight sunflower circle.
        cardinal_roots: list[str] = []
        non_cardinal_roots: list[str] = []
        for name in unplaced_roots:
            hint = self._direction_hints.get(name)
            if hint in self._CARDINAL_POS:
                cardinal_roots.append(name)
            else:
                non_cardinal_roots.append(name)

        for name in cardinal_roots:
            hint = self._direction_hints[name]
            fx, fy = self._CARDINAL_POS[hint]
            layout[name] = (
                self._canvas_min_x + fx * w,
                self._canvas_min_y + fy * h,
            )

        # Remaining roots: sunflower seed around center
        radius = min(w, h) * 0.2
        golden_angle = math.pi * (3 - math.sqrt(5))  # ≈ 137.5°
        n_roots = max(len(non_cardinal_roots), 1)
        for i, name in enumerate(non_cardinal_roots):
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
        canvas_w = self._canvas_max_x - self._canvas_min_x
        canvas_h = self._canvas_max_y - self._canvas_min_y
        for parent in parents:
            children = self.children.get(parent, [])
            if not children:
                continue
            px, py = layout.get(parent, (self._canvas_cx, self._canvas_cy))
            n = len(children)
            # Adaptive radius: scale with sqrt(n_children) for better spread
            effective_radius = child_radius * max(1.0, math.sqrt(n / 5))
            effective_radius = min(effective_radius, min(canvas_w, canvas_h) * 0.3)
            for i, child in enumerate(children):
                if child in layout:
                    continue
                # Sunflower seed distribution: fills circle area organically
                frac = (i + 0.5) / n
                r = effective_radius * (0.3 + 0.7 * math.sqrt(frac))
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
        parsed_constraints: list[tuple[int, int, str, str, float, list[str] | None]] = []
        for c in constraints:
            si = self.loc_index.get(c["source"])
            ti = self.loc_index.get(c["target"])
            if si is None or ti is None:
                continue
            cs = c.get("confidence_score")
            weight = max(cs * 3.0, 0.3) if cs is not None else _CONF_RANK.get(c.get("confidence", "medium"), 2)
            parsed_constraints.append(
                (si, ti, c["relation_type"], c.get("value", ""), weight, c.get("waypoints"))
            )

        # Seeded RNG local to this routine. The repulsion loop below used the
        # global np.random, which advanced shared state on every call: identical
        # inputs produced different layouts each run (measured 53/690 placements,
        # 7.7%, moving between two calls in the SAME process).
        rng = np.random.RandomState(42)

        # Run 80 iterations of spring-force simulation
        velocities = np.zeros_like(positions)
        damping = 0.85
        dt = 1.0

        for _ in range(80):
            forces = np.zeros_like(positions)

            # ── Attraction: constraints pull locations toward satisfaction ──
            for si, ti, rtype, value, weight, wps in parsed_constraints:
                diff = positions[ti] - positions[si]
                dist = np.linalg.norm(diff)
                if dist < 1e-6:
                    continue
                direction = diff / dist

                if classify_spatial_relation(rtype) == "hierarchy":
                    # Pull child toward parent if too far
                    radius = self._get_parent_radius(si)
                    if dist > radius:
                        force_mag = (dist - radius) * 0.1 * weight
                        forces[ti] -= direction * force_mag
                        if not fixed[si]:
                            forces[si] += direction * force_mag * 0.3
                elif rtype in ("adjacent", "travel_sequence"):
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
                elif rtype == "cluster":
                    if dist > CLUSTER_DIST:
                        force_mag = (dist - CLUSTER_DIST) * 0.05 * weight
                        forces[si] += direction * force_mag
                        forces[ti] -= direction * force_mag
                elif rtype == "travel_path" and wps:
                    # Nudge waypoints to maintain topological order along s→t
                    indices = [si]
                    for wp in wps:
                        wi = self.loc_index.get(wp)
                        if wi is not None:
                            indices.append(wi)
                    indices.append(ti)
                    if len(indices) >= 3:
                        s_pos = positions[indices[0]]
                        t_pos = positions[indices[-1]]
                        v_main = t_pos - s_pos
                        ml = np.linalg.norm(v_main)
                        if ml > 1e-6:
                            v_n = v_main / ml
                            for idx_i in range(len(indices) - 1):
                                seg = positions[indices[idx_i + 1]] - positions[indices[idx_i]]
                                proj = float(np.dot(seg, v_n))
                                if proj < 0:
                                    nudge = v_n * abs(proj) * 0.03 * weight
                                    forces[indices[idx_i + 1]] += nudge
                                    forces[indices[idx_i]] -= nudge

            # ── Repulsion: O(n²) pairwise repulsion ──
            for i in range(n):
                for j in range(i + 1, n):
                    diff = positions[j] - positions[i]
                    dist = np.linalg.norm(diff)
                    if dist < 1e-6:
                        dist = 1e-6
                        diff = rng.randn(2) * 1e-6
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
    # Warm parchment palette: center values are neutral/warm,
    # green/teal only appears at high moisture (near water/garden locations).
    # e=0.0  (lowland): warm sand → warm → olive → green → teal
    [(215, 200, 160), (200, 195, 150), (165, 180, 125), (120, 160, 95), (100, 148, 120)],
    # e=0.25: warm tan → sandy → light olive → forest → wetland
    [(210, 198, 158), (198, 192, 148), (170, 180, 130), (130, 162, 100), (108, 145, 118)],
    # e=0.5:  parchment → neutral → subtle olive → moderate → green-gray
    [(205, 195, 160), (195, 190, 152), (180, 182, 140), (150, 170, 115), (125, 152, 112)],
    # e=0.75: cool parchment → gray-warm → gray → dark olive → dark
    [(188, 180, 158), (175, 168, 150), (158, 160, 135), (130, 145, 110), (108, 128, 100)],
    # e=1.0  (peak): bright / snow
    [(240, 238, 230), (238, 235, 228), (235, 233, 225), (232, 230, 222), (228, 226, 220)],
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


def _stable_seed(text: str) -> int:
    """Deterministic 31-bit seed — `hash()` on a str is randomized per process.

    PYTHONHASHSEED makes builtin `hash()` differ between runs, so
    `hash(novel_id)` re-baked a different noise field on every backend restart:
    the same novel came back with different terrain after a restart, and no
    before/after bake could be compared. md5 is the same deterministic hashing
    `compute_chapter_hash` already uses for the layout cache key.
    """
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16) % (2**31)


# Ground field calibration, in field units. The bounded field below spans about
# 0.10-0.86 across the six novels measured, so a window of 0.20-0.70 puts its
# mass across the whole 4x4 Whittaker lookup instead of the middle of it. Frozen
# rather than taken from each novel's own percentiles so that two novels are
# comparable and a per-tile renderer — which sees one screenful, not the canvas —
# can reproduce it from the tile alone.
_FIELD_WINDOW = (0.20, 0.70)


# The influence field is evaluated on a grid this many samples wide on the long
# side and then upsampled. Every term's radius is a fraction of the raster's own
# long side (0.12-0.22 of it), so the field has no structure finer than ~1/5 of
# the raster — 512 samples resolve it with an order of magnitude to spare.
# This is what makes the cost independent of the requested bake size: at 1024 the
# full-resolution form was already 0.75 s, and at 4096 it would have been 16x
# that (~12 s) purely to interpolate a field that is smooth by construction.
_INFLUENCE_SAMPLES = 512


def _bounded_influence(
    img_w: int,
    img_h: int,
    terms: list[tuple[list[tuple[float, float]], float, float]],
) -> np.ndarray:
    """Location influence that cannot grow with the number of points.

    `elev[mask] += 0.25 * (1 - d/r)`, run once per point in range, is an
    accumulator rather than a field. On 西游记 the 127 mountain locations put a
    mean of 60 — a maximum of 76 — of themselves inside one another's 1440-unit
    radius, so a single cluster core collected 76 x 0.25 = 19.0 of elevation,
    the pre-clip field reached 12.4, and `np.clip` discarded 36 % of the
    elevation field and 52 % of the moisture field at the ceiling. A saturated
    field is a flat field: the two whitest Whittaker cells covered 40.4 % of
    that canvas, and the window under the camera at zoom k=10 had elevation
    range [1.00, 1.00] — which is what a client-rendered ground tile turned out
    to be, a pale wash, before this was found.

    So each sign keeps its strongest contribution rather than summing them —
    `max` over the positive terms, `min` over the negative — and the two are
    added, which leaves a mountain beside a lake showing both. The local value
    is then bounded by the strongest single point by construction, whatever the
    point count.
    """
    from scipy.ndimage import zoom

    spacing = max(1, round(max(img_w, img_h) / _INFLUENCE_SAMPLES))
    gw = max(2, img_w // spacing + 1)
    gh = max(2, img_h // spacing + 1)
    # Raster coordinates of the coarse grid's own samples — the terms are stored
    # in raster coordinates, so the grid has to be built in the same space.
    ys_grid, xs_grid = np.mgrid[0:gh, 0:gw].astype(np.float64)
    ys_grid *= spacing
    xs_grid *= spacing

    pos = np.zeros_like(xs_grid)
    neg = np.zeros_like(xs_grid)
    for pts, radius, amp in terms:
        if not pts:
            continue
        target = pos if amp > 0 else neg
        for px, py in pts:
            dist = np.sqrt((xs_grid - px) ** 2 + (ys_grid - py) ** 2)
            contrib = np.where(dist < radius, amp * (1.0 - dist / radius), 0.0)
            if amp > 0:
                np.maximum(target, contrib, out=target)
            else:
                np.minimum(target, contrib, out=target)
    field = pos + neg
    if spacing == 1:
        return field[:img_h, :img_w]
    up = zoom(field, (img_h / gh, img_w / gw), order=1)
    return up[:img_h, :img_w]


def _spread_unit(field: np.ndarray) -> np.ndarray:
    """Map `_FIELD_WINDOW` onto 0-1 for the Whittaker lookup.

    Bounding the influence is not enough on its own. Measured across six novels,
    the bounded-but-unspread field used only 9-12 of the 16 reachable Whittaker
    cells, with entropy 2.4-2.7 bit and one cell taking 30-38 % of the canvas,
    because its mass sits in the middle of the range. Applying the window:
    16/16 cells on all six, entropy 3.7-3.9 bit, top cell 10.8-13.2 %.
    """
    lo, hi = _FIELD_WINDOW
    return np.clip((field - lo) / (hi - lo), 0.0, 1.0)


# Bump this when the terrain field recipe changes. `_LAYOUT_VERSION` covers the
# layout JSON; the baked PNG is a separate artifact with a separate cache, and
# it carries no version of its own — so a recipe change used to leave the old
# PNG on disk being served as the current one. Measured, not hypothetical: the
# field fix in this same change left `terrain.png` untouched at 293168 bytes
# (sd 8.01) while the new recipe produced sd 20.88, and nothing in the request
# path noticed. The filename and the URL both carry the version, so a recipe
# bump invalidates the disk cache and every client's cached copy at once.
# v6: relief/texture/flat-noise strengths recalibrated against a composed A/B on
# the real map. v5 reached v2's fine detail and lost the land/sea separation
# while painting +-60 % relief over the labels — a regression that the bare
# raster's own statistics scored as an improvement.
# v7: the landform recipe. One ridged height field, Lambert-shaded, with the
# palette ramped over the same height instead of a Whittaker lookup over a
# different field. See the _SHAPE comment for why no amount of dial-turning on
# v6 could have got there.
# v10: the detail floor. The recipe's finest ridge was 64 canvas px, so at z2 and
# z3 the ground was one soft hump per screen and the picture read as mediocre
# even though the fit view was acceptable. A fourth `_RIDGE_SCALES` entry takes
# the floor to 24 canvas px and `_TERRAIN_MAX_SIZE` goes to 4096 so there is
# resolution under it. Both halves in one bump because either alone is wasted:
# detail without raster is interpolated away, raster without detail is an
# expensive wash. This is also the bump that retires the last of the
# areal-cover reasoning on the symbol layer, but that layer is not cached.
# v9: v8's plains kept too little relief, and a province at _PLAIN_FLOOR 0.22
# rendered as a large featureless pale patch -- a hole in the map rather than a
# plain. 0.34 keeps a texture there while the crests still clearly win.
# v8: v7's value and scale. Structure was right and the picture still read as
# heavy: the land came out a dark rust wash that labels had to fight, and the
# ridges were fine enough to read as grain at fit zoom. Land is now held in the
# light half of the range with the mass pushed into the lowlands, shading
# modulates instead of dominating, and the octave falloff is shallower so macro
# form wins at fit.
_TERRAIN_VERSION = 16


def terrain_path_for(novel_id: str) -> Path:
    """Where a novel's baked terrain lives. The writer and all readers call this.

    Keeping the path in one function is the point: when the writer and the route
    hard-coded the same literal and the recipe changed, the route kept serving a
    PNG the old recipe had produced, and the response was indistinguishable from
    a correct one.
    """
    return DATA_DIR / "maps" / novel_id / f"terrain.v{_TERRAIN_VERSION}.png"


def terrain_url_for(novel_id: str) -> str:
    """The URL clients fetch the terrain from. Versioned for the same reason."""
    return f"/api/novels/{novel_id}/map/terrain/v{_TERRAIN_VERSION}"


# Ceiling for the terrain bake, in pixels on its LONG side. The bake is stretched
# over the whole canvas, so its resolution is the ceiling on every zoomed-in
# detail the map can ever show. It used to be a flat 1024: on the 8000x4500
# overworld canvas that is upsampled 7.8x, which is why the biome field read as
# soft blobs at exactly the zoom the map exists to support (scaleExtent runs to
# k=10, i.e. one canvas pixel per screen pixel).
#
# The old ceiling was a CPU ceiling, not a size one — the Python simplex loop
# below cost 5.7 s at 1024 and grew with the square of the size, and a 4096 bake
# was killed for memory long before it was tried. Both are gone now that the
# field is vectorised, so the ceiling is set by what the image costs to ship:
#
#   size   bake s   PNG MB   upscale    MPx    (8000x4500 canvas)
#    1024     0.89     0.53     7.81x    0.6
#    2048     2.81     2.11     3.91x    2.4
#    3072     6.83     4.77     2.60x    5.3
#    4096    15.12     8.49     1.95x    9.4
#    6144    32.60    19.12     1.30x   21.2   <- rejected: 19 MB and 21 MPx
#
# And having paid for all that, the resolution turned out not to be the binding
# constraint. A rendered A/B of the same recipe at 1024 and 4096 -- same field,
# only the raster differing, confirmed by cross-correlation 0.9996 on the two
# downsampled to a common size -- measured a high-frequency gain of 1.00-1.07x
# ACROSS THE WHOLE ZOOM RANGE, from fit to 2 canvas px per screen px. The reason
# is the field's own spectrum: 99 % of its power sits above a wavelength of 320
# canvas px while even a 1024 raster resolves 15.6. The bake was twenty times
# oversampled and no amount of megapixels could show it.
#
# So this is a headroom ceiling, not a quality dial. 2048 leaves the raster's own
# Nyquist at 3.9 canvas px, comfortably finer than the ~30 px floor the field
# below can produce even with its octaves at full count, and it keeps the image
# at 2 MB. Re-derive this number if the detail budget changes: the test is
# `2 * canvas_long_side / size <= finest_wavelength / 2`.
#
# A later measurement narrowed the claim above rather than overturning it. It
# holds at fit zoom, where one bake texel covers 0.58 screen px, and it stops
# holding deep in the range: at 4.8x a texel covers 2.78 screen px and the bake
# is genuinely soft. But raising the size does not cure that, because the layer
# has almost nothing at those scales to resolve. Toggling the layer off and on
# and taking the residual (no mask, so nothing can hide in the choice of window)
# gives, in screen-px bands at fit / 2.2x / 4.8x:
#
#   band    4-8     8-16   16-32   32-64   64-200
#   fit     2.08    2.59    3.24    4.01     5.29
#   2.2x    1.72    2.13    2.58    3.32     6.73
#   4.8x    1.36    1.55    1.94    2.48     5.45
#
# The layer is a broad tonal wash, strongest by a factor of two at 64-200 px, and
# its fine end FALLS as it is zoomed — that is upsampling, exactly as predicted.
# A bigger raster would push that knee out, not remove it, and would cost 8 MB and
# 13 s for a wash. The thing that would put real detail at deep zoom is content in
# the field at those scales, which is a different change from this one.
#
# ── 2048 -> 4096 (v10) ────────────────────────────────────────────────────
# The paragraph above named the precondition: a bigger raster for a wash is
# waste. v10 changes the other half first -- `_RIDGE_SCALES` gains a fourth scale
# reaching 24 canvas px, so there is now content down at the resolution being
# bought -- and then this number follows, because the two only work together.
#
# Re-derived rather than picked: the test stated above is
# `2 * canvas_long_side / size <= finest_wavelength / 2`, which with the new
# floor is `2 * 8000 / size <= 12`, i.e. `size >= 1333`. That would leave 2048
# standing, so the binding number is not Nyquist but the one the later
# measurement found: at 4.8x a texel covers `4.8 * 0.58 * (2048 / size)` screen
# px, and the bake only stops being visibly soft below about 1.5.
#
#   size   texel @4.8x   full bake   memory
#   2048      2.78 px       2.81 s     2.4 MB
#   3072      1.86 px       6.83 s     5.3 MB
#   4096      1.39 px      15.12 s     9.4 MB
#   6144      0.93 px      32.60 s    21.2 MB   <- still rejected
#
# 4096 is the last row inside a single-digit-MB budget and the first one that
# clears the softness threshold, so it is the one that is paid for. The cost is
# once per recipe change -- the bake is cached against `_TERRAIN_VERSION` -- not
# per launch.
_TERRAIN_MAX_SIZE = 4096

# ── The field's detail budget ──
# These, not the raster, decide how fine the terrain reads. The shipped values
# were three octaves per field, stopping at 224 canvas px on the 8000 px
# overworld — blobs a thirty-sixth of the map wide — which is why the result read
# as soft however sharp the bake was. Octaves are added at half the wavelength
# each, so the floor moves to ~30 canvas px and the raster still resolves it.
# The class fields stay at three octaves: every octave in them becomes a biome
# boundary, so more octaves means more, smaller patches, which is the opposite of
# readable. The detail goes into `_ELEV_OCTAVES`, which only shades.
_CLASS_OCTAVES = 3
_MOIST_OCTAVES = 3         # 1560 -> 390 canvas px, coherent moisture bands
_RELIEF_OCTAVES = 5        # 1952 -> 122 canvas px: slopes, not grain
# The grain field starts at its own high base rather than running the full
# fractal and being high-passed afterwards: _fbm always begins at the largest
# scale, so a 7-octave texture would carry a +/-1 low-frequency component and
# dim the map in patches. Starting at 244 canvas px keeps it to grain.
_TEXTURE_BASE_WL = 0.0305  # 244 canvas px on the overworld
_TEXTURE_OCTAVES = 4       # down to 30 canvas px
_VARIATION_OCTAVES = 4     #  784 ->  98 canvas px

# Strength of the relief shading as a fraction of the base colour. 0 disables it.
#
# 0.30 was the first attempt and it was a regression, shipped-in-measurement
# visible only in a composed A/B: with the slope clipped at +-2 sigma, 0.30 is a
# +-60 % modulation of the base colour. At 0.4 opacity on the map that is a
# +-24 % swing laid over the region colours, place labels and roads — the reader
# sees blotches, and reads them as dirt, not as landform. Isolating the terms one
# at a time on the real map (relief+texture off = clean, relief on = dirty)
# pinned the cause here and not on the flat noise terms, on the blur, or on the
# raster size. 0.10 keeps a slope readable and stays under the +-18 RGB the
# pre-existing colour variation already spent.
_RELIEF_GAIN = 0.10
# How much the finest detail perturbs the colour without being shaded. Kept
# separate from the relief so grain cannot masquerade as landform. 0.03 with the
# relief above is the measured point where texture is present and the labels are
# untouched; 0.10 was not.
_TEXTURE_AMPLITUDE = 0.03

# ── A dedicated fine brightness channel was tried here and reverted (v17-v19).
#    Do NOT re-add it. ──
#
# The reasoning: the deep-zoom ground is the worst view in the product (at
# k=9.75 the ridge field's finest 24 canvas px is 234 screen px wide, so the
# reader gets a wash), and `_TEXTURE_AMPLITUDE` above is already a brightness
# channel that never enters `_hillshade` — simply too coarse and too weak. So: a
# dedicated term at 13 canvas px base, 2 octaves, finest 6.5 px.
#
# Measured, in three steps:
#
#   amplitude 0.05   land mean |dRGB| **1.07**  — five times under the 7.17
#                    same-recipe bake-to-bake noise floor. No change at all.
#   amplitude 0.50   **12.03** — so the channel is real and LINEAR in amplitude
#                    (10x amplitude, 11x effect). It is not being swallowed.
#   amplitude 0.35   **8.06**, i.e. just above the noise floor — and here the
#                    costs and the benefits separate cleanly:
#
#                      bake hf, every scale      4.02/4.01/4.53/7.57
#                                          ->    4.33/4.34/5.19/8.86   UP
#                      deep-zoom hf(5)           0.65 -> 0.69           UP
#                      deep-zoom luminance std  12.56 -> 15.20          UP
#                      land/sea dL               53.5 -> 48.1          DOWN 10%
#                      land/sea contrast         1.70 -> 1.63           DOWN
#
# and the picture at k=9.75 is still a featureless green wash in both. The
# numbers rise, the gain is invisible, and the price is real — `dL` is this
# map's V1 acceptance criterion and it is not for sale at that rate.
#
# The mechanism: the field's effective deviation is far below the unit sd the
# docstring claims, so a visible modulation needs an amplitude near 1.0, at
# which the multiplicative form clips and drags the land's mean around. Additive
# would hold the mean but the visible gain was already zero at 0.35.
#
# Three routes to a finer bake are now measured and closed: a finer
# `_RIDGE_SCALES` entry (see that table), and this channel, at both ends of its
# amplitude. **Deep zoom is a frontend problem, not a bake problem**: what is
# needed is ground texture that is resolution-independent — the screen-pitched
# glyph layer, or a procedural overlay drawn at the current scale.

# The two flat noise terms that predate the octave budget, now named because they
# are a large share of the terrain's visible contrast and were previously
# unnamed literals inside the bake. `_fbm` returns a zero-mean unit-sd field
# while `variation` here is a three-octave 0.5/0.3/0.2 sum, so the effective
# swing of `_VARIATION_STRENGTH` is about 0.55 x the number: 30 was +-18 RGB at
# 781 canvas px, i.e. 115 screen px at fit zoom, which is blotch scale rather
# than texture scale and was a second, smaller contributor to the same dirt.
# Their wavelengths are the old per-raster-pixel frequencies converted to
# fractions of the long side (100/33/12.5 px of a 1024 raster = 0.098/0.033/
# 0.012; 8.33 px = 0.0081) — that conversion is what keeps the field
# resolution-independent, and it is correct.
_VARIATION_STRENGTH = 6    # +-3 RGB at 781 -> 98 canvas px
_PAPER_STRENGTH = 0        # grain read as noise on a map; kept as a dial

# Final painterly blur, as a fraction of the long side. Was a flat sigma of 4
# raster px, i.e. 0.0039 of the 1024 raster it was tuned on = 31 canvas px on the
# overworld, which is wider than every octave added above and would have erased
# them. 0.0008 is 6.4 canvas px: enough to keep the biome lookup's borders from
# looking like a contour map, not enough to be the detail ceiling.
_BLUR_FRAC = 0.0008

# ── Landform shape ────────────────────────────────────────────────────
#
# "biome" is everything above: the class field through the Whittaker table, plus
# a slope nudge from a second, unrelated draw of the same seed. It is kept
# because it is measurable and because a recipe change should be reversible, but
# it does not draw landform:
#
#   Its colour comes from a BIOME table, which answers "what grows here", and it
#   has no hillshade. The relief term is +-10 % of the base colour, and it is
#   driven by a DIFFERENT field from the one that picks the colour, so nothing
#   ties "this pixel is high" to "this pixel is coloured like high ground".
#   Rendered at 2x the result is camouflage: three flat colours in amorphous
#   blobs with a faint directional streak on top. Every dial that was turned
#   against it -- relief gain, texture amplitude, blur, raster size -- moved a
#   number without moving that read, because the field being tuned has no
#   structure for a gradient to find.
#
# "ridged" draws one height field, shades it, and ramps the palette over that
# same height:
#
#   A ridged sum (1-|n|) turns smooth extrema into connected crest lines, so the
#   gradient has ridges to shade and valleys to leave dark. Lambert shading from
#   the upper left then gives the slopes their lit and shadowed faces, and the
#   palette runs sand -> grass -> scree -> rock -> snow with height, so elevation
#   is legible as colour and as relief at once. Moisture tilts the low ground
#   green instead of ochre.
#
# This is the standard game-map recipe, and the reason it is worth the rewrite is
# that it is the only variant of eight that was tried and LOOKED at that reads as
# terrain rather than as texture.
_SHAPE = "ridged"

# Octaves for the ridged field. Base wavelength stays at the class field's
# 0.244 (1952 canvas px) so the ranges land where the regions are; seven octaves
# reach 30 canvas px, which is the finest detail the reader can resolve at fit.
_RIDGE_OCTAVES = 4
_RIDGE_BASE_WL = 0.244
# Three ridged fields at three base wavelengths, summed. One field at one
# wavelength gives one cell size, and one cell size repeated across the canvas
# reads as texture -- a canopy, or broccoli -- however good the individual cells
# look. Real ground is hierarchical: a few massifs, ridges off them, hills off
# those. Each entry is (base wavelength, octaves, amplitude), the wavelengths a
# factor of ~2.8 apart so the three scales are distinguishable rather than
# stacking into one band.
#
# ── The fourth entry, and why the first three were not enough (v10) ──
# The three scales bottom out at 256 canvas px with three octaves, i.e. a finest
# ridge of 64 canvas px. At fit zoom that is 12 screen px and reads as ground.
# At the deep zoom it is 96 screen px -- a single soft hump with nothing inside
# it -- which is what the reader was calling "still mediocre" at z2 and z3 while
# the fit view looked acceptable. The cap on detail was the field, not the
# raster: 99 % of its power sat above 320 canvas px and the bake was twenty
# times oversampled for it (see the resolution table above).
#
# The fourth scale is 96 canvas px at base, reaching 24 canvas px. Amplitude is
# 0.06 rather than the ~0.10 the 2.8x spacing would suggest, because at fit zoom
# 24 canvas px is 4.5 screen px and anything heavier there reads as grain on the
# paper rather than as hills. At z2 and above the same feature is 36-72 screen
# px, which is where it stops being grain and starts being a hill -- the detail
# is present at every zoom and only becomes legible where it can be.
_RIDGE_SCALES: tuple[tuple[float, int, float], ...] = (
    (0.244, 4, 1.00),      # 1952 canvas px: where the ranges are
    (0.085, 4, 0.42),      #  680 canvas px: ridges off them
    (0.032, 3, 0.17),      #  256 canvas px: hills off those
    (0.012, 3, 0.06),      #   96 canvas px: the ground surface itself
)
# ── A fifth scale was tried twice and reverted. Do NOT re-add it without
#    changing how the shading is normalised. ──
#
# The deep-zoom ground is the worst view in the product: at k=9.75 the reader
# gets a green-to-grey gradient with scattered triangles and no landform in the
# frame. The reasoning that suggests a finer scale is sound — the bake is 4096
# across an 8000-unit canvas (1.95 canvas units per texel), so the raster can
# carry detail down to ~2 canvas px while the *field* stops at 24, i.e. 234
# screen px at that zoom. A 28 canvas px entry with a 7 px finest octave should
# fill exactly that gap.
#
# It does not, for two measured reasons:
#
#   1. **Amplitude 0.022 did nothing at all.** The ridge sum is normalised
#      through `_own_unit(ridge, _HEIGHT_WINDOW)`, and 0.022 against a total of
#      1.00 + 0.42 + 0.17 + 0.06 is 1.3 % of the sum, which the percentile
#      normalisation then compresses further. The bake moved by mean |dRGB|
#      **1.42** over land, against a same-recipe bake-to-bake noise floor of
#      **7.17** — five times smaller than the noise.
#
#   2. **Amplitude 0.22 moved it, in the wrong direction.** `_hillshade`
#      normalises the gradient against its own p95, so a large fine octave takes
#      over that budget: `s` rises, every shading value is scaled down, and the
#      map's visible texture is what comes from shading. Measured on the bake,
#      high-frequency energy FELL at every scale (hf k=3: 4.02 -> 1.98, k=5:
#      4.01 -> 1.97, k=9: 4.53 -> 2.31), while at deep zoom the luminance
#      spread halved (14.48 -> 7.95) — a flatter ground, not a more detailed one.
#
# So the fine scale is not the lever: the shading has one gradient budget and
# detail finer than it can carry simply steals it. A real fix has to shade each
# scale against its own normaliser, or add a fine-scale channel that never
# enters `_hillshade`. Both are changes to the recipe's structure, not to this
# table.
# Amplitude falloff per octave within one scale.
_RIDGE_PERSISTENCE = 0.5
# How strongly a crest at one octave invites detail at the next. Above 1 the
# detail crowds onto the ranges and the lowlands go completely smooth; too low
# and every octave ignores the last, which is the filament topology described in
# `_ridged`.
_RIDGE_WEIGHT_GAIN = 1.4
# How mountainous each region is, as a low-frequency mask. Without it every
# region gets the same treatment and the map has no macro reading -- the reader
# cannot tell a mountain province from a plain, which is precisely the thing a
# world map is for.
#
# The wavelength is the thing, and 0.10 was the wrong one. 0.10 of the raster is
# ~800 canvas px, which at fit zoom is **~144 screen px** — under a tenth of the
# frame — so the provinces were finer than the continents they sit inside and
# averaged out into one texture before the reader could see them. Four separate
# attempts were then spent trying to make those too-small provinces more
# different (opacity, a height bias, a moisture bias, a second ramp), and all
# four failed; in hindsight they were all trying to fix the wrong axis.
#
# Measured at the raster (`scripts/probe_bake_variants.py`), between-block std
# divided by within-block std — >1 means two provinces differ more than one
# province's own texture does. ⚠️ The block size is in RASTER px, and the raster
# is 4096 across a canvas that fit zoom draws at ~1439 screen px: **1 screen px
# = 1.39 raster px**, so 128 screen px is block 178 here. Comparing a raster
# block against a screen block of the same number is comparing two scales, and
# it produces two criteria that disagree in sign.
#
#                 blk178 (~128 screen)      blk356 (~256 screen)
#   0.10 (was)    0.489  0.775  0.720        0.284  0.413  0.387
#   0.30 (v15)    0.554  0.884  0.861        0.305  0.484  0.478
#   0.30 + blend  0.571  0.969  0.945        0.310  0.463  0.538   <- shipped
#
# Comfortably monotone at the scale the reader actually has: each step raises
# luminance, saturation and warm-cool together. (At blk128/256, i.e. 45/92
# screen px, the same ordering holds; those are the finer scales, well inside a
# province.) The criterion keeps rising as the field coarsens, so it cannot pick
# a wavelength — 0.30 was chosen on the eye, on the full map and at 2x on two
# continents.
#
# On the composite: land/sea separation held (dL 51.0 -> 53.5, contrast
# 1.67 -> 1.70), the structure metric rose (coarse/fine 0.451 -> 0.527), and
# label legibility is untouched (optical loss still 0.0% median).
_RELIEF_MASK_WL = 0.30
_RELIEF_MASK_OCTAVES = 3
# Amplitude multiplier where the mask is at its lowest. Not 0: a province with
# no relief at all has no texture either, and a flat colour patch on a map this
# size reads as a hole rather than as a plain.
_PLAIN_FLOOR = 0.34
# NOTE (2026-09-28): a provincial MEAN shift was tried here, twice, and both
# variants were reverted on their own pre-registered criterion. Recorded because
# the failure is structural, not a matter of tuning the constant.
#
# The map is statistically homogeneous over land. Measured at fit zoom on
# 西游记, 128 px blocks, between-block std divided by within-block std — 1.0
# would mean two provinces differ as much as one province's own texture does:
#
#     v10 baseline   luminance 0.179   saturation 0.303   warm-cool 0.522
#     height bias    luminance 0.223   saturation 0.163   warm-cool 0.389
#     moisture bias  luminance 0.251   saturation 0.181   warm-cool 0.416
#
# (the provincial field is `_RELIEF_MASK_WL`; it was only ever used for ridge
# AMPLITUDE, so a province varies in roughness while keeping the same mean)
#
# The luminance ratio rises every time and the other two channels never recover
# to baseline, because **both differentiation mechanisms are bounded and
# saturate**:
#
#   - Height runs into a 1-D ramp whose ends are its least saturated stops
#     (light sand at 0.00, snow at 0.97-1.00), so moving provinces toward
#     either end trades chroma for value.
#   - Moisture only ever reaches the colour through
#     `clip((moist - 0.5) * 2, -1, 1) * clip(1 - height / 0.62, 0, 1)` — hard
#     saturation, and low ground only. A bias pushes more area into saturation
#     and therefore *compresses* the differences between provinces.
#
# The structural fix — a second ramp blended per province — was then built and
# ALSO failed, and the way it failed is the part worth keeping:
#
#   a two-ramp province blend (v11, v12, v13) measured WORSE than the recipe it
#   replaced on exactly the channel it was built to move. Compared at the RAS
#   level, same metric, same mask rule, same scale, no browser:
#
#     v10 (current)   blk128 lum 0.636  SAT 1.064  WARM 0.960
#     v11             blk128 lum 0.685  sat 0.863  warm 0.881
#     v12             blk128 lum 0.697  sat 0.870  warm 0.819
#     v13             blk128 lum 0.662  sat 0.916  warm 0.806
#
# Note v10's saturation ratio is already ABOVE 1.0 at that scale: the current
# map does differentiate regions in chroma, and the number that said otherwise
# (0.303) came from browser screenshots whose completeness was never verified —
# one of them was missing every label and mark, which removes ink and moves the
# ratio. That is the real lesson: **measure this at the raster, where there is
# no render timing to get wrong.** Compare variants with
# `region_variety()` from `scripts/probe_map_visual.py` applied to the baked
# PNGs directly, and verify a screenshot is complete (91 marks / 47 labels)
# before quoting any number taken from one.
# Height is pushed toward the lowlands before the palette is applied, which is
# what stops the mid-tones from filling with rock and snow. Set to 1.0 -- off --
# because the ridged field already concentrates its mass low once the crest
# weighting above is in place, and an earlier 1.8 on top of the unweighted field
# collapsed the middle of the ramp and rendered as pale-veined green cells.
_HEIGHT_GAMMA = 1.0

# How much of the height is ridged crest versus broad fBm mass. See the branch
# in generate_terrain: too high and the high ground is a web of filaments, too
# low and there are no crests to shade.
_RIDGE_MIX = 0.45

# Lambert light: upper left, the convention every map and hillshade uses.
_HILLSHADE_AZ = 315.0
_HILLSHADE_EL = 45.0
# Vertical exaggeration, applied after normalising the gradient against its own
# p95 so the value does not drift with raster size or octave count.
_HILLSHADE_EXAG = 1.5
# Shading is a modulation of the ramp colour, not a replacement for it. Floor
# and range together set how far the darkest slope falls below the ramp: 0.62
# means even a fully away-facing face keeps 62 % of its colour, so relief reads
# as form without turning the map into a dark relief model. At 0.42 it did, and
# the mud that produced was the reason the first version of this read as heavy.
_SHADE_FLOOR = 0.58
_SHADE_RANGE = 0.62

# Sand -> grass -> scree -> rock -> snow, held in the light half of the range
# and pushed warm on purpose.
#
# The land is where every label, road and icon goes, so it has to stay a light
# field; the sea is the mid-tone wash that recedes. Warm because the map's own
# parchment base and its region tints are warm, and a cool grey-green terrain
# under them reads as dirt on the paper rather than as ground -- which is what
# the first three attempts at this palette all came out looking like. Low end
# anchored on the map's parchment family, whose lowland cell is (215,200,160);
# this starts lighter than that so the lowlands can carry labels unaided.
_HEIGHT_RAMP: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.00, (243, 234, 207)),
    (0.22, (230, 218, 180)),
    (0.42, (212, 205, 160)),
    (0.62, (191, 188, 150)),
    (0.78, (178, 172, 155)),
    (0.90, (199, 195, 189)),
    (0.97, (240, 241, 242)),
    (1.00, (250, 251, 252)),
)
# A second ramp for the wet provinces: same value ladder stop by stop (within a
# few luminance), hue shifted to green. The value match is the point — a first
# cut that was merely "greener" came out ~19 luminance darker at mid height and
# dropped land/sea separation from 51.0 to 43.5.
#
# This was built once before and reverted. Then it was blended by a province
# field at `_RELIEF_MASK_WL` 0.10, i.e. ~144 screen px, and it measured WORSE
# than the recipe it replaced (saturation ratio 0.916 against 1.064): it was
# spending its whole effect inside a province, where the reader never saw it.
# With the provincial field now at 0.30 (~432 screen px) the blend finally has
# a scale to act on, so it is worth one more look — with the criterion, not on
# the strength of that argument.
_HEIGHT_RAMP_ALT: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.00, (232, 238, 216)),
    (0.22, (214, 226, 190)),
    (0.42, (188, 212, 158)),
    (0.62, (158, 196, 140)),
    (0.78, (140, 178, 146)),
    (0.90, (188, 192, 186)),
    (0.97, (240, 241, 242)),
    (1.00, (250, 251, 252)),
)
# How far, and how decisively. `_PROVINCE_COMMIT` smoothsteps the blend weight
# so a province is arid or wet rather than lukewarm: the raw field concentrates
# near 0.5 and left most of the map halfway between the two ramps.
_PROVINCE_BLEND = 1.0
_PROVINCE_COMMIT = True
# A second ramp for the wet provinces. Same snow line, same overall value
# ladder — deliberately — so the two can be blended without one province
# reading as a hole or a spill. What differs is HUE through the whole low and
# mid range: ochre/straw on the arid ramp, green-grey on this one.
#
# Why this exists, and why it is a second ramp rather than another scalar:
# the map measured as statistically homogeneous at region scale — between-block
# std divided by within-block std came out 0.180 (luminance), 0.302
# (saturation), 0.522 (warm-cool) — and every attempt to fix that with a
# bounded scalar failed. Height cannot carry it (a ramp's two ends are its
# least saturated stops, so moving provinces along it trades chroma for value)
# and moisture cannot either (it only reaches colour through
# `clip((moist-0.5)*2, -1, 1) * clip(1 - height/0.62, 0, 1)`, hard saturation
# on low ground only, so a bias pushes area INTO saturation and compresses the
# very difference it was meant to create).
#
# Blending between two ramps is not bounded in that way: it changes which
# colour a height means, per province.
# How far moisture can swing the low ground from ochre to green, and the RGB
# direction it swings in. Only the low ground: moisture is a lowland concept and
# tinting the snow line green is how a map starts looking arbitrary. Milder than
# the first pass, which pushed lowland reds down far enough to read as bruise.
_MOISTURE_TILT = 0.55
_MOISTURE_LOW_TOP = 0.62
_MOISTURE_TILT_RGB = np.array([-28.0, 14.0, -4.0])
# The height window used to spread the ridged field onto 0-1. Its own percentiles
# rather than _FIELD_WINDOW: that window is calibrated to an fBm's mean and tails,
# and applying it to a ridged sum clipped the field to 1.0 nearly everywhere,
# which renders as an all-white map.
_HEIGHT_WINDOW = (1.0, 99.0)


def _hillshade(height: np.ndarray) -> np.ndarray:
    """Lambert shading of `height`, light from the upper left. Returns 0-1.

    The gradient is normalised against its own p95 before the exaggeration is
    applied, so `_HILLSHADE_EXAG` keeps its meaning when the raster size or the
    octave count changes.
    """
    gy, gx = np.gradient(height)
    s = float(np.percentile(np.hypot(gx, gy), 95.0)) or 1.0
    dzdx = gx / s * _HILLSHADE_EXAG
    dzdy = gy / s * _HILLSHADE_EXAG
    nx, ny, nz = -dzdx, -dzdy, np.ones_like(dzdx)
    n = np.sqrt(nx * nx + ny * ny + nz * nz)
    az, el = np.radians(_HILLSHADE_AZ), np.radians(_HILLSHADE_EL)
    lx, ly, lz = np.cos(az) * np.cos(el), np.sin(az) * np.cos(el), np.sin(el)
    # Image y grows downward, so the light's y component is negated to keep the
    # source above the picture rather than below it.
    return np.clip((nx * lx + ny * (-ly) + nz * lz) / n, 0.0, 1.0)


def _ramp_lookup(
    height: np.ndarray,
    moist: np.ndarray,
    province: np.ndarray | None = None,
) -> np.ndarray:
    """Palette as a function of height, tilted green where the ground is wet.

    `province` (0-1, low-frequency) blends toward `_HEIGHT_RAMP_ALT`, so the
    same height means a different colour in a different province.
    """

    def _interp(ramp):
        stops = np.array([p for p, _ in ramp], dtype=np.float64)
        cols = np.array([c for _, c in ramp], dtype=np.float64)
        out = np.empty((*height.shape, 3), dtype=np.float64)
        for ch in range(3):
            out[..., ch] = np.interp(height, stops, cols[:, ch])
        return out

    rgb = _interp(_HEIGHT_RAMP)
    if province is not None and _PROVINCE_BLEND > 0.0:
        w = np.clip(province, 0.0, 1.0)
        if _PROVINCE_COMMIT:
            w = w * w * (3.0 - 2.0 * w)
        w = w * _PROVINCE_BLEND
        rgb = rgb * (1.0 - w)[..., np.newaxis] + _interp(_HEIGHT_RAMP_ALT) * w[..., np.newaxis]
    wet = np.clip((moist - 0.5) * 2.0, -1.0, 1.0)
    low = np.clip(1.0 - height / _MOISTURE_LOW_TOP, 0.0, 1.0)
    tilt = (wet * low * _MOISTURE_TILT)[..., np.newaxis]
    return rgb + tilt * _MOISTURE_TILT_RGB


def _own_unit(field: np.ndarray, window: tuple[float, float]) -> np.ndarray:
    """Spread a field onto 0-1 using its own percentiles, not a shared window.

    `_spread_unit` exists and does the same job, but through `_FIELD_WINDOW`,
    which is calibrated to an fBm's mean and tails. A ridged sum has a different
    mean and a long lower tail; running it through that window clipped it to 1.0
    over almost the whole canvas, which renders as an all-white map.
    """
    lo = float(np.percentile(field, window[0]))
    hi = float(np.percentile(field, window[1]))
    return np.clip((field - lo) / max(hi - lo, 1e-9), 0.0, 1.0)


def terrain_bake_size(canvas_width: int, canvas_height: int) -> int:
    """Raster size for the terrain bake, measured on its long side.

    Matches the canvas where that is affordable and is capped where it is not,
    so the upsampling factor is 1.0 on the smaller overlay canvases (2400x1350)
    and 2.0x on the 8000x4500 overworld instead of a flat 7.8x.
    """
    return int(min(_TERRAIN_MAX_SIZE, max(canvas_width, canvas_height)))


def generate_terrain(
    locations: list[dict],
    layout: dict[str, tuple[float, float]],
    novel_id: str,
    size: int | None = None,
    canvas_width: int = CANVAS_WIDTH,
    canvas_height: int = CANVAS_HEIGHT,
) -> str | None:
    """Generate a terrain PNG using continuous simplex noise fields.

    Instead of Voronoi cells (geometric, hard edges), this uses continuous
    elevation + moisture noise fields → Whittaker biome color lookup for
    every pixel. Location types bias the fields (mountains raise elevation,
    water boosts moisture) so terrain naturally reflects the story geography.

    Final image is Gaussian-blurred for smooth, painterly transitions.

    `canvas_width` / `canvas_height` must be the canvas the caller lays this
    image over — the same numbers it reports to the client as `canvas_size`.
    The image is stretched over that rectangle, so a mismatch between it and
    the layout's own coordinate range shifts every influence point off the
    raster: called with the 1600x900 defaults on an 8000x4500 layout, 0 of 803
    locations landed inside, and the terrain came out as location-independent
    noise that still looked like plausible ground. Both call sites in
    visualisation_service now pass the layout's own canvas, and the guard below
    reports it if that ever stops being true.
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow not installed, skipping terrain generation")
        return None

    if len(layout) < 2:
        return None

    if size is None:
        size = terrain_bake_size(canvas_width, canvas_height)

    # ── Image dimensions (preserve canvas 16:9 aspect) ──
    aspect = canvas_width / max(canvas_height, 1)
    if aspect >= 1:
        img_w = size
        img_h = max(1, int(size / aspect))
    else:
        img_h = size
        img_w = max(1, int(size * aspect))

    scale_x = img_w / canvas_width
    scale_y = img_h / canvas_height

    # ── Classify location influence points ──
    # The name/type/icon → class rule lives in `location_influence` so that both
    # the bake and any comparison script share ONE implementation (a script that
    # re-implements it is a measuring device that lies when either side changes).
    mountain_pts: list[tuple[float, float]] = []
    water_pts: list[tuple[float, float]] = []
    forest_pts: list[tuple[float, float]] = []

    for loc in locations:
        name = loc["name"]
        if name not in layout:
            continue
        x, y = layout[name]
        px = x * scale_x
        py = (canvas_height - y) * scale_y
        classes = influence_classes(name, loc.get("type", ""), loc.get("icon", ""))
        if "mountain" in classes:
            mountain_pts.append((px, py))
        if "water" in classes:
            water_pts.append((px, py))
        if "forest" in classes:
            forest_pts.append((px, py))

    # ── Noise fields ──
    #
    # These used to be `OpenSimplex.noise2` calls in a Python double loop, one
    # per sparse-grid sample. Profiled at 1024 px: 396_288 calls at 13.8 us each
    # = 5.73 s of a 6.88 s bake — 83 % of it — and the call count grows with the
    # square of the requested size, which is why a 4096 bake never completed.
    # That was the whole reason the bake was pinned at a resolution 7.8x too
    # small for the canvas.
    #
    # What a terrain field needs from noise is smoothness, isotropy and a known
    # wavelength, not simplex's particular lattice. So it is drawn as white
    # noise on a grid whose spacing is a quarter of the octave's wavelength and
    # upsampled with a cubic kernel: same field character, vectorised, and
    # seeded from the novel id so it still reproduces exactly. The grid is
    # ~1/step of the raster on each side, so this costs a few milliseconds per
    # octave at any output size instead of a few seconds.
    seed_base = _stable_seed(novel_id)

    # ── Sparse-sample + upsample helper ──
    from scipy.ndimage import gaussian_filter, zoom

    long_side = max(img_w, img_h)

    def _sparse_noise(seed: int, wl_frac: float) -> np.ndarray:
        """Smooth isotropic noise with wavelength `wl_frac` of the long side.

        The wavelength is a FRACTION OF THE RASTER, deliberately, and not a count
        of pixels. When it was a pixel count (1/freq, with freq in cycles per
        raster pixel as simplex used to take it) the terrain's physical scale was
        welded to the bake resolution: a 4096 bake laid down the same 250-pixel
        blobs as a 1024 bake, which on a fixed-size canvas means every feature
        came out 4x smaller. Raising the resolution therefore did not resolve the
        same terrain more sharply, it drew *different* terrain — the biome field
        collapsed into per-texel speckle, which an HF-energy metric happily
        scored as a 3.6x sharpness win. Tying the wavelength to the long side
        makes the field resolution-independent, so more pixels buy only sharpness.
        The three elevation values reproduce the old 1024-pixel field exactly
        (250/83/29 px of a 1024 raster = 0.244/0.081/0.028 of its long side).

        The sample spacing is a quarter of the wavelength — derived from the
        octave, not fixed. The old code sampled every fixed 4 px: that
        oversampled the 0.004 octave (wavelength 250 px) sixtyfold while the
        paper-grain octave at 0.12 was sampled at about its own Nyquist limit.
        Four samples per wavelength is comfortably above Nyquist and cheap. The
        grid is sized straight from the fraction rather than from a rounded
        spacing, so two bakes of different size get the identical grid, hence
        the identical random draw, hence the same field sampled finer.
        """
        wavelength = max(wl_frac, 1e-9) * long_side
        gw = max(3, int(np.ceil(4.0 * img_w / wavelength)) + 1)
        gh = max(3, int(np.ceil(4.0 * img_h / wavelength)) + 1)
        grid = np.random.default_rng(seed).standard_normal((gh, gw))
        up = zoom(grid, (img_h / gh, img_w / gw), order=3)[:img_h, :img_w]
        # Cubic interpolation of a white-noise grid leaves a faint ripple on the
        # grid lines. A sub-pixel blur removes it without touching the octave's
        # scale, which is 4 samples across. This sigma is deliberately in raster
        # pixels, unlike the wavelength and the final blur: the artefact it kills
        # is a property of the interpolation kernel, not of the field.
        up = gaussian_filter(up, 0.6)
        sd = float(up.std())
        return (up - up.mean()) / sd if sd > 1e-9 else up

    def _fbm(seed: int, base_wl: float, octaves: int) -> np.ndarray:
        """Fractal sum: each octave half the wavelength and half the weight.

        The count is the field's detail budget and is the thing that decides how
        fine the terrain reads — not the raster size. Measured on 西游记, the
        shipped three-octave fields put 99 % of their spectral power above a
        wavelength of 320 canvas pixels, i.e. the whole map was blobs a
        twenty-fifth of its width across, while the raster resolved down to 15.6.
        Three octaves are also the worst case for the look: with no small scales
        to break them up, the Whittaker lookup paints large flat patches with
        smooth borders, which is what "soft" means here. `lacunarity` is fixed at
        2 so successive octaves overlap in scale and read as texture.
        """
        out = np.zeros((img_h, img_w), dtype=np.float64)
        amp, wl, norm = 1.0, base_wl, 0.0
        for i in range(octaves):
            out += _sparse_noise(seed + i, wl) * amp
            norm += amp
            amp *= 0.5
            wl *= 0.5
        return out / norm

    def _ridged(seed: int, base_wl: float, octaves: int) -> np.ndarray:
        """Ridged multifractal: crests first, then detail weighted onto them.

        `1-|n|` on its own is not enough, and the way it fails is worth writing
        down because it looks plausible in every statistic. `|n|` is small on
        the zero set of a smooth random field, which is a NETWORK OF CURVES, so
        `1-|n|` is large only along thin filaments and near zero everywhere
        between them. Applying it at every octave therefore produces the
        topology of terrain INVERTED: broad lowland with a web of thin high
        ridges, where real ground is broad uplands cut by narrow valleys. On the
        palette that puts the snow line along the filaments, and the picture
        reads as veins under skin rather than as mountains. Seen, not inferred.

        The fix is the standard one -- squaring sharpens the crest, and a
        `weight` carried from the previous octave means each finer octave only
        contributes where the coarser one already put a ridge. Detail therefore
        gathers on the crests and the lowlands stay smooth, which is both what
        real terrain does and what makes a mountain range read as a range
        instead of as texture.

        Each octave is divided by its own p99 of |n|, not by a shared maximum: a
        shared maximum is set by one outlier cell, which compresses every other
        octave and flattens the crests.
        """
        out = np.zeros((img_h, img_w), dtype=np.float64)
        amp, wl, norm = 1.0, base_wl, 0.0
        weight = np.ones((img_h, img_w), dtype=np.float64)
        for i in range(octaves):
            n = _sparse_noise(seed + i, wl)
            scale = float(np.percentile(np.abs(n), 99.0)) or 1.0
            signal = (1.0 - np.clip(np.abs(n) / scale, 0.0, 1.0)) ** 2
            signal = signal * weight
            out += signal * amp
            norm += amp
            weight = np.clip(signal * _RIDGE_WEIGHT_GAIN, 0.0, 1.0)
            amp *= _RIDGE_PERSISTENCE
            wl *= 0.5
        return out / norm

    # ── Continuous elevation field ──
    #
    # Two fields off one seed, and the distinction matters more than the octave
    # count:
    #
    #   elev   the CLASS field. It feeds the Whittaker lookup, so every octave in
    #          it becomes a biome boundary. It stays coarse on purpose: feeding
    #          the full fractal sum in here was tried, and because an fBm's
    #          number of above-threshold excursions grows fast with octave count,
    #          the snow class shattered into hundreds of separate caps — visible
    #          in a rendered A/B as the map turning into confetti at fit zoom,
    #          where the reader sees it most. Coherent regions are what make a
    #          map readable, and they are bought by keeping this field smooth.
    #   relief the DETAIL field: the same seed with the full octave budget. It
    #          never changes a class. It only shades the result, which is where
    #          fine texture belongs — as relief, not as more biomes.
    #
    # Base wavelength is a fraction of the long side, converted from the old
    # cycles-per-pixel frequency against the 1024 raster it was tuned on:
    # 1/0.004 = 250 px = 0.244 of 1024.
    elev = _fbm(seed_base + 11, 0.244, _CLASS_OCTAVES)
    # An affine bias. It no longer sets an absolute level — `_spread_unit` maps
    # the field's own window onto 0-1 and the clipping there is what gives this
    # its meaning — but it does control how much of the field lands above or
    # below `_FIELD_WINDOW`, so it shapes the result rather than shifting it.
    elev = elev * 0.5 + 0.38

    # ── Location influence on elevation, bounded, then spread ──
    # The radii are the bake's calibration, unchanged: 0.18 / 0.22 / 0.15 / 0.14
    # of the raster's long side, i.e. a fraction of the canvas rather than a
    # fixed distance. Only the way overlapping points combine is different.
    influence_r = max(img_w, img_h) * 0.18

    elev += _bounded_influence(img_w, img_h, [
        (mountain_pts, influence_r, 0.25),
        (water_pts, influence_r, -0.20),
    ])
    elev = _spread_unit(elev)

    # ── Continuous moisture field (multi-octave) ──
    moist = _fbm(seed_base + 21, 0.195, _MOIST_OCTAVES)
    # An affine bias, in the same sense as the elevation one above.
    moist = moist * 0.5 + 0.30

    water_r = max(img_w, img_h) * 0.22
    forest_r = max(img_w, img_h) * 0.15
    mtn_r = max(img_w, img_h) * 0.14
    moist += _bounded_influence(img_w, img_h, [
        (water_pts, water_r, 0.35),
        (forest_pts, forest_r, 0.20),
        (mountain_pts, mtn_r, -0.12),
    ])
    moist = _spread_unit(moist)

    # ── Guards ──
    # Both defects this replaced were silent. The influence accumulator
    # saturated, and on any canvas wider than the 1600x900 default every
    # influence point landed outside the raster, leaving location-independent
    # noise that still looked like plausible terrain. Neither raised and neither
    # was visible in the output without a measuring tool, so both are now
    # reported at the source rather than left to be rediscovered.
    outside = sum(
        1 for x, y in layout.values()
        if not (0.0 <= x * scale_x < img_w
                and 0.0 <= (canvas_height - y) * scale_y < img_h)
    )
    if outside:
        logger.warning(
            "terrain: %d/%d locations fall outside the %dx%d raster — the "
            "canvas is probably not %dx%d (novel=%s). Pass the layout's own "
            "canvas, or the field will be location-independent",
            outside, len(layout), img_w, img_h,
            canvas_width, canvas_height, novel_id,
        )
    dominant = np.bincount(
        (elev * 255).astype(np.uint8).ravel(), minlength=256,
    ).max() / elev.size
    if dominant > 0.35:
        logger.warning(
            "terrain: elevation field is degenerate, %.0f %% of pixels share "
            "one value (novel=%s) — check that the influence rule is bounded",
            dominant * 100, novel_id,
        )

    if _SHAPE == "ridged":
        # ── One field, shaded, with the palette ramped over it ──
        #
        # The influences are the ones the class field already uses, and they are
        # applied here rather than to `elev`, because in this recipe the height
        # field is the only field: a mountain location has to raise the ground
        # the reader sees, not a parallel quantity that only picks a colour.
        # A ridged field on its own has the wrong topology for mass, and mixing
        # it into an fBm base is the standard fix.
        #
        # `1-|n|` is large along the zero set of a smooth field, which is a
        # NETWORK OF CURVES, so a pure ridged sum puts its greatest heights on
        # one-dimensional filaments. Snow lands on them, and because filaments
        # are long, thin and branching the eye reads them as rivers in the
        # valleys rather than as crests -- seen at length, not inferred. Terrain
        # needs its high ground to have AREA.
        #
        # So the broad shape is an fBm, whose mass is genuinely two-dimensional,
        # and the ridged sum is mixed into it to sharpen the crests and give
        # them a drainage structure. _RIDGE_MIX is the split.
        ridge = np.zeros((img_h, img_w), dtype=np.float64)
        for i, (wl, octs, amp) in enumerate(_RIDGE_SCALES):
            ridge += amp * _ridged(seed_base + 11 + 101 * i, wl, octs)
        ridge = _own_unit(ridge, _HEIGHT_WINDOW)

        base = _own_unit(_fbm(seed_base + 11, _RIDGE_BASE_WL, _RIDGE_OCTAVES),
                         _HEIGHT_WINDOW)

        # Which provinces are mountainous, as a separate low-frequency field, so
        # the answer is a property of the map rather than of each cell. Applied
        # to the ridges only: a plain keeps its broad shape and loses its crests,
        # rather than going flat and reading as a hole.
        mask = _fbm(seed_base + 61, _RELIEF_MASK_WL, _RELIEF_MASK_OCTAVES)
        mask = _own_unit(mask, (10.0, 90.0))
        ridge *= _PLAIN_FLOOR + (1.0 - _PLAIN_FLOOR) * mask

        # Which provinces are WET, as its own field — deliberately not `mask`,
        # which answers "which provinces are mountainous"; reusing it would make
        # every green province a mountainous one. One octave, so a province is
        # one thing over a whole province rather than carrying detail inside
        # itself.
        province = _own_unit(_fbm(seed_base + 71, _RELIEF_MASK_WL, 1), (10.0, 90.0))

        height = (1.0 - _RIDGE_MIX) * base + _RIDGE_MIX * ridge
        height += _bounded_influence(img_w, img_h, [
            (mountain_pts, influence_r, 0.25),
            (water_pts, influence_r, -0.20),
        ])
        h_lo = float(np.percentile(height, _HEIGHT_WINDOW[0]))
        h_hi = float(np.percentile(height, _HEIGHT_WINDOW[1]))
        height = np.clip((height - h_lo) / max(h_hi - h_lo, 1e-9), 0.0, 1.0)
        # Bias the mass into the lowlands before colouring, so the map is mostly
        # the light low ground a reader can put labels on. See _HEIGHT_GAMMA.
        height = height ** _HEIGHT_GAMMA
        rgb = _ramp_lookup(height, moist, province) * (
            _SHADE_FLOOR + _SHADE_RANGE * _hillshade(height)
        )[:, :, np.newaxis]
    else:
        # ── Per-pixel Whittaker color lookup ──
        # Vectorized: scale to grid indices and bilinear interpolate
        e_idx = np.clip(elev * 4.0, 0.0, 4.0)
        m_idx = np.clip(moist * 4.0, 0.0, 4.0)
        ei = np.clip(np.floor(e_idx).astype(np.int32), 0, 3)
        mi = np.clip(np.floor(m_idx).astype(np.int32), 0, 3)
        ef = e_idx - ei.astype(np.float64)
        mf = m_idx - mi.astype(np.float64)

        # Build grid lookup array
        grid_arr = np.array(_WHITTAKER_GRID, dtype=np.float64)  # (5, 5, 3)
        c00 = grid_arr[ei, mi]          # (H, W, 3)
        c01 = grid_arr[ei, mi + 1]
        c10 = grid_arr[ei + 1, mi]
        c11 = grid_arr[ei + 1, mi + 1]

        ef3 = ef[:, :, np.newaxis]
        mf3 = mf[:, :, np.newaxis]
        rgb = (
            c00 * (1 - ef3) * (1 - mf3)
            + c01 * (1 - ef3) * mf3
            + c10 * ef3 * (1 - mf3)
            + c11 * ef3 * mf3
        )

        # ── Relief shading from the detail field ──
        # A slope-driven light from the upper left, the same convention every map
        # and every hillshade uses. This is what makes the fine octaves read as
        # landform instead of as speckle: they never move a biome boundary, they
        # only darken what faces away from the light, so the terrain gains
        # structure without the classes fragmenting. The slope is normalised
        # against its own 95th percentile so the gain does not drift with the
        # raster size or with how many octaves the detail budget happens to buy.
        # Shaded on a band-limited height field, deliberately. Running the
        # gradient over the full fractal sum shades the finest octave hardest — a
        # gradient amplifies high frequencies — and the result reads as stucco
        # rather than as landform. The finest octaves go in flat, as
        # _TEXTURE_AMPLITUDE, where they add grain without pretending to be
        # slopes.
        if _RELIEF_GAIN > 0.0:
            relief = _fbm(seed_base + 11, 0.244, _RELIEF_OCTAVES)
            gy, gx = np.gradient(relief)
            slope = gx + gy             # signed slope along the light vector
            slope_scale = float(np.percentile(np.abs(slope), 95.0))
            shade = np.clip(slope / (slope_scale + 1e-12), -2.0, 2.0)
            rgb = rgb * (1.0 + _RELIEF_GAIN * shade)[:, :, np.newaxis]

    if _TEXTURE_AMPLITUDE > 0.0:
        texture = _fbm(seed_base + 51, _TEXTURE_BASE_WL, _TEXTURE_OCTAVES)
        rgb = rgb * (1.0 + _TEXTURE_AMPLITUDE * texture)[:, :, np.newaxis]

    # ── Color variation noise for visual depth ──
    # Each of these is skipped at 0 rather than multiplied by it: the dials are
    # documented as disabling the term, and a disabled term should not cost a
    # full fractal sum.
    if _VARIATION_STRENGTH > 0.0:
        variation = _fbm(seed_base + 31, 0.098, _VARIATION_OCTAVES)
        rgb = rgb + variation[:, :, np.newaxis] * _VARIATION_STRENGTH

    # ── Paper grain texture ──
    if _PAPER_STRENGTH > 0.0:
        paper = _sparse_noise(seed_base + 41, 0.0081)
        rgb = rgb + paper[:, :, np.newaxis] * _PAPER_STRENGTH

    rgb = np.clip(rgb, 0, 255).astype(np.uint8)

    # ── Gaussian blur for smooth, painterly transitions ──
    # Also a fraction of the long side, for the reason the noise wavelengths are:
    # a fixed sigma of 4 px was 0.39 % of a 1024 raster but only 0.10 % of a 4096
    # one, so raising the resolution silently traded the painterly wash for the
    # speckle the field is made of. See _BLUR_FRAC.
    blur_sigma = max(0.5, long_side * _BLUR_FRAC)
    for ch in range(3):
        rgb[:, :, ch] = gaussian_filter(
            rgb[:, :, ch].astype(np.float64), sigma=blur_sigma).astype(np.uint8)

    # ── Save ──
    img = Image.fromarray(rgb, "RGB")
    maps_dir = DATA_DIR / "maps" / novel_id
    maps_dir.mkdir(parents=True, exist_ok=True)
    out_path = terrain_path_for(novel_id)
    img.save(str(out_path), "PNG")
    logger.info("Terrain image saved: %s (%dx%d)", out_path, img_w, img_h)
    return str(out_path)


# ── Layout caching helpers ─────────────────────────


# Bump this when solver algorithm changes to invalidate layout cache
#
# v12: the revert of the three-ring shelf experiment. v11 was spent on the
# three-ring version, so the cache already held 8136-vertex wrapped rings when
# the code went back to one — and a 0.50 s response proved the cache was still
# serving them. Same trap as `_TERRAIN_VERSION`: the recipe changed and the
# number did not, so nothing invalidated. A revert is a recipe change.
#
# v11: the landmass payload gained two more shelf rings (`_SHELF_RING_MULTS`), so
# the cached layout held one ring where the client then read three.
#
# v12: `_SHELF_RING_MULTS` went from one entry to two and each contour carries a
# depth band. Same species of bug as v11 -- the shelf *geometry* changed, so the
# cache key has to move with it or the first request after a restart is served a
# single flat ring and looks like the fix did nothing.
#
# v13: two-band depth shipped in `generate_landmasses` (shelves + shelf_depth),
# but the `map_geo_artifacts` cache persisted only `shelves` and dropped
# `shelf_depth` on every cached read -- so the depth bands never reached the
# client. The fix persists `shelf_depth_json` in that cache.
#
# v14: bump so the stale v13 geo-artifacts row (shelves WITHOUT shelf_depth) is
# not reused. The ch_hash already folds this version, so both `map_layouts` and
# `map_geo_artifacts` keys move together.
#
# v15: v13/v14 did not actually deliver the depth bands. The store's signature
# had gained `geo_coords_json` *before* `shelf_depth_json` and the single call
# site still passed positionally, so every row written since holds the depth
# array in `geo_coords_json` and NULL in `shelf_depth_json`. The call site now
# passes keywords and the two trailing params are keyword-only, but every row
# cached so far is corrupt, so the bump is what forces a rewrite.
_LAYOUT_VERSION = 21


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
        # v0.68: tighter scatter to keep unresolved near their anchor
        jitter_r = base_jitter * 0.5 + base_jitter * 0.2 * (orphan_idx % 8)
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
    is_land_fn=None,
) -> list[tuple[float, float]]:
    """Trace a single river path via gradient descent with lateral wiggle.

    If *is_land_fn* is provided, the river terminates when it leaves land
    (reaches the coastline / ocean).
    """
    path = [(sx, sy)]
    x, y = sx, sy
    for _i in range(max_steps):
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
        # Stop if next point is outside land (river reached ocean)
        if is_land_fn and not is_land_fn(new_x, new_y):
            break
        path.append((new_x, new_y))
        x, y = best_x, best_y  # gradient position (unwiggled) for next step
    return path


def generate_rivers(
    locations: list[dict],
    layout_data: list[dict],
    novel_id: str,
    canvas_width: int = CANVAS_WIDTH,
    canvas_height: int = CANVAS_HEIGHT,
    land_mask_info: dict | None = None,
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
    base_seed = _stable_seed(novel_id)
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

    # ── Filter sources: only keep those on land ──
    if land_mask_info:
        _fmask = land_mask_info["_land_mask"]
        _fcs = land_mask_info["_cell_size"]
        _fh, _fw = _fmask.shape
        land_sources = []
        for sx, sy in sources:
            gi = round(sx / _fcs)
            gj = round(sy / _fcs)
            if 0 <= gj < _fh and 0 <= gi < _fw and _fmask[gj, gi]:
                land_sources.append((sx, sy))
        sources = land_sources

    # ── Build land check function from landmass mask ──
    is_land_fn = None
    if land_mask_info:
        _mask = land_mask_info["_land_mask"]
        _cs = land_mask_info["_cell_size"]
        _mh, _mw = _mask.shape

        def is_land_fn(x: float, y: float) -> bool:
            gi = round(x / _cs)
            gj = round(y / _cs)
            if 0 <= gj < _mh and 0 <= gi < _mw:
                return bool(_mask[gj, gi])
            return False

    # ── Trace rivers ──
    rivers: list[dict] = []
    for sx, sy in sources:
        path = _trace_river(
            sx, sy, elevation_at, wiggle_noise,
            canvas_width, canvas_height,
            is_land_fn=is_land_fn,
        )
        if len(path) < 5:
            continue
        width = min(5.0, max(1.5, len(path) / 20))
        rivers.append({
            "points": [[round(px, 1), round(py, 1)] for px, py in path],
            "width": round(width, 1),
        })

    return rivers


# ── Road network generation (Delaunay MST) ──────────────────────


def generate_roads(
    locations: list[dict],
    layout_data: list[dict],
    land_mask_info: dict | None = None,
) -> list[dict]:
    """Generate road network via Delaunay triangulation → MST filtering.

    Returns list of road segments: [{"from": name, "to": name, "points": [[x,y],...]}]
    Roads follow the MST (minimum spanning tree) of the Delaunay graph,
    producing a connected network without redundant paths.
    """
    layout_map = {item["name"]: item for item in layout_data}

    # Score locations: higher mention count + lower level = more important
    scored: list[tuple[float, dict]] = []
    for loc in locations:
        item = layout_map.get(loc["name"])
        if not item or "x" not in item or "y" not in item:
            continue
        # Skip ocean/sea locations — no roads on water
        loc_type = (loc.get("type") or "").lower()
        icon = (loc.get("icon") or "").lower()
        if "海" in loc_type or "洋" in loc_type or icon in ("water", "ocean"):
            continue
        score = loc.get("mention_count", 1) + (5 - loc.get("level", 3)) * 10
        scored.append((score, loc))

    # Limit road network to top 150 locations for performance
    # (Delaunay + MST on thousands of points is too slow for roughjs rendering)
    _MAX_ROAD_LOCATIONS = 150
    if len(scored) > _MAX_ROAD_LOCATIONS:
        scored.sort(key=lambda x: -x[0])
        scored = scored[:_MAX_ROAD_LOCATIONS]

    points = []
    names = []
    for _, loc in scored:
        item = layout_map[loc["name"]]
        points.append((item["x"], item["y"]))
        names.append(loc["name"])

    if len(points) < 3:
        return []

    pts = np.array(points)
    tri = Delaunay(pts)

    # Build adjacency with distances
    edges: dict[tuple[int, int], float] = {}
    for simplex in tri.simplices:
        for i in range(3):
            a, b = int(simplex[i]), int(simplex[(i + 1) % 3])
            key = (min(a, b), max(a, b))
            if key not in edges:
                edges[key] = float(np.linalg.norm(pts[a] - pts[b]))

    # Kruskal's MST
    parent = list(range(len(pts)))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: int, b: int) -> bool:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[ra] = rb
            return True
        return False

    # Build water-crossing check using land_mask
    _land_mask = None
    _cell_sz = 8.0
    if land_mask_info:
        _land_mask = land_mask_info.get("_land_mask")
        _cell_sz = land_mask_info.get("_cell_size", 8.0)

    # Compute max road length: median NN distance × 4 — roads longer than this
    # almost certainly cross water or connect distant unrelated locations
    nn_dists_road = np.linalg.norm(pts[np.newaxis] - pts[:, np.newaxis], axis=2)
    np.fill_diagonal(nn_dists_road, np.inf)
    median_nn_road = float(np.median(nn_dists_road.min(axis=1)))
    max_road_dist = median_nn_road * 4.0

    def _crosses_water(ax: float, ay: float, bx: float, by: float) -> bool:
        """Check if a road segment crosses water by dense sampling along it."""
        if _land_mask is None:
            return False
        grid_h, grid_w = _land_mask.shape
        # Adaptive sampling: 1 sample per 8px (= cell_size), min 8 samples
        seg_len = math.sqrt((bx - ax) ** 2 + (by - ay) ** 2)
        n_samples = max(8, int(seg_len / _cell_sz))
        for i in range(1, n_samples):
            t = i / n_samples
            sx = ax + t * (bx - ax)
            sy = ay + t * (by - ay)
            gx = round(sx / _cell_sz)
            gy = round(sy / _cell_sz)
            if 0 <= gy < grid_h and 0 <= gx < grid_w:
                if not _land_mask[gy, gx]:
                    return True
        return False

    # Pre-filter: remove long edges and water-crossing edges before MST
    land_edges: dict[tuple[int, int], float] = {}
    for key, dist in edges.items():
        if dist > max_road_dist:
            continue  # too long → skip
        a, b = key
        if not _crosses_water(pts[a][0], pts[a][1], pts[b][0], pts[b][1]):
            land_edges[key] = dist

    roads = []
    for (a, b), _dist in sorted(land_edges.items(), key=lambda x: x[1]):
        if _union(a, b):
            roads.append({
                "from": names[a],
                "to": names[b],
                "points": [
                    [round(pts[a][0], 1), round(pts[a][1], 1)],
                    [round(pts[b][0], 1), round(pts[b][1], 1)],
                ],
            })

    return roads


# ── Landmass generation (distance field + contour tracing) ─────────

# Tier weight: higher-tier locations have larger "territory"
_TIER_WEIGHT: dict[str, float] = {
    "continent": 3.0, "kingdom": 2.5, "region": 2.0,
    "city": 1.5, "site": 1.0, "building": 0.8,
}

# Water-type detection patterns (Chinese)
# Only oceans/seas create coastline gaps; rivers/springs flow through land
_OCEAN_TYPE_KEYWORDS = ("海", "洋")          # 海/洋 → strong distance amplification
_OCEAN_TYPE_EXCLUDE = ("海榴", "海棠", "海市")  # false positives (place names)
_ISLAND_TYPE_KEYWORDS = ("岛",)


def _point_in_polygon_scalar(px: float, py: float, poly: list[tuple[float, float]]) -> bool:
    """Ray casting point-in-polygon test (scalar reference implementation).

    Kept for equivalence tests against the vectorized ``_points_in_polygon``.
    """
    n_ = len(poly)
    inside = False
    j = n_ - 1
    for i in range(n_):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _points_in_polygon(
    points: np.ndarray | list[tuple[float, float]],
    poly: np.ndarray | list[tuple[float, float]],
) -> np.ndarray:
    """Vectorized ray casting: batch-test many points against one polygon ring.

    Returns a bool array; element-wise identical to ``_point_in_polygon_scalar``
    (same expression, same float operation order per edge).
    """
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    n_pts = len(pts)
    if n_pts == 0:
        return np.zeros(0, dtype=bool)
    poly_arr = np.asarray(poly, dtype=np.float64)
    if len(poly_arr) < 3:
        return np.zeros(n_pts, dtype=bool)
    xi = poly_arr[:, 0]
    yi = poly_arr[:, 1]
    xj = np.roll(xi, 1)  # edge (j, i) with j = i - 1, matching the scalar loop
    yj = np.roll(yi, 1)
    inside = np.zeros(n_pts, dtype=bool)
    chunk = 2048  # bound the (points × edges) broadcast temporaries
    with np.errstate(divide="ignore", invalid="ignore"):
        for start in range(0, n_pts, chunk):
            px = pts[start:start + chunk, 0:1]
            py = pts[start:start + chunk, 1:2]
            crosses = ((yi > py) != (yj > py)) & (
                px < (xj - xi) * (py - yi) / (yj - yi) + xi
            )
            inside[start:start + chunk] = np.count_nonzero(crosses, axis=1) % 2 == 1
    return inside


def generate_landmasses(
    locations: list[dict],
    layout_data: list[dict],
    novel_id: str,
    canvas_width: int = CANVAS_WIDTH,
    canvas_height: int = CANVAS_HEIGHT,
    **_kwargs,  # accept and ignore location_region_map for backward compat
) -> dict:
    """Generate landmass contours via distance field + contour tracing.

    Returns dict with ``landmasses`` (list of landmass dicts with coastline,
    holes, area, location_count, is_main) and ``shelves`` (list of shelf
    contour coordinate arrays).
    """
    from opensimplex import OpenSimplex
    from scipy.ndimage import binary_closing, binary_opening
    from scipy.spatial import KDTree

    # Deterministic per-novel seed, hoisted to the top on purpose. It used to be
    # computed a thousand lines down, next to the coastline distortion, and the
    # shelf wobble needs it *before* that point — the first cut added there
    # raised `UnboundLocalError`, which the caller swallows into "Failed to
    # generate landmasses" and an empty landmass list behind an HTTP 200.
    # Two independent `md5` calls in one function would also be a trap: they can
    # drift apart, and then the wobble stops belonging to the same world as the
    # coast it is wobbling.
    _seed = _stable_seed(novel_id)

    # Build coord + tier lookup from layout_data
    coords: dict[str, tuple[float, float]] = {}
    tiers: dict[str, str] = {}
    for item in layout_data:
        name = item.get("name", "")
        if item.get("is_portal"):
            continue
        coords[name] = (item["x"], item["y"])
        tiers[name] = item.get("tier", "site")

    cell_size = 8.0  # grid cell size in canvas px (canvas_width // 8)

    # Classify locations: only ocean/sea types create coastline gaps
    ocean_names: set[str] = set()  # seas/oceans → distance amplification
    island_names: set[str] = set()
    for loc in locations:
        name = loc.get("name", "")
        icon = loc.get("icon", "generic")
        loc_type = loc.get("type", "")

        if icon == "island" or any(kw in loc_type for kw in _ISLAND_TYPE_KEYWORDS):
            island_names.add(name)
        elif icon == "ocean" or (
            # icon=water + type is actual water body (海/洋/湖) — not rivers/springs/bridges
            icon == "water"
            and loc_type in ("海", "洋", "海洋", "大海", "海域", "湖", "湖泊")
        ) or (
            any(kw in loc_type for kw in _OCEAN_TYPE_KEYWORDS)
            and not any(ex in loc_type for ex in _OCEAN_TYPE_EXCLUDE)
            # Only match actual seas/oceans, not rivers/springs with 海 in name
            and loc_type not in ("河流", "泉水", "水井", "水池", "水潭", "池塘",
                                 "温泉", "涧", "河岸", "桥梁", "地点", "卫所", "亭子")
        ):
            ocean_names.add(name)

    # Collect LAND points only (exclude ocean/sea locations from distance field anchors)
    # Ocean locations mixed among land would attract the landmass boundary,
    # preventing proper coastline gaps. Excluding them lets the distance field
    # naturally produce high values at ocean positions.
    all_points: list[tuple[float, float]] = []
    all_weights: list[float] = []
    for item in layout_data:
        name = item.get("name", "")
        if item.get("is_portal") or name not in coords:
            continue
        if name in ocean_names:
            continue  # oceans are NOT land anchors
        all_points.append(coords[name])
        all_weights.append(_TIER_WEIGHT.get(tiers.get(name, "site"), 1.0))

    n = len(all_points)
    if n < 3:
        return {"landmasses": [], "shelves": []}

    points_arr = np.array(all_points, dtype=np.float64)
    weights_arr = np.array(all_weights, dtype=np.float64)

    # ── 1.1 Build weighted distance field ──
    grid_w = canvas_width // 8
    grid_h = canvas_height // 8
    tree = KDTree(points_arr)

    # Create grid coordinates
    gx = np.linspace(0, canvas_width, grid_w, endpoint=False) + 4  # center of cell
    gy = np.linspace(0, canvas_height, grid_h, endpoint=False) + 4
    grid_xx, grid_yy = np.meshgrid(gx, gy)
    grid_pts = np.column_stack([grid_xx.ravel(), grid_yy.ravel()])

    # Query k nearest neighbors for each grid point
    k = min(5, n)
    dists, idxs = tree.query(grid_pts, k=k)
    if k == 1:
        dists = dists.reshape(-1, 1)
        idxs = idxs.reshape(-1, 1)

    # Weighted distance: effective_dist = min(dist / weight) over k neighbors
    w_at_idx = weights_arr[idxs]  # shape (grid_pts, k)
    effective = dists / np.maximum(w_at_idx, 0.1)
    dist_field = effective.min(axis=1).reshape(grid_h, grid_w)

    # ── 1.2 Ocean/sea distance amplification (rivers/springs excluded) ──
    ocean_pts = [coords[name] for name in ocean_names if name in coords]
    if ocean_pts:
        ocean_arr = np.array(ocean_pts, dtype=np.float64)
        ocean_tree = KDTree(ocean_arr)
        ocean_dists, _ = ocean_tree.query(grid_pts, k=1)
        ocean_dists = ocean_dists.reshape(grid_h, grid_w)
        # Strong amplification near oceans/seas: creates coastline gaps
        amplify_radius = max(grid_w, grid_h) * 0.15  # tighter radius for focused effect
        ocean_factor = 1.0 + 1.5 * np.exp(-ocean_dists / max(amplify_radius, 1))
        dist_field *= ocean_factor

    # NOTE: Region-aware multi-continent separation (T4) was removed.
    # The distance-field suppression approach fragments landmasses when locations
    # are densely distributed. The natural distance field already creates
    # separation based on point density gaps between region clusters.

    # ── 1.3 Adaptive threshold ──
    nn_dists, _ = tree.query(points_arr, k=2)
    median_nn = float(np.median(nn_dists[:, 1]))

    # Convert median_nn from canvas coords to grid coords
    median_nn_grid = median_nn / cell_size
    # Generous threshold: merge nearby clusters into larger continents.
    # Higher k_factor = larger landmasses, fewer isolated islands.
    # v0.68: raised cap from 4.0→5.5 to compensate for tighter child spread
    k_factor = min(5.5, 1.8 + 0.6 * math.log2(max(2, n)))
    #   n=3→k=2.7, n=10→k=3.8, n=100→k=5.2, n=200→k=5.5
    threshold = median_nn_grid * k_factor

    # Content-driven adjustment (ocean ratio — only actual seas count)
    ocean_count = len(ocean_names)
    ocean_ratio = ocean_count / max(1, n)
    threshold *= max(0.6, 1.0 - ocean_ratio * 0.4)

    # Minimum landmass coverage guarantee (35% of canvas)
    # v0.68: raised from 25%→35% to ensure dense location clusters form connected land
    canvas_area_grid = grid_w * grid_h
    min_area = canvas_area_grid * 0.35
    min_threshold = math.sqrt(min_area / math.pi)
    threshold = max(threshold, min_threshold)

    # ── 1.3b Binary mask + morphological cleanup ──
    land_mask = dist_field < threshold
    # Morphological cleanup: large closing kernel merges nearby islands into continents
    struct_small = np.ones((3, 3), dtype=bool)
    struct_large = np.ones((7, 7), dtype=bool)   # 7x7 (was 5x5) for stronger merging
    land_mask = binary_closing(land_mask, structure=struct_large, iterations=2)
    land_mask = binary_opening(land_mask, structure=struct_small)   # remove pixel noise

    # ── 1.3b2 Ensure all land locations are covered ──
    # After morphological cleanup, some edge locations may fall outside the mask.
    # Grow small circles around uncovered land points to guarantee they're on land.
    _uncovered = 0
    _patch_r = max(3, round(threshold * 0.15))
    for px, py in all_points:
        gxi = round(px / cell_size)
        gyi = round(py / cell_size)
        if 0 <= gyi < grid_h and 0 <= gxi < grid_w and not land_mask[gyi, gxi]:
            _uncovered += 1
            y_lo = max(0, gyi - _patch_r)
            y_hi = min(grid_h, gyi + _patch_r + 1)
            x_lo = max(0, gxi - _patch_r)
            x_hi = min(grid_w, gxi + _patch_r + 1)
            yy, xx = np.ogrid[y_lo:y_hi, x_lo:x_hi]
            dist_sq = (xx - gxi) ** 2 + (yy - gyi) ** 2
            land_mask[y_lo:y_hi, x_lo:x_hi][dist_sq <= _patch_r * _patch_r] = True
    if _uncovered:
        # Re-apply closing to merge the patches smoothly with existing land
        land_mask = binary_closing(land_mask, structure=struct_large)
        logger.debug("Patched %d uncovered land locations", _uncovered)

    # ── 1.3c Ocean hole carving ──
    # Ocean locations may be positioned among land by the layout engine.
    # Carve small guaranteed ocean circles at each ocean position.
    # These create visible "inner sea" holes even when oceans are misplaced.
    if ocean_pts:
        ocean_r = max(round(threshold * 0.2), 3)
        for ox, oy in ocean_pts:
            gxi = round(ox / cell_size)
            gyi = round(oy / cell_size)
            y_lo = max(0, gyi - ocean_r)
            y_hi = min(grid_h, gyi + ocean_r + 1)
            x_lo = max(0, gxi - ocean_r)
            x_hi = min(grid_w, gxi + ocean_r + 1)
            yy, xx = np.ogrid[y_lo:y_hi, x_lo:x_hi]
            dist_sq = (xx - gxi) ** 2 + (yy - gyi) ** 2
            land_mask[y_lo:y_hi, x_lo:x_hi][dist_sq <= ocean_r * ocean_r] = False

    # ── Shelf rings ──
    #
    # One band at `threshold * 1.3` is a hard-edged flat ring, and a ring is a
    # sticker outline however pale it is — deepening the ocean put ~14 levels
    # between the two and made the edge worse, which is why the alpha had to be
    # walked back. A single ring cannot read as *depth*; depth needs more than
    # one contour.
    #
    # Three nested rings were tried here and reverted (v10). They do give a true
    # shallow-to-deep gradient — a shore point sits inside all three masks and
    # open water inside the outermost — but the masks are built from the same
    # global `dist_field`, i.e. "distance to the nearest land", so the outer ring
    # is not three rings around a continent. It is one ring around *everywhere
    # that happens to be within N of any coast*, and on a map with 西游记's
    # island density at 3.4x that merges the whole archipelago into a single
    # connected component: 8136 vertices where the field should have had a
    # hundred, a polygon covering half the canvas, and a payload ten times the
    # size — the client stopped reaching networkidle. A ring per landmass needs
    # a distance field per landmass, which is a different implementation rather
    # than a different multiplier.
    #
    # The multiplied form is kept as the seam for that work: the loop below
    # builds one mask per entry, and the caller already handles a list.
    # v11: two bands, not one. Measured on 西游记, the sea carries 6% of the
    # land's structure density and 62% of its water tiles are completely flat
    # (flatness_probe). There is no bathymetry to blame it on -- dialling the
    # client's ocean fill to zero makes the sea *flatter*, 0.882 -> 0.507, which
    # says the baked terrain under the water is empty and the flat wash is the
    # only thing there is. So depth has to be drawn, and the cheapest honest
    # thing that draws it is the one thing the shelf mask already has: how far
    # the water is from the nearest land. Two bands is the point of diminishing
    # return and three is where the archipelago stops being separate (see the
    # reverted attempt below).
    _SHELF_RING_MULTS = (1.3, 2.0)

    # ── Shelf width wobble ──────────────────────────────────────────────
    # The band width was uniform *by construction*: land is
    # `dist_field < threshold` and the shelf is the same field scaled by a
    # constant, so every stretch of coast got the same width. On the picture
    # that reads as a die-cut sticker around every island — the most clip-art
    # element on the map — and it is exactly the defect the land just had: a
    # constant where a large-scale field belongs.
    #
    # So the multiplier wobbles along the coast. Two invariants are preserved
    # deliberately, and both are why the numbers below are what they are:
    #
    #   * **nesting.** Inner 1.3 * 1.45 = 1.885 <= outer 2.0, so each band still
    #     contains the band inside it. The client paints them in area order and
    #     relies on that containment; if it broke, the banding reverses and the
    #     map "turns inside out" (see the note on `shelves` in NovelMap.tsx).
    #   * **the outer ring only shrinks** (`min(1, wobble)`). 2.0 is the value
    #     the archipelago-merge measurement was taken at, so the outer envelope
    #     never exceeds what has been measured safe.
    #
    # Amplitude is bounded by the first invariant, not by taste. The field is
    # sampled on a coarse lattice and upsampled: it is large-scale by design, so
    # 24 samples per wavelength is plenty, and the full-grid version costs 3.3 s
    # for a field that has no detail to resolve.
    _SHELF_WOBBLE_WL = 0.35    # of the canvas long side
    _SHELF_WOBBLE_AMP = 0.45   # +/- 45 % of the band
    _wob_wl = max(canvas_width, canvas_height) * _SHELF_WOBBLE_WL
    _wob_step_cells = max(1, int(round(_wob_wl / 8.0 / 24.0)))
    _wob_x = np.arange(0, grid_w + _wob_step_cells, _wob_step_cells) * 8.0
    _wob_y = np.arange(0, grid_h + _wob_step_cells, _wob_step_cells) * 8.0
    _wob_n = OpenSimplex(seed=_seed + 313)
    _wob_coarse = _wob_n.noise2array(_wob_x / _wob_wl, _wob_y / _wob_wl)
    _wob_coarse = np.clip((_wob_coarse + 1.0) * 0.5, 0.0, 1.0)   # 0..1
    from scipy.ndimage import zoom as _nd_zoom

    _wob = _nd_zoom(
        _wob_coarse,
        (grid_h / max(_wob_coarse.shape[0] - 1, 1), grid_w / max(_wob_coarse.shape[1] - 1, 1)),
        order=1,
    )[:grid_h, :grid_w]
    if _wob.shape != (grid_h, grid_w):
        _wob = np.pad(_wob, ((0, max(0, grid_h - _wob.shape[0])),
                             (0, max(0, grid_w - _wob.shape[1]))), mode="edge")[:grid_h, :grid_w]
    _shelf_wobble = 1.0 - _SHELF_WOBBLE_AMP + 2.0 * _SHELF_WOBBLE_AMP * _wob  # 1-a .. 1+a

    # ── The shelf is a rim measured from LAND, not from location points ──
    #
    # The masks used to be `dist_field < threshold * mult`, i.e. thresholds on
    # the field that runs to the nearest LOCATION POINT. Two things follow, and
    # both were measured on screen:
    #
    #   * 西游记 names places in the sea (西洋大海, 南海). A sea location is a
    #     point, so the threshold draws a patch around it in open water — the
    #     reader sees pale blobs in the middle of the ocean, 442 to 1018 canvas
    #     units from any coast (80 to 183 screen px at fit).
    #   * the mapping from field value to geometric distance depends on the
    #     local density of location points, so the band's WIDTH is uncontrolled.
    #     Measured along the contours: the inner ring's outline sits a median of
    #     63 canvas units from the coast (p90 125) — a proper rim — while the
    #     outer ring's runs a median of 214 and a p90 of **1014**. That is the
    #     ballooning, and it is why one ring covered 42 % of the canvas.
    #
    # `_SHELF_RING_MULTS` is kept as the shape of the ladder (how the bands step
    # outward) but it now multiplies a width in LAND-distance, so the band has a
    # geometric width by construction. Two consequences worth stating:
    #
    #   * a patch in open water is now impossible — not filtered out, impossible,
    #     because everything is measured from the land mask;
    #   * the band's width is the same everywhere, which is what the wobble is
    #     for. Without the wobble this would have traded a defect for a
    #     mechanical look; with it, the shelf is wide where it should be and
    #     absent where it should not.
    from scipy.ndimage import distance_transform_edt as _edt

    _dist_to_land_cells = _edt(~land_mask, sampling=(1.0, 1.0))
    # 8 cells is 64 canvas units, ~11 screen px at fit: the width the inner ring
    # already had, so the close-up that was already right does not change.
    _SHELF_BASE_CELLS = 8.0

    shelf_masks = []
    for _ring_i, _mult in enumerate(_SHELF_RING_MULTS):
        _w = _shelf_wobble if _ring_i < len(_SHELF_RING_MULTS) - 1 else np.minimum(1.0, _shelf_wobble)
        _width = _SHELF_BASE_CELLS * _mult * _w
        _m = _dist_to_land_cells < _width
        _m = binary_closing(_m, structure=struct_large)
        _m = binary_opening(_m, structure=struct_small)
        shelf_masks.append(_m)

    # (A dilation bound on the shelf masks was tried here first and is no longer
    # needed: measuring from the land mask makes an open-water patch impossible
    # rather than filtered. It also did not work — the patches are 8-connected
    # to the rims through thin necks, and the bound that would have caught them
    # was wider than the distance they sat at, so the output did not change.)

    # ── 1.4 Contour tracing (Moore Neighborhood per component) ──
    from scipy.ndimage import label as ndimage_label

    # 8-connected neighbor offsets (clockwise from right)
    _dx = [1, 1, 0, -1, -1, -1, 0, 1]
    _dy = [0, 1, 1, 1, 0, -1, -1, -1]

    def _trace_single_boundary(comp_mask: np.ndarray) -> list[tuple[int, int]] | None:
        """Trace the outer boundary of a single connected component via Moore neighborhood.

        Uses a per-component mask, so no shared visited-state issues.
        ``move_dir`` always stores the direction we moved to reach the current pixel.
        Search starts at ``(move_dir + 5) % 8`` = one step CW past the backtrack direction.
        """
        h, w = comp_mask.shape
        max_steps = h * w  # generous safety limit

        # Find first boundary pixel (top-left scan)
        start_x = start_y = -1
        initial_nonmask_dir = 0
        for sy in range(h):
            for sx in range(w):
                if not comp_mask[sy, sx]:
                    continue
                for di in range(8):
                    ny, nx_ = sy + _dy[di], sx + _dx[di]
                    if ny < 0 or ny >= h or nx_ < 0 or nx_ >= w or not comp_mask[ny, nx_]:
                        start_x, start_y = sx, sy
                        initial_nonmask_dir = di
                        break
                if start_x >= 0:
                    break
            if start_x >= 0:
                break

        if start_x < 0:
            return None

        # Convert initial non-mask direction to fake "movement direction" so that
        # the search formula (move_dir + 5) % 8 produces the correct start:
        # backtrack_dir = non-mask dir → search_start = (backtrack_dir + 1) % 8
        # Using move_dir: search_start = (move_dir + 5) % 8
        # So we need (move_dir + 5) = (initial_nonmask_dir + 1) → move_dir = initial_nonmask_dir - 4
        move_dir = (initial_nonmask_dir + 4) % 8  # opposite of non-mask = as if we came from the non-mask side

        contour: list[tuple[int, int]] = []
        cx, cy_ = start_x, start_y
        first = True
        steps = 0

        while steps < max_steps:
            contour.append((cx, cy_))

            # Search CW from one past backtrack: (move_dir + 5) % 8
            search_start = (move_dir + 5) % 8
            found = False
            for di_offset in range(8):
                di = (search_start + di_offset) % 8
                ny, nx_ = cy_ + _dy[di], cx + _dx[di]
                if 0 <= ny < h and 0 <= nx_ < w and comp_mask[ny, nx_]:
                    move_dir = di
                    cx, cy_ = nx_, ny
                    found = True
                    break

            if not found:
                break

            if not first and cx == start_x and cy_ == start_y:
                break
            first = False
            steps += 1

        return contour if len(contour) >= 4 else None

    def _trace_all_components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
        """Trace boundary of each connected component in mask independently."""
        labels, num = ndimage_label(mask, structure=np.ones((3, 3), dtype=int))
        contours: list[list[tuple[int, int]]] = []
        for comp_id in range(1, num + 1):
            comp_mask = labels == comp_id
            c = _trace_single_boundary(comp_mask)
            if c:
                contours.append(c)
        return contours

    # Trace land outer boundaries (one per connected land component)
    land_contours = _trace_all_components(land_mask)
    shelf_rings = [_trace_all_components(m) for m in shelf_masks]

    # Trace hole boundaries: connected sea regions NOT touching the grid border
    sea_labels, num_sea = ndimage_label(~land_mask, structure=np.ones((3, 3), dtype=int))
    hole_contours_raw: list[list[tuple[int, int]]] = []
    for comp_id in range(1, num_sea + 1):
        comp = sea_labels == comp_id
        # Skip ocean (touches border)
        if comp[0, :].any() or comp[-1, :].any() or comp[:, 0].any() or comp[:, -1].any():
            continue
        c = _trace_single_boundary(comp)
        if c:
            hole_contours_raw.append(c)

    # ── 1.5 Polygon classification ──
    def _unsigned_area(poly: list[tuple[float, float]]) -> float:
        """Unsigned polygon area via Shoelace formula."""
        n_ = len(poly)
        area = 0.0
        for i in range(n_):
            j = (i + 1) % n_
            area += poly[i][0] * poly[j][1]
            area -= poly[j][0] * poly[i][1]
        return abs(area) / 2.0

    # Convert grid coords to canvas coords
    def _grid_to_canvas(contour: list[tuple[int, int]]) -> list[tuple[float, float]]:
        return [(float(gx[min(c[0], len(gx) - 1)]), float(gy[min(c[1], len(gy) - 1)]))
                for c in contour]

    canvas_area = canvas_width * canvas_height
    min_outer_area = canvas_area * 0.005  # 0.5% of canvas

    # Convert all contours to canvas coords
    canvas_outers = [_grid_to_canvas(c) for c in land_contours]
    outer_areas = [_unsigned_area(c) for c in canvas_outers]
    canvas_holes = [_grid_to_canvas(c) for c in hole_contours_raw]

    # Count locations inside each outer ring (vectorized ray casting)
    def _count_locations_inside(poly: list[tuple[float, float]]) -> int:
        return int(np.count_nonzero(_points_in_polygon(points_arr, poly)))

    # Filter small outer rings (unless they contain locations)
    filtered_outers: list[tuple[list[tuple[float, float]], float, int]] = []
    for contour, area in zip(canvas_outers, outer_areas, strict=False):
        loc_count = _count_locations_inside(contour)
        if area >= min_outer_area or loc_count > 0:
            filtered_outers.append((contour, area, loc_count))

    # Sort by area descending
    filtered_outers.sort(key=lambda x: -x[1])

    # Post-process: absorb small islands (< 5% of largest) with few locations
    if len(filtered_outers) > 1:
        max_land_area = filtered_outers[0][1]
        absorb_threshold = max_land_area * 0.05
        kept: list[tuple[list[tuple[float, float]], float, int]] = []
        for contour, area, loc_count in filtered_outers:
            if area >= absorb_threshold or loc_count >= 2:
                kept.append((contour, area, loc_count))
        # Keep at least the main landmass
        filtered_outers = kept if kept else filtered_outers[:1]

        # ── The mask has to be pruned with the same rule, or it disagrees with
        #    the coastlines the client is given. ──
        #
        # `filtered_outers` is what becomes `landmasses` and every coastline the
        # reader sees. `land_mask` drives the terrain bake (clipped to it) and,
        # since this commit, the shelf's distance field. Nothing pruned it, so
        # every component the absorb rule just dropped kept painting: measured
        # on 西游记, `#terrain-biome` draws pale cream at four or five points in
        # open water (rgb 218,208,186 against the sea's 95,123,155) as far as
        # 1018 canvas units from any coast, and the shelf rings each one. That
        # is what the reader sees as grey discs in the middle of the ocean.
        #
        # Same two-part rule as above so the two agree by construction: a
        # component survives if it holds two or more locations, or if its area
        # clears the absorb threshold. Counting locations per mask component
        # rather than per contour is the only difference, and it is the safer
        # direction — a mask component that merges several contours still counts
        # all of their locations.
        from scipy.ndimage import label as _nd_label

        _lab, _n_comp = _nd_label(land_mask, structure=np.ones((3, 3), dtype=int))
        if _n_comp > 1:
            _loc_per_comp: dict[int, int] = {}
            for _lx, _ly in coords.values():
                _gi = int(_lx / cell_size)
                _gj = int(_ly / cell_size)
                if 0 <= _gi < grid_w and 0 <= _gj < grid_h:
                    _cid = int(_lab[_gj, _gi])
                    if _cid:
                        _loc_per_comp[_cid] = _loc_per_comp.get(_cid, 0) + 1
            _comp_cells = np.bincount(_lab.ravel(), minlength=_n_comp + 1)
            _cell_area = float(cell_size * cell_size)
            _drop = [
                _cid
                for _cid in range(1, _n_comp + 1)
                if _loc_per_comp.get(_cid, 0) < 2
                and _comp_cells[_cid] * _cell_area < absorb_threshold
            ]
            if _drop:
                land_mask = ~np.isin(_lab, _drop)
                land_mask = binary_opening(land_mask, structure=struct_small)
                logger.warning(
                    "landmask: dropped %d components the contour pass absorbed "
                    "(%.1f%% of the mask) so the bake and the shelf stop painting "
                    "land no coastline describes",
                    len(_drop),
                    100.0 * _comp_cells[_drop].sum() / max(_comp_cells[1:].sum(), 1),
                )

    # Associate holes with their containing outer ring
    hole_map: dict[int, list[list[tuple[float, float]]]] = {i: [] for i in range(len(filtered_outers))}
    for hole_contour in canvas_holes:
        hx = sum(p[0] for p in hole_contour) / len(hole_contour)
        hy = sum(p[1] for p in hole_contour) / len(hole_contour)
        for oi, (outer_c, _, _) in enumerate(filtered_outers):
            if _point_in_polygon_scalar(hx, hy, outer_c):
                hole_map[oi].append(hole_contour)
                break

    # ── 1.6 Chaikin smoothing + dual-frequency OpenSimplex distortion ──
    base_seed = _seed          # hoisted to the top of this function
    noise_gen = OpenSimplex(seed=base_seed + 200)

    def _chaikin_smooth(poly: list[tuple[float, float]], rounds: int) -> list[tuple[float, float]]:
        """Chaikin corner-cutting subdivision."""
        pts = list(poly)
        for _ in range(rounds):
            new_pts: list[tuple[float, float]] = []
            n_ = len(pts)
            for i in range(n_):
                p0 = pts[i]
                p1 = pts[(i + 1) % n_]
                new_pts.append((0.75 * p0[0] + 0.25 * p1[0], 0.75 * p0[1] + 0.25 * p1[1]))
                new_pts.append((0.25 * p0[0] + 0.75 * p1[0], 0.25 * p0[1] + 0.75 * p1[1]))
            pts = new_pts
        return pts

    def _distort_coastline(
        poly: list[tuple[float, float]],
        area: float,
        is_hole: bool = False,
    ) -> list[tuple[float, float]]:
        """Apply Chaikin smoothing + dual-frequency OpenSimplex distortion."""
        n_ = len(poly)
        if n_ < 4:
            return poly

        # Adaptive Chaikin rounds
        rounds = 2 if n_ > 20 else (1 if n_ > 10 else 0)
        smoothed = _chaikin_smooth(poly, rounds) if rounds > 0 else list(poly)

        # Amplitude scaling: small islands get less distortion
        area_scale = max(0.3, math.sqrt(abs(area) / canvas_area))
        large_amp = min(canvas_width, canvas_height) * 0.025 * area_scale
        small_amp = min(canvas_width, canvas_height) * 0.008 * area_scale

        # Distort along normals
        n_pts = len(smoothed)
        result: list[tuple[float, float]] = []
        for i in range(n_pts):
            px, py = smoothed[i]
            # Compute normal direction from neighbors
            prev = smoothed[(i - 1) % n_pts]
            nxt = smoothed[(i + 1) % n_pts]
            tx = nxt[0] - prev[0]
            ty = nxt[1] - prev[1]
            t_len = math.sqrt(tx * tx + ty * ty)
            if t_len < 1e-6:
                result.append((px, py))
                continue
            # Normal (perpendicular to tangent)
            nx_dir = -ty / t_len
            ny_dir = tx / t_len

            # Dual-frequency noise
            low_noise = noise_gen.noise2(px * 0.003, py * 0.003) * large_amp
            high_noise = noise_gen.noise2(px * 0.02, py * 0.02) * small_amp
            offset = low_noise + high_noise
            if is_hole:
                offset *= -1  # Inward for holes

            result.append((
                round(px + offset * nx_dir, 1),
                round(py + offset * ny_dir, 1),
            ))

        return result

    # Build landmass objects
    landmasses: list[dict] = []
    max_area = filtered_outers[0][1] if filtered_outers else 0

    for i, (contour, area, loc_count) in enumerate(filtered_outers):
        coastline = _distort_coastline(contour, area)
        holes: list[list[list[float]]] = []
        for hole_c in hole_map.get(i, []):
            hole_area = _unsigned_area(hole_c)
            distorted_hole = _distort_coastline(hole_c, hole_area, is_hole=True)
            holes.append([[round(p[0], 1), round(p[1], 1)] for p in distorted_hole])

        landmasses.append({
            "id": f"landmass_{i}" if i == 0 or area > max_area * 0.3 else f"island_{i}",
            "coastline": [[round(p[0], 1), round(p[1], 1)] for p in coastline],
            "holes": holes,
            "area": round(area, 1),
            "location_count": loc_count,
            "is_main": i == 0,
        })

    # Build shelf contours.
    #
    # The list, the sort and the loop all survive the reverted three-ring
    # attempt: with one entry in `_SHELF_RING_MULTS` they degenerate to the
    # original single pass, and the shape is the seam for the per-landmass
    # version described above. Sorting by area descending is a no-op today and
    # is what makes the list paint outermost-first the moment it is not.
    #
    # The upper bound is a guard, not a tuned value. An over-wide mask traces
    # the canvas border instead of a coastline — a polygon covering the map,
    # which would flood every ocean with shelf colour — and that is exactly what
    # the three-ring attempt produced. 0.7 is loose enough that no honest single
    # ring reaches it (the largest component measured on 西游记 is well under
    # half the canvas) and tight enough to catch the degenerate case.
    # (area, depth, points) triples, so the depth travels with its contour
    # through the sort. Sorting the depths separately would be wrong: the two
    # bands do not contribute the same number of contours, so the multiset of
    # depths is not the sequence the areas produce, and after a value sort they
    # would be paired with the wrong polygons.
    shelf_paths: list[tuple[float, float, list[list[float]]]] = []
    # Which band each contour belongs to, 0 = nearest the shore. With one entry
    # in `_SHELF_RING_MULTS` every value is 0 and the client falls back to its
    # single fill, so a persisted artifact from before this change is not a
    # shape error.
    _n_rings = max(1, len(_SHELF_RING_MULTS))
    for _ring_idx, ring_contours in enumerate(shelf_rings):
        _depth = 0.0 if _n_rings < 2 else _ring_idx / (_n_rings - 1)
        for c in ring_contours:
            sc = _grid_to_canvas(c)
            sc_area = _unsigned_area(sc)
            if sc_area < canvas_area * 0.01 or sc_area > canvas_area * 0.7:
                continue
            smoothed_shelf = _distort_coastline(sc, sc_area)
            shelf_paths.append((
                sc_area,
                round(_depth, 3),
                [[round(p[0], 1), round(p[1], 1)] for p in smoothed_shelf],
            ))
    shelf_paths.sort(key=lambda item: item[0], reverse=True)
    shelves: list[list[list[float]]] = [pts for _, _, pts in shelf_paths]
    # Area descending is also outermost-first, and a band's polygon contains the
    # bands inside it, so painting in this order lets the shallow inner band land
    # on top of the deep outer one instead of being buried by it.
    shelf_depth: list[float] = [d for _, d, _ in shelf_paths]

    # ── Post-generation coverage guarantee ──
    # Chaikin smoothing + OpenSimplex distortion shrink coastlines inward,
    # causing edge locations to fall outside rendered polygons even though
    # land_mask covers them. Expand each coastline to guarantee coverage.
    _expand_margin = max(15.0, min(canvas_width, canvas_height) * 0.01)
    _uncovered_expanded = 0
    for lm in landmasses:
        coast = lm["coastline"]
        if len(coast) < 3:
            continue
        # Find non-ocean points outside this landmass (vectorized ray casting)
        outside = points_arr[~_points_in_polygon(points_arr, coast)]
        if len(outside) == 0:
            continue
        # Keep only points near this coastline (within expand_margin * 3).
        # Squared distances avoid sqrt; chunked to bound temporaries.
        coast_arr = np.asarray(coast, dtype=np.float64)
        near_limit_sq = (_expand_margin * 3) ** 2
        uncovered_pts: list[tuple[float, float]] = []
        for start in range(0, len(outside), 256):
            blk = outside[start:start + 256]
            min_dist_sq = ((blk[:, None, :] - coast_arr[None, :, :]) ** 2).sum(axis=2).min(axis=1)
            for k in np.nonzero(min_dist_sq < near_limit_sq)[0]:
                uncovered_pts.append((float(blk[k][0]), float(blk[k][1])))

        if not uncovered_pts:
            continue

        # Expand coastline outward to cover uncovered points
        # Compute centroid of coastline
        cx = sum(c[0] for c in coast) / len(coast)
        cy = sum(c[1] for c in coast) / len(coast)
        new_coast: list[list[float]] = []
        for c in coast:
            # Check if any uncovered point is near this coast segment
            needs_expand = False
            for pt in uncovered_pts:
                d = math.sqrt((pt[0] - c[0]) ** 2 + (pt[1] - c[1]) ** 2)
                if d < _expand_margin * 2:
                    needs_expand = True
                    break
            if needs_expand:
                # Push this vertex outward from centroid
                dx = c[0] - cx
                dy = c[1] - cy
                dist_from_center = math.sqrt(dx * dx + dy * dy)
                if dist_from_center > 1e-6:
                    scale = _expand_margin / dist_from_center
                    new_coast.append([
                        round(c[0] + dx * scale, 1),
                        round(c[1] + dy * scale, 1),
                    ])
                else:
                    new_coast.append(c)
            else:
                new_coast.append(c)
        lm["coastline"] = new_coast
        _uncovered_expanded += len(uncovered_pts)

    if _uncovered_expanded:
        logger.info("Expanded coastlines to cover %d uncovered locations", _uncovered_expanded)

    logger.info(
        "Generated landmasses for novel %s: %d landmasses, %d shelves (threshold=%.1f, n=%d)",
        novel_id, len(landmasses), len(shelves), threshold, n,
    )

    return {
        "landmasses": landmasses,
        "shelves": shelves,
        # Depth band per contour, 0 = nearest the shore. Parallel to `shelves`
        # in the same order; absent on artifacts persisted before v11, where the
        # client falls back to a single fill.
        "shelf_depth": shelf_depth,
        "_land_mask": land_mask,       # internal: numpy bool grid (not serialized)
        "_cell_size": cell_size,       # internal: grid cell size in canvas px
    }
