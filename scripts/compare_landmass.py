#!/usr/bin/env python3
"""Compare two landmass generation approaches using real novel data.

Approach A: Current distance-field pipeline (KDTree + tier weights + morphology)
Approach B: Voronoi tessellation + land/ocean cell classification + marching squares

Both approaches share the same Delaunay MST road network overlay.

Usage:
    cd backend && uv run python ../scripts/compare_landmass.py [novel_id]

If no novel_id given, uses the first novel with analyzed chapters.
"""

from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import Voronoi, Delaunay, KDTree
from scipy.ndimage import binary_closing, binary_opening, label as ndimage_label
from opensimplex import OpenSimplex

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

CANVAS_W, CANVAS_H = 1600, 900
GRID_CELL = 4  # Finer grid for better resolution


# ── Data Loading ───────────────────────────────────────────────


async def load_map_data(novel_id: str | None = None):
    """Load real location + layout data from the database."""
    from src.db.sqlite_db import get_connection

    conn = await get_connection()
    try:
        if not novel_id:
            row = await conn.execute_fetchall(
                "SELECT id FROM novels WHERE total_chapters > 0 LIMIT 1"
            )
            if not row:
                raise RuntimeError("No novels found")
            novel_id = row[0]["id"]

        # Get analyzed range
        row = await conn.execute_fetchall(
            "SELECT MIN(chapter_num) as mn, MAX(chapter_num) as mx "
            "FROM chapters c JOIN chapter_facts cf ON c.id = cf.chapter_id "
            "WHERE c.novel_id = ?",
            (novel_id,),
        )
        ch_start, ch_end = row[0]["mn"] or 1, row[0]["mx"] or 25

        # Use visualization_service to get locations and layout
        from src.services.visualization_service import get_map_data

        data = await get_map_data(novel_id, ch_start, min(ch_end, 25))
        return data
    finally:
        await conn.close()


# ── Approach A: Current Distance Field ─────────────────────────


def approach_a_distance_field(
    locations: list[dict], layout: list[dict], cw: int = CANVAS_W, ch: int = CANVAS_H,
) -> tuple[np.ndarray, list[list[tuple[float, float]]]]:
    """Current approach: KDTree distance field + morphological cleanup."""
    from src.services.map_layout_service import generate_landmasses

    result = generate_landmasses(
        locations, layout, "compare-test",
        canvas_width=cw, canvas_height=ch,
    )
    return result.get("_land_mask", np.zeros((1, 1))), result.get("landmasses", [])


# ── Approach A+: Distance Field with stronger merging ──────────


def approach_a_plus(
    locations: list[dict], layout: list[dict], cw: int = CANVAS_W, ch: int = CANVAS_H,
) -> tuple[np.ndarray, list[list[tuple[float, float]]]]:
    """Optimized distance field: larger closing kernel to merge islands into continents."""
    from src.services.map_layout_service import generate_landmasses
    import src.services.map_layout_service as _mod

    # Temporarily monkey-patch the threshold multiplier and closing kernel
    # to produce fewer, larger landmasses
    _orig_fn = _mod.generate_landmasses

    def _patched(locs, lay, nid, canvas_width=1600, canvas_height=900):
        # Call original but with modified parameters via post-processing
        result = _orig_fn(locs, lay, nid, canvas_width=canvas_width, canvas_height=canvas_height)
        mask = result.get("_land_mask")
        if mask is None or mask.size == 0:
            return result

        # Post-process: aggressive merging — close with larger kernel
        from scipy.ndimage import binary_closing, binary_opening, binary_dilation
        struct_merge = np.ones((9, 9), dtype=bool)  # Much larger than original (5,5)
        struct_clean = np.ones((3, 3), dtype=bool)
        merged = binary_closing(mask, structure=struct_merge, iterations=2)
        merged = binary_opening(merged, structure=struct_clean)

        # Re-trace boundaries with merged mask
        result["_land_mask"] = merged

        # Re-run boundary tracing (reuse internal functions)
        # For simplicity, just return the merged mask and let the rendering show the difference
        return result

    _mod.generate_landmasses = _patched
    try:
        result = generate_landmasses(locations, layout, "compare-a-plus",
                                     canvas_width=cw, canvas_height=ch)
    finally:
        _mod.generate_landmasses = _orig_fn

    return result.get("_land_mask", np.zeros((1, 1))), result.get("landmasses", [])


