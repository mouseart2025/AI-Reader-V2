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

* `test_coastline_roughness_follows_the_terrain` — a coast is crenulated where
  mountains meet the sea and smooth where plains do. That is ordinary geomorphology
  and it is what a user reported missing. Measured as **sinuosity** (arc / chord over
  a 300-unit window) split by distance to the nearest rugged vs smooth location,
  because that is the criterion that still has resolution at this map's feature
  scale. Reference bands: ~1.00-1.03 straight, 1.05-1.15 headland/bay,
  >1.20 strongly crenulated.

  ⚠️ This replaces an assertion that the **whole-coast** fractal dimension sits in the
  published real-coastline band [1.15, 1.35]. That assertion encoded a uniformity
  assumption: it can only be satisfied by making every stretch equally rough, which
  is the very defect it was meant to prevent. The literature band describes one
  homogeneous rocky coastline; a coast that is deliberately half plains-facing
  legitimately reads lower. A per-class fractal dimension was tried and abandoned —
  after modulation the rugged arcs are only ~2 000 units against a 186-unit box
  ceiling, and the fit is endpoint-dominated (it returned D < 1.0 for smooth arcs,
  which no curve can do).

* `test_shelf_is_anchored_to_the_drawn_coastline` — the band must sit on the coast
  the map actually draws, not on the pre-prune, pre-displacement mask. The defect
  put up to 6.5 % of the inner rings' points inside the drawn land and left two
  rings ~1120 units from any coastline at all (the pale discs in open water).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

# The ruler lives in `scripts/`; the repo root is two levels up from `backend/tests`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from probe_coast_roughness_profile import classify, sinuosity_along
from probe_coastline_morphology import boxcount
from probe_shelf_anchor import anchor_stats
from scipy.spatial import cKDTree

from src.services.map_layout_service import generate_landmasses

# Only used by the degeneracy guard now; the real criterion is the sinuosity split.
CALIBRATED_SIZES = [28.0, 41.0, 60.0, 88.0, 128.0, 186.0]
REAL_COAST_BAND = (1.15, 1.35)
SIN_WINDOW = 300.0
SIN_STEP = 40.0
RUGGED_SINUOSITY = (1.15, 1.45)   # crenulated, but not past strongly crenulated
SMOOTH_SINUOSITY_MAX = 1.08       # "plains coast" reads as gentle bays at most
MIN_SEPARATION = 0.12


def _fixture_novel():
    """Deterministic input whose coast runs past an alternating sequence of places.

    Not the real novel: CI has no database. But the shape matters for what is being
    tested. A first attempt spread 12 places over a regular grid, and the resulting
    coast was nearest to whichever **corner** happened to be closest — so the
    rugged/smooth split along the coast was arbitrary and the test read **backwards**
    (rugged 1.108 vs smooth 1.320). The real map interleaves rugged and smooth places
    along the coast, and a fixture must reproduce that or it is testing the fixture.

    So: 16 places on a circle, alternating mountain and city. The landmass forms a
    disc, its coast runs around the ring, and each stretch of coast is nearest to a
    known kind of place in turn. That is the structure the contract is about.
    """
    locs, layout = [], []
    for i in range(16):
        angle = 2.0 * math.pi * i / 16.0
        rugged = i % 2 == 0
        locs.append({
            "name": f"{'峰' if rugged else '城'}{i}",
            "type": "山" if rugged else "城市",
            "icon": "mountain" if rugged else "city",
        })
        layout.append({
            "name": locs[-1]["name"],
            "x": 4000.0 + 1600.0 * math.cos(angle),
            "y": 2400.0 + 1600.0 * math.sin(angle),
        })
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


def test_coastline_roughness_follows_the_terrain(landmasses_and_shelves):
    lms, _ = landmasses_and_shelves
    locs, layout = _fixture_novel()
    pos = {d["name"]: (d["x"], d["y"]) for d in layout}
    rugged = np.array(
        [pos[loc["name"]] for loc in locs if loc["name"] in pos and classify(loc) == "rugged"]
    )
    smooth = np.array(
        [pos[loc["name"]] for loc in locs if loc["name"] in pos and classify(loc) == "smooth"]
    )
    assert len(rugged) >= 3 and len(smooth) >= 3, (
        f"fixture must contain both kinds of place, got {len(rugged)} rugged / "
        f"{len(smooth)} smooth"
    )

    rings = [np.asarray(lm["coastline"], dtype=float) for lm in lms]
    prof = np.concatenate([sinuosity_along(r, SIN_WINDOW, SIN_STEP) for r in rings], 0)
    assert len(prof) > 50, f"only {len(prof)} sinuosity windows; fixture too small to judge"

    # Split by whichever kind of place is NEARER. Parameter-free on purpose: a
    # distance radius was the first version and it needed a different value on the
    # fixture than on the novel, which is a sign the parameter was doing the work.
    tr, ts = cKDTree(rugged), cKDTree(smooth)
    d_r, _ = tr.query(prof[:, :2])
    d_s, _ = ts.query(prof[:, :2])
    sin = prof[:, 2]
    near_r, near_s = sin[d_r < d_s], sin[d_s <= d_r]
    assert len(near_r) >= 50 and len(near_s) >= 50, (
        f"too few windows on either side ({len(near_r)} / {len(near_s)}); "
        "the fixture does not put both kinds of place along the coast"
    )
    m_r, m_s = float(np.median(near_r)), float(np.median(near_s))

    lo, hi = RUGGED_SINUOSITY
    assert lo <= m_r <= hi, (
        f"coast next to rugged places has sinuosity {m_r:.3f}, outside [{lo}, {hi}]. "
        "Below the band the mountains meet a smooth coast; above it the crenulation "
        "is past 'strongly crenulated' and the map reads as torn."
    )
    assert m_s <= SMOOTH_SINUOSITY_MAX, (
        f"coast next to smooth places has sinuosity {m_s:.3f} > {SMOOTH_SINUOSITY_MAX}. "
        "A plains-facing coast should read as gentle bays, not as a mountain front."
    )
    assert m_r - m_s >= MIN_SEPARATION, (
        f"rugged {m_r:.3f} vs smooth {m_s:.3f} differ by {m_r - m_s:.3f} < {MIN_SEPARATION}. "
        "The roughness is being applied evenly, independent of the terrain — the exact "
        "defect this test exists for."
    )

    # Degeneracy guard only. The whole-coast dimension is NOT the criterion any more:
    # it falls when most of a coast is plains-facing, which is correct.
    D = boxcount(_ring_points([lm["coastline"] for lm in lms]), CALIBRATED_SIZES)["D"]
    d_lo, d_hi = REAL_COAST_BAND
    assert d_hi + 0.10 >= D, (
        f"whole-coast fractal dimension {D:.4f} is far above the published band "
        f"({d_lo}-{d_hi}) — the coast is fragmenting into noise."
    )


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
