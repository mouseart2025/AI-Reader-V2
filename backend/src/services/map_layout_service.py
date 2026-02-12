"""Map layout engine: constraint-based coordinate solver + terrain generation.

Uses scipy.optimize.differential_evolution to find (x, y) coordinates for each
location that satisfy spatial constraints extracted from the novel text.
Falls back to hierarchical circular layout when constraints are insufficient.
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
CANVAS_MIN = 20  # margin
CANVAS_MAX = CANVAS_SIZE - 20

# Minimum spacing between any two locations (pixels)
MIN_SPACING = 30

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


class ConstraintSolver:
    """Compute (x, y) layout for locations using spatial constraints."""

    def __init__(
        self,
        locations: list[dict],
        constraints: list[dict],
        user_overrides: dict[str, tuple[float, float]] | None = None,
    ):
        self.locations = locations
        self.constraints = _detect_and_remove_conflicts(constraints)
        self.user_overrides = user_overrides or {}

        # Build name -> index mapping
        self.loc_names = [loc["name"] for loc in locations]
        self.loc_index = {name: i for i, name in enumerate(self.loc_names)}
        self.n = len(self.loc_names)

        # Build parent -> children mapping for hierarchy fallback
        self.children: dict[str, list[str]] = {}
        self.roots: list[str] = []
        parent_map: dict[str, str | None] = {}
        for loc in locations:
            parent_map[loc["name"]] = loc.get("parent")

        for name, parent in parent_map.items():
            if parent and parent in self.loc_index:
                self.children.setdefault(parent, []).append(name)
            else:
                self.roots.append(name)

    def solve(self) -> tuple[dict[str, tuple[float, float]], str]:
        """Solve layout. Returns (name->coords, layout_mode)."""
        if len(self.constraints) < 3 or self.n < 2:
            logger.info(
                "Insufficient constraints (%d) or locations (%d), using hierarchy layout",
                len(self.constraints), self.n,
            )
            return self._hierarchy_layout(), "hierarchy"

        logger.info(
            "Solving layout for %d locations with %d constraints",
            self.n, len(self.constraints),
        )

        # Build bounds: each location has (x, y) in [CANVAS_MIN, CANVAS_MAX]
        # User-overridden locations are fixed (narrow bounds)
        bounds = []
        fixed_indices: set[int] = set()
        for name in self.loc_names:
            if name in self.user_overrides:
                ox, oy = self.user_overrides[name]
                bounds.extend([(ox - 0.1, ox + 0.1), (oy - 0.1, oy + 0.1)])
                fixed_indices.add(self.loc_index[name])
            else:
                bounds.extend([(CANVAS_MIN, CANVAS_MAX), (CANVAS_MIN, CANVAS_MAX)])

        # Filter constraints to only those referencing known locations
        valid_constraints = [
            c for c in self.constraints
            if c["source"] in self.loc_index and c["target"] in self.loc_index
        ]

        if len(valid_constraints) < 3:
            logger.info("Only %d valid constraints after filtering, using hierarchy", len(valid_constraints))
            return self._hierarchy_layout(), "hierarchy"

        try:
            result = differential_evolution(
                self._energy,
                bounds=bounds,
                args=(valid_constraints,),
                maxiter=200,
                popsize=15,
                tol=1e-6,
                seed=42,
                polish=True,
            )
            coords = result.x.reshape(-1, 2)
            layout = {
                name: (float(coords[i, 0]), float(coords[i, 1]))
                for i, name in enumerate(self.loc_names)
            }
            logger.info("Constraint solver converged: energy=%.2f", result.fun)
            return layout, "constraint"
        except Exception:
            logger.exception("Constraint solver failed, falling back to hierarchy")
            return self._hierarchy_layout(), "hierarchy"

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
            elif rtype == "terrain":
                pass  # terrain doesn't affect positioning

        # Anti-overlap penalty
        e += self._e_overlap(coords)

        return e

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

    def _e_overlap(self, coords: np.ndarray) -> float:
        """Anti-overlap: penalize locations that are too close to each other."""
        penalty = 0.0
        for i in range(self.n):
            for j in range(i + 1, self.n):
                dist = np.linalg.norm(coords[i] - coords[j])
                violation = max(0.0, MIN_SPACING - dist)
                if violation > 0:
                    penalty += violation ** 2
        return penalty

    def _hierarchy_layout(self) -> dict[str, tuple[float, float]]:
        """Fallback: concentric circle layout based on parent-child hierarchy."""
        center = CANVAS_SIZE / 2
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

        radius = 200
        for i, name in enumerate(unplaced_roots):
            angle = 2 * math.pi * i / max(len(unplaced_roots), 1)
            x = center + radius * math.cos(angle)
            y = center + radius * math.sin(angle)
            layout[name] = (x, y)

        # Place children around their parents
        self._place_children(layout, self.roots, child_radius=100)

        # Place any remaining unplaced locations
        unplaced = [n for n in self.loc_names if n not in layout]
        if unplaced:
            angle_step = 2 * math.pi / max(len(unplaced), 1)
            r = 350
            for i, name in enumerate(unplaced):
                angle = angle_step * i
                layout[name] = (center + r * math.cos(angle), center + r * math.sin(angle))

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
            px, py = layout.get(parent, (CANVAS_SIZE / 2, CANVAS_SIZE / 2))
            for i, child in enumerate(children):
                if child in layout:
                    continue
                angle = 2 * math.pi * i / len(children)
                cx = px + child_radius * math.cos(angle)
                cy = py + child_radius * math.sin(angle)
                # Clamp to canvas
                cx = max(CANVAS_MIN, min(CANVAS_MAX, cx))
                cy = max(CANVAS_MIN, min(CANVAS_MAX, cy))
                layout[child] = (cx, cy)
            self._place_children(layout, children, child_radius * 0.6)

    def _spiral_layout(self) -> dict[str, tuple[float, float]]:
        """Place all locations in a spiral pattern from center."""
        center = CANVAS_SIZE / 2
        layout: dict[str, tuple[float, float]] = {}
        for i, name in enumerate(self.loc_names):
            if name in self.user_overrides:
                layout[name] = self.user_overrides[name]
                continue
            angle = i * 2.4  # golden angle
            r = 30 + 15 * math.sqrt(i)
            x = center + r * math.cos(angle)
            y = center + r * math.sin(angle)
            layout[name] = (
                max(CANVAS_MIN, min(CANVAS_MAX, x)),
                max(CANVAS_MIN, min(CANVAS_MAX, y)),
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

    # Scale layout coordinates from [0, 1000] canvas to [0, size] image
    scale = size / CANVAS_SIZE
    points = []
    biome_colors = []

    for loc in locations:
        name = loc["name"]
        if name not in layout:
            continue
        x, y = layout[name]
        # Flip y: canvas y=0 is bottom, image y=0 is top
        px = x * scale
        py = (CANVAS_SIZE - y) * scale
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


def compute_chapter_hash(chapter_start: int, chapter_end: int) -> str:
    """Deterministic hash for a chapter range."""
    return hashlib.md5(f"{chapter_start}-{chapter_end}".encode()).hexdigest()[:16]


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
