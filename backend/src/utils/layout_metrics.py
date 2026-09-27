"""Layout geometry quality metrics — how far each child sits from its parent.

Complements `topology_metrics` (which scores the hierarchy tree, no coordinates).
This module scores the *layout*: given coordinates, are children placed inside
their parent's neighbourhood?

Caliber note (2026-09-25)
-------------------------
The sibling-outlier ratio used by the earlier containment baseline
(`dist > k * median(sibling distances)`) has a DENOMINATOR EFFECT: tightening
the normal population shrinks the median, which pushes untouched outliers over
the threshold — reporting an improvement as a regression.

Measured on 西游记 (cosmic canvas 8000x4500), after the scatter-attractor fix:
    scatter-path parent-child p90:  806.6 -> 50.6   (-94%, real improvement)
    sibling-outlier rate (k=3):     6.51% -> 10.13% (worse, artifact)
See ai-reader-internal/docs/analysis/map-layout-determinism-and-scatter-verify-2026-09-25.md

These metrics therefore use ABSOLUTE distance quantiles normalised by the canvas
diagonal, split by parent scale: a continent's children SHOULD spread wide,
a building's should not.
"""

from __future__ import annotations

import math

# Parent tiers whose children legitimately spread across the canvas (macro
# structure). Everything else is expected to hug its parent.
COARSE_PARENT_TIERS = frozenset({"world", "continent", "kingdom"})

# Gate thresholds on `{scale}_{quantile}_ratio` = quantile(distance) / canvas diagonal.
#
# Why three quantiles, not just p90: the scatter-attractor fix moved the BULK of
# the layout hard (西游记 fine-scale p50 65.8 -> 8.7, -87%) while p90 barely
# moved (2061.5 -> 2042.8, -1%) because its tail is owned by solver-placed
# nodes this patch cannot reach. A p90-only gate would sit red forever and stop
# carrying signal. So: p50/p75 gate the body, p90 gates the tail and doubles as
# the tracker for the outstanding solver work.
#
# PROVISIONAL — calibrated from ONE novel (西游记, cosmic canvas 8000x4500,
# 2026-09-25). Values are engineering calibration, not natural constants; the
# other four novels still need measuring, and any change to the solver must
# re-baseline them. `coarse` is deliberately loose: a continent's children are
# SUPPOSED to spread.
DEFAULT_THRESHOLDS: dict[str, dict[str, float]] = {
    "fine": {"p50_ratio": 0.005, "p75_ratio": 0.040, "p90_ratio": 0.250},
    "coarse": {"p50_ratio": 0.040, "p75_ratio": 0.150, "p90_ratio": 0.600},
    "overall": {"p50_ratio": 0.008, "p75_ratio": 0.060, "p90_ratio": 0.250},
}
_GATED_QUANTILES = ("p50", "p75", "p90")


def _quantile(sorted_vals: list[float], q: float) -> float:
    """Nearest-rank quantile on an already-sorted list."""
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(q * len(sorted_vals)))
    return sorted_vals[idx]