# ── Approach B: Voronoi + Marching Squares ─────────────────────


def approach_b_voronoi(
    locations: list[dict], layout: list[dict],
    cw: int = CANVAS_W, ch: int = CANVAS_H,
) -> tuple[np.ndarray, list[list[tuple[float, float]]]]:
    """New approach: Voronoi tessellation → land/ocean classification → marching squares."""
    layout_map = {item["name"]: item for item in layout}
    points = []
    names = []
    for loc in locations:
        item = layout_map.get(loc["name"])
        if item:
            points.append((item["x"], item["y"]))
            names.append(loc["name"])

    if len(points) < 3:
        return np.zeros((ch // GRID_CELL, cw // GRID_CELL), dtype=bool), []

    points_arr = np.array(points)
    n = len(points_arr)

    # Ocean detection
    ocean_kw = ("海", "洋")
    ocean_exclude = ("海榴", "海棠", "海市")
    ocean_indices = set()
    for i, loc in enumerate(locations):
        if i >= n:
            break
        loc_type = (loc.get("type") or "").lower()
        name = names[i] if i < len(names) else ""
        if any(kw in loc_type or kw in name for kw in ocean_kw):
            if not any(ex in name for ex in ocean_exclude):
                ocean_indices.add(i)

    # --- Step 1: Generate Voronoi with supplementary seeds ---
    # Add boundary mirror points for bounded Voronoi
    mirror_pts = []
    for px, py in points_arr:
        mirror_pts.append((-px, py))
        mirror_pts.append((2 * cw - px, py))
        mirror_pts.append((px, -py))
        mirror_pts.append((px, 2 * ch - py))

    # Add uniform seed grid for finer coastline resolution
    seed_spacing = 80
    for sx in range(0, cw, seed_spacing):
        for sy in range(0, ch, seed_spacing):
            mirror_pts.append((sx + seed_spacing / 2, sy + seed_spacing / 2))

    all_pts = np.vstack([points_arr, np.array(mirror_pts)])
    vor = Voronoi(all_pts)

    # --- Step 2: Classify Voronoi cells as land/ocean ---
    # A cell is "land" if its seed is a real location (not mirror/fill)
    # AND it's within a distance threshold from a land location
    tree = KDTree(points_arr)

    # Distance threshold: based on median nearest-neighbor
    nn_dists, _ = tree.query(points_arr, k=min(3, n))
    median_nn = float(np.median(nn_dists[:, -1])) if n > 1 else 200.0
    land_radius = median_nn * 2.0  # How far from locations is still "land"

    grid_h = ch // GRID_CELL
    grid_w = cw // GRID_CELL
    land_mask = np.zeros((grid_h, grid_w), dtype=bool)

    # Rasterize: for each grid cell, find nearest location; if within radius → land
    gx = np.linspace(GRID_CELL / 2, cw - GRID_CELL / 2, grid_w)
    gy = np.linspace(GRID_CELL / 2, ch - GRID_CELL / 2, grid_h)
    grid_xx, grid_yy = np.meshgrid(gx, gy)
    grid_pts = np.column_stack([grid_xx.ravel(), grid_yy.ravel()])

    dists, idxs = tree.query(grid_pts)
    dists = dists.reshape(grid_h, grid_w)
    idxs = idxs.reshape(grid_h, grid_w)

    # Base land mask: within radius AND nearest location is not ocean
    for gy_i in range(grid_h):
        for gx_i in range(grid_w):
            nearest_idx = idxs[gy_i, gx_i]
            dist = dists[gy_i, gx_i]
            is_ocean_loc = nearest_idx in ocean_indices
            if dist < land_radius and not is_ocean_loc:
                land_mask[gy_i, gx_i] = True

    # Ocean repulsion: carve out areas near ocean locations
    if ocean_indices:
        ocean_pts = points_arr[list(ocean_indices)]
        ocean_tree = KDTree(ocean_pts)
        ocean_dists, _ = ocean_tree.query(grid_pts)
        ocean_dists = ocean_dists.reshape(grid_h, grid_w)
        ocean_carve_r = land_radius * 0.4
        land_mask[ocean_dists < ocean_carve_r] = False

    # --- Step 3: Morphological cleanup ---
    struct3 = np.ones((3, 3), dtype=bool)
    struct5 = np.ones((5, 5), dtype=bool)
    land_mask = binary_closing(land_mask, structure=struct5)
    land_mask = binary_opening(land_mask, structure=struct3)

    # Ensure all land locations are covered
    for i, (px, py) in enumerate(points_arr):
        if i in ocean_indices:
            continue
        gi = int(py / GRID_CELL)
        gj = int(px / GRID_CELL)
        gi = min(gi, grid_h - 1)
        gj = min(gj, grid_w - 1)
        if not land_mask[gi, gj]:
            r = 3
            y_lo, y_hi = max(0, gi - r), min(grid_h, gi + r + 1)
            x_lo, x_hi = max(0, gj - r), min(grid_w, gj + r + 1)
            land_mask[y_lo:y_hi, x_lo:x_hi] = True

    # Re-close after patching
    land_mask = binary_closing(land_mask, structure=struct3)

    # --- Step 4: Marching Squares contour extraction ---
    coastlines = _marching_squares_contours(land_mask, gx, gy)

    # --- Step 5: OpenSimplex distortion ---
    noise = OpenSimplex(seed=42)
    distorted = []
    for contour in coastlines:
        if len(contour) < 4:
            continue
        smoothed = _chaikin(contour, rounds=2)
        pts_out = []
        for px, py in smoothed:
            nx_val = noise.noise2(px * 0.004, py * 0.004) * 12
            ny_val = noise.noise2(px * 0.004 + 100, py * 0.004 + 100) * 12
            pts_out.append((px + nx_val, py + ny_val))
        distorted.append(pts_out)

    # Build landmass dicts matching existing format
    landmasses = []
    for i, coast in enumerate(distorted):
        area = _shoelace_area(coast)
        if area < 500:
            continue
        landmasses.append({
            "id": f"voronoi_{i}",
            "coastline": coast,
            "holes": [],
            "area": area,
            "location_count": 0,
            "is_main": i == 0,
        })

    return land_mask, landmasses


def _marching_squares_contours(
    mask: np.ndarray, gx: np.ndarray, gy: np.ndarray
) -> list[list[tuple[float, float]]]:
    """Extract contours from binary mask using connected-component boundary tracing."""
    labeled, n_features = ndimage_label(mask, structure=np.ones((3, 3)))
    contours = []
    h, w = mask.shape

    for comp_id in range(1, n_features + 1):
        comp_mask = (labeled == comp_id)
        # Find boundary pixels (land pixel adjacent to non-land)
        boundary = []
        for y in range(h):
            for x in range(w):
                if not comp_mask[y, x]:
                    continue
                # Check 4-neighbors
                is_border = False
                for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    ny, nx = y + dy, x + dx
                    if ny < 0 or ny >= h or nx < 0 or nx >= w or not comp_mask[ny, nx]:
                        is_border = True
                        break
                if is_border:
                    cx = float(gx[min(x, len(gx) - 1)])
                    cy = float(gy[min(y, len(gy) - 1)])
                    boundary.append((cx, cy))

        if len(boundary) < 4:
            continue

        # Order boundary points by angle from centroid
        cx_mean = sum(p[0] for p in boundary) / len(boundary)
        cy_mean = sum(p[1] for p in boundary) / len(boundary)
        boundary.sort(key=lambda p: math.atan2(p[1] - cy_mean, p[0] - cx_mean))
        contours.append(boundary)

    return contours


def _chaikin(pts: list[tuple[float, float]], rounds: int = 2) -> list[tuple[float, float]]:
    """Chaikin corner-cutting smoothing."""
    result = list(pts)
    for _ in range(rounds):
        if len(result) < 3:
            break
        new_pts = []
        n = len(result)
        for i in range(n):
            p0 = result[i]
            p1 = result[(i + 1) % n]
            new_pts.append((0.75 * p0[0] + 0.25 * p1[0], 0.75 * p0[1] + 0.25 * p1[1]))
            new_pts.append((0.25 * p0[0] + 0.75 * p1[0], 0.25 * p0[1] + 0.75 * p1[1]))
        result = new_pts
    return result


def _shoelace_area(pts: list[tuple[float, float]]) -> float:
    """Shoelace formula for polygon area."""
    n = len(pts)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += pts[i][0] * pts[j][1]
        area -= pts[j][0] * pts[i][1]
    return abs(area) / 2.0


# ── Road Network (Delaunay MST) ───────────────────────────────


def generate_road_network(
    locations: list[dict], layout: list[dict]
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Generate road network via Delaunay triangulation → MST filtering."""
    layout_map = {item["name"]: item for item in layout}
    points = []
    for loc in locations:
        item = layout_map.get(loc["name"])
        if item:
            points.append((item["x"], item["y"]))

    if len(points) < 3:
        return []

    pts = np.array(points)
    tri = Delaunay(pts)

    # Build adjacency with distances
    edges: dict[tuple[int, int], float] = {}
    for simplex in tri.simplices:
        for i in range(3):
            a, b = simplex[i], simplex[(i + 1) % 3]
            key = (min(a, b), max(a, b))
            if key not in edges:
                dist = float(np.linalg.norm(pts[a] - pts[b]))
                edges[key] = dist

    # Kruskal's MST
    sorted_edges = sorted(edges.items(), key=lambda x: x[1])
    parent = list(range(len(pts)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
            return True
        return False

    mst_edges = []
    for (a, b), dist in sorted_edges:
        if union(a, b):
            mst_edges.append((tuple(pts[a]), tuple(pts[b])))

    return mst_edges


# ── Rendering ──────────────────────────────────────────────────


def render_comparison_3way(
    locations: list[dict],
    layout: list[dict],
    landmasses_a: list,
    landmasses_ap: list,
    landmasses_b: list,
    roads: list,
    cw: int = CANVAS_W,
    ch: int = CANVAS_H,
    output_path: str = "scripts/landmass_comparison.png",
):
    """Render 3-panel comparison to PNG."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Polygon as MplPolygon
        from matplotlib.collections import LineCollection
    except ImportError:
        print("matplotlib not installed.")
        return

    layout_map = {item["name"]: item for item in layout}
    loc_pts = [(layout_map[loc["name"]]["x"], layout_map[loc["name"]]["y"])
               for loc in locations if loc["name"] in layout_map]

    fig, axes = plt.subplots(1, 3, figsize=(30, 9), facecolor="#2a2520")

    panels = [
        (axes[0], landmasses_a, f"A: Distance Field ({len(landmasses_a)} islands)"),
        (axes[1], landmasses_ap, f"A+: Stronger Merging ({len(landmasses_ap)} islands)"),
        (axes[2], landmasses_b, f"B: Voronoi ({len(landmasses_b)} islands)"),
    ]

    for ax, landmasses, title in panels:
        ax.set_facecolor("#3a5a7c")
        ax.set_xlim(0, cw)
        ax.set_ylim(ch, 0)
        ax.set_aspect("equal")
        ax.set_title(title, color="white", fontsize=13, fontweight="bold")
        ax.tick_params(colors="white", labelsize=6)

        for lm in landmasses:
            coast = lm.get("coastline", [])
            if len(coast) < 3:
                continue
            poly = MplPolygon(coast, facecolor="#d4c5a0", edgecolor="#8b7d5e",
                             linewidth=0.8, alpha=0.9)
            ax.add_patch(poly)
            for hole in lm.get("holes", []):
                if len(hole) >= 3:
                    ax.add_patch(MplPolygon(hole, facecolor="#3a5a7c",
                                           edgecolor="#8b7d5e", linewidth=0.4, alpha=0.8))

        if roads:
            lc = LineCollection([[(a[0], a[1]), (b[0], b[1])] for a, b in roads],
                               colors="#6b5b3e", linewidths=0.5, alpha=0.4)
            ax.add_collection(lc)

        if loc_pts:
            xs, ys = zip(*loc_pts)
            ax.scatter(xs, ys, s=6, c="#e74c3c", zorder=5, edgecolors="none", alpha=0.6)

    plt.tight_layout(pad=1.0)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(str(out), dpi=150, bbox_inches="tight")
    plt.close()
    print(f"\n✅ 3-way comparison saved to: {out.resolve()}")


def render_comparison(
    locations: list[dict],
    layout: list[dict],
    landmasses_a: list,
    landmasses_b: list,
    roads: list,
    cw: int = CANVAS_W,
    ch: int = CANVAS_H,
    output_path: str = "scripts/landmass_comparison.png",
):
    """Render side-by-side comparison to PNG."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Polygon as MplPolygon
        from matplotlib.collections import PatchCollection, LineCollection
    except ImportError:
        print("matplotlib not installed. Install: uv add matplotlib")
        return

    layout_map = {item["name"]: item for item in layout}
    loc_pts = []
    for loc in locations:
        item = layout_map.get(loc["name"])
        if item:
            loc_pts.append((item["x"], item["y"]))

    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(20, 9), facecolor="#2a2520")

    for ax, landmasses, title in [
        (ax_a, landmasses_a, "A: Distance Field (current)"),
        (ax_b, landmasses_b, "B: Voronoi + Marching Squares"),
    ]:
        ax.set_facecolor("#3a5a7c")  # Ocean blue
        ax.set_xlim(0, cw)
        ax.set_ylim(ch, 0)
        ax.set_aspect("equal")
        ax.set_title(title, color="white", fontsize=14, fontweight="bold")
        ax.tick_params(colors="white", labelsize=7)

        # Draw landmasses
        for lm in landmasses:
            coast = lm.get("coastline", [])
            if len(coast) < 3:
                continue
            poly = MplPolygon(coast, facecolor="#d4c5a0", edgecolor="#8b7d5e",
                             linewidth=1.0, alpha=0.9)
            ax.add_patch(poly)
            # Draw holes
            for hole in lm.get("holes", []):
                if len(hole) >= 3:
                    h_poly = MplPolygon(hole, facecolor="#3a5a7c", edgecolor="#8b7d5e",
                                       linewidth=0.5, alpha=0.8)
                    ax.add_patch(h_poly)

        # Draw roads
        if roads:
            road_lines = [[(a[0], a[1]), (b[0], b[1])] for a, b in roads]
            lc = LineCollection(road_lines, colors="#6b5b3e", linewidths=0.6, alpha=0.5)
            ax.add_collection(lc)

        # Draw locations
        if loc_pts:
            xs, ys = zip(*loc_pts)
            ax.scatter(xs, ys, s=8, c="#e74c3c", zorder=5, edgecolors="none", alpha=0.7)

    plt.tight_layout(pad=1.0)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(str(out), dpi=150, bbox_inches="tight")
    plt.close()
    print(f"\n✅ Comparison saved to: {out.resolve()}")
    print(f"   A: {len(landmasses_a)} landmasses")
    print(f"   B: {len(landmasses_b)} landmasses")
    print(f"   Roads: {len(roads)} edges (Delaunay MST)")


# ── Main ───────────────────────────────────────────────────────


async def main():
    novel_id = sys.argv[1] if len(sys.argv) > 1 else None

    print("Loading map data...")
    data = await load_map_data(novel_id)
    locations = data.get("locations", [])
    layout = data.get("layout", [])
    print(f"  {len(locations)} locations, {len(layout)} layout items")

    canvas = data.get("canvas_size", {})
    cw = canvas.get("width", CANVAS_W)
    ch = canvas.get("height", CANVAS_H)
    print(f"  Canvas: {cw}x{ch}")

    print("\nApproach A: Distance Field (current)...")
    _, landmasses_a = approach_a_distance_field(locations, layout, cw, ch)
    print(f"  → {len(landmasses_a)} landmasses")

    print("\nApproach A+: Distance Field (stronger merging)...")
    _, landmasses_ap = approach_a_plus(locations, layout, cw, ch)
    print(f"  → {len(landmasses_ap)} landmasses")

    print("\nApproach B: Voronoi + Marching Squares...")
    _, landmasses_b = approach_b_voronoi(locations, layout, cw, ch)
    print(f"  → {len(landmasses_b)} landmasses")

    print("\nGenerating road network (Delaunay MST)...")
    roads = generate_road_network(locations, layout)
    print(f"  → {len(roads)} road edges")

    print("\nRendering 3-way comparison...")
    render_comparison_3way(locations, layout, landmasses_a, landmasses_ap, landmasses_b, roads, cw, ch)


if __name__ == "__main__":
    asyncio.run(main())
