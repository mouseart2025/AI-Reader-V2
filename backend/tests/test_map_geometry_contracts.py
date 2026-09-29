"""Geometry contracts for the generated coastline and its shelf band.

These two are the ones that would have caught today's defects *before* they were
committed, and they are the only checks in the style-lock contract that need no
renderer: both read `generate_landmasses` directly, so they cost a couple of
seconds and can run in CI where the browser-based probes cannot.

The metric is **imported, not re-implemented** — `scripts/probe_coastline_morphology.py`
is the single implementation of the fractal-dimension ruler, and a test that
re-derived the box count would be a second instrument free to disagree with the
first. That is the same rule the audit probe follows.

What each test is anchored to:

* `test_coastline_is_as_rough_as_a_real_coast` — the published band for real
  coastlines, 1.15-1.35 (Mandelbrot's Britain ≈ 1.25). The ruler reproduces Koch
  L4's analytic log4/log3 = 1.2619 to within 0.010 over the epsilon range used
  here, so the band is being checked against a ruler that has been calibrated on
  a curve with a known answer. Before the midpoint-displacement change this
  coastline scored 1.0625 — barely above a regular polygon's 1.0358 — so the test
  fails loudly on the old shape.

* `test_shelf_is_anchored_to_the_drawn_coastline` — the band must sit on the coast
  the map actually draws, not on the pre-prune, pre-displacement mask. The defect
  put up to 6.5 % of the inner rings' points inside the drawn land and left two
  rings ~1120 units from any coastline at all (the pale discs in open water).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# The ruler lives in `scripts/`; the repo root is two levels up from `backend/tests`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from probe_coastline_morphology import boxcount
from probe_shelf_anchor import anchor_stats

from src.services.map_layout_service import generate_landmasses

# The range the ruler was calibrated in. Koch L4 reads 1.2524 here (true 1.2619);
# a regular polygon reads 1.0358.
CALIBRATED_SIZES = [28.0, 41.0, 60.0, 88.0, 128.0, 186.0]
REAL_COAST_BAND = (1.15, 1.35)


def _fixture_novel():
    """Deterministic input with landmasses large enough to be measured.

    Not the real novel: CI has no database, and the contract is a property of the
    generator, not of one book. Several locations are spread far enough apart to
    produce a continent-scale ring, because the shelf and roughness amplitudes are
    clamped by ring size — a tiny island is a different regime and would not
    exercise the path under test.
    """
    locs = [
        {"name": n, "type": t, "icon": i}
        for n, t, i in [
            ("花果山", "山", "mountain"),
            ("水帘洞", "洞府", "cave"),
            ("流沙河", "河流", "water"),
            ("黑松林", "树林", "forest"),
            ("南海普陀山", "山", "mountain"),
            ("傲来国", "城市", "city"),
            ("乌鸡国", "区域", "city"),
            ("女儿国", "区域", "city"),
            ("车迟国", "城市", "city"),
            ("祭赛国", "城市", "city"),
            ("通天河", "河流", "water"),
            ("五行山", "山", "mountain"),
        ]
    ]
    layout = [
        {
            "name": loc["name"],
            "x": 800.0 + 1400.0 * (i % 4),
            "y": 700.0 + 1300.0 * (i // 4),
        }
        for i, loc in enumerate(locs)
    ]
    return locs, layout


@pytest.fixture(scope="module")
def landmasses_and_shelves():
    locs, layout = _fixture_novel()
    out = generate_landmasses(locs, layout, "geometry-contract", canvas_width=8000, canvas_height=4800)
    return out["landmasses"], out["shelves"]


def _ring_points(rings) -> np.ndarray:
    pts = []
    for r in rings:
        a = np.asarray(r, dtype=float)
        if len(a) >= 3:
            pts.append(np.vstack([a, a[:1]]))
    return np.concatenate(pts, 0)


def test_coastline_is_as_rough_as_a_real_coast(landmasses_and_shelves):
    lms, _ = landmasses_and_shelves
    rings = [lm["coastline"] for lm in lms] + [h for lm in lms for h in lm.get("holes", [])]
    pts = _ring_points(rings)
    assert len(pts) > 1000, f"fixture produced only {len(pts)} coastline points"

    r = boxcount(pts, CALIBRATED_SIZES)
    lo, hi = REAL_COAST_BAND
    assert lo <= r["D"] <= hi, (
        f"coastline fractal dimension {r['D']:.4f} is outside the real-coastline band "
        f"[{lo}, {hi}] (R2 {r['r2']:.4f}). Too low means the coast is smooth like a "
        "polygon — a smooth displacement field cannot fix that, only subdivision that "
        "introduces new vertices; too high means it is fragmenting into static."
    )
    assert r["r2"] > 0.99, f"the log-log fit is not a line (R2 {r['r2']:.4f}), so D is not meaningful"


def test_shelf_is_anchored_to_the_drawn_coastline(landmasses_and_shelves):
    lms, shelves = landmasses_and_shelves
    assert shelves, "no shelf rings were produced"

    # Rebuild the dump shape `anchor_stats` expects, from the in-memory geometry.
    # The rings are already polylines, so each becomes one path with `M ... L ...`.
    def to_d(ring) -> str:
        a = np.asarray(ring, dtype=float)
        # Explicit `L` per vertex: the shared parser handles `M`/`L`/`C`/`Q` and does
        # NOT implement SVG's implicit-lineto-after-moveto, so `M x y x y ...` would
        # parse as a single point and the ring would vanish.
        head = f"M {a[0][0]:.1f} {a[0][1]:.1f}"
        body = " ".join(f"L {p[0]:.1f} {p[1]:.1f}" for p in a[1:])
        return f"{head} {body} Z"

    dump = {
        "canvas": None,
        "groups": {
            "coastline": [{"tag": "path", "d": to_d(lm["coastline"])} for lm in lms],
            "shelf": [{"tag": "path", "d": to_d(s)} for s in shelves],
        },
    }
    tmp = Path("/tmp/_geometry_contract_dump.json")
    tmp.write_text(__import__("json").dumps(dump))

    # cell=4 is half the production grid (8) so the rasterisation error is below the
    # geometry's own resolution; the residual is ~0.5 % and is quantisation.
    st = anchor_stats(str(tmp), cell=4.0)
    assert "error" not in st, st.get("error")

    worst = st["worst_inside_pct"]
    assert worst <= 2.0, (
        f"{worst:.1f}% of the worst shelf ring's points fall inside the drawn land. "
        "The band is anchored to something the map does not draw — most likely it is "
        "being traced from a mask that is pruned later, or from the undisplaced "
        "contour, instead of from the final coastlines."
    )

    # A ring far from every coastline is the other half of the same defect: a band
    # for a component that has no drawn coast at all.
    assert st["max_dist_p50"] <= 400.0, (
        f"a shelf ring sits {st['max_dist_p50']} units from the nearest coastline "
        "at its median — that is a band around a shape the map never draws."
    )