def compute_layout_metrics(
    coords: dict[str, tuple[float, float]],
    parents: dict[str, str],
    tiers: dict[str, str] | None = None,
    layers: dict[str, str] | None = None,
    canvas_size: tuple[float, float] = (1600.0, 900.0),
    thresholds: dict[str, float] | None = None,
) -> dict:
    """Score parent-child geometry of a computed layout.

    Args:
        coords: {name: (x, y)} for placed locations.
        parents: {child: parent}.
        tiers: {name: tier} — used to split stats by parent scale.
        layers: {name: layer_id} — cross-layer pairs are skipped, as distance
            between two coordinate systems is meaningless.
        canvas_size: (width, height) the layout was solved on.
        thresholds: override DEFAULT_THRESHOLDS.

    Returns:
        Dict with per-scale distance quantiles, their ratio to the canvas
        diagonal, and a pass/fail flag per scale.
    """
    tiers = tiers or {}
    layers = layers or {}
    # Deep-merge so a caller can override a single quantile without dropping
    # the rest of that scale's limits
    th = {scale: dict(limits) for scale, limits in DEFAULT_THRESHOLDS.items()}
    for scale, limits in (thresholds or {}).items():
        th.setdefault(scale, {}).update(limits)

    diag = math.hypot(float(canvas_size[0]), float(canvas_size[1])) or 1.0

    fine: list[float] = []
    coarse: list[float] = []
    edges = 0
    cross_layer = 0
    parent_unplaced = 0

    for name, xy in coords.items():
        parent = parents.get(name)
        if not parent:
            continue
        pxy = coords.get(parent)
        if pxy is None:
            parent_unplaced += 1
            continue
        layer_child = layers.get(name)
        layer_parent = layers.get(parent)
        if layer_child and layer_parent and layer_child != layer_parent:
            cross_layer += 1
            continue

        dist = math.hypot(xy[0] - pxy[0], xy[1] - pxy[1])
        edges += 1
        if tiers.get(parent, "") in COARSE_PARENT_TIERS:
            coarse.append(dist)
        else:
            fine.append(dist)

    fine.sort()
    coarse.sort()
    overall = sorted(fine + coarse)

    result: dict = {
        "edges": edges,
        "placed": len(coords),
        "skipped_cross_layer": cross_layer,
        "skipped_parent_unplaced": parent_unplaced,
        "canvas_diagonal": round(diag, 1),
    }

    for label, vals in (("fine", fine), ("coarse", coarse), ("overall", overall)):
        result[f"{label}_edges"] = len(vals)
        limits = th.get(label, {})
        failures: list[str] = []
        for qname, q in (("p50", 0.5), ("p75", 0.75), ("p90", 0.9)):
            value = _quantile(vals, q)
            ratio = value / diag
            result[f"{label}_{qname}"] = round(value, 1)
            result[f"{label}_{qname}_ratio"] = round(ratio, 4)
            limit = limits.get(f"{qname}_ratio")
            passed = limit is None or ratio <= limit
            result[f"{label}_{qname}_pass"] = passed
            if not passed:
                failures.append(f"{qname} {ratio:.4f} > {limit:.4f}")
        result[f"{label}_max"] = round(vals[-1], 1) if vals else 0.0
        result[f"{label}_failures"] = failures
        result[f"{label}_pass"] = not failures

    result["thresholds"] = th
    result["all_pass"] = all(
        result[f"{label}_pass"] for label in ("fine", "coarse", "overall")
    )
    return result


def format_layout_metrics(m: dict) -> str:
    """One-screen human-readable rendering (used by the CLI)."""
    lines = [
        f"edges {m['edges']}  placed {m['placed']}  "
        f"cross-layer skipped {m['skipped_cross_layer']}  "
        f"parent-unplaced {m['skipped_parent_unplaced']}",
        f"canvas diagonal {m['canvas_diagonal']:.0f}",
        "",
        "             edges       p50       p75       p90        max"
        "  p50/diag  p75/diag  p90/diag  gate",
    ]
    for label in ("fine", "coarse", "overall"):
        lines.append(
            f"{label:9} {m[f'{label}_edges']:>8} "
            f"{m[f'{label}_p50']:>9.1f} {m[f'{label}_p75']:>9.1f} "
            f"{m[f'{label}_p90']:>9.1f} {m[f'{label}_max']:>10.1f}  "
            f"{m[f'{label}_p50_ratio']:>8.4f}  {m[f'{label}_p75_ratio']:>8.4f}  "
            f"{m[f'{label}_p90_ratio']:>8.4f}  "
            f"{'PASS' if m[f'{label}_pass'] else 'FAIL'}"
        )
    lines.append("")
    if m["all_pass"]:
        lines.append("ALL PASS")
    else:
        lines.append("GATE FAILED")
        for label in ("fine", "coarse", "overall"):
            for failure in m.get(f"{label}_failures", []):
                lines.append(f"  {label}: {failure}")
    return "\n".join(lines)
