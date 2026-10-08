"""Post-layout plausibility audit.

The map pipeline ends at rendering: the solver reports its own constraint
satisfaction, but nothing checks that the result is *semantically* sane.
The 西游记 map shipped with three of the four continents piled at the
same point (东胜神洲/南赡部洲/北俱芦洲 54-75 px apart), 北俱芦洲 with zero
constraints parked on that pile, and 大唐国 3000 px from its own capital —
all invisible to every existing metric.

This module is the read-only audit pass that runs after the layout is
finalised and reports such violations. Five checks, each motivated by an
observed failure:

1. Named-cardinal conformance — a name that says 东/南/西/北 *洲/海/洋/岛
   carries a direction the text never states explicitly (canonical
   cosmology is common knowledge, so the LLM extracts no constraint for
   it: 北俱芦洲 had zero). The name is the prior; check it is honoured.
2. Sibling spread — same-parent, same-tier siblings with no mutual
   separation collapse onto one attractor (the continent pile).
3. Containment consistency — a high-confidence `contains` whose endpoints
   land far apart (大唐国 contains 长安城, 3000 px) means containment lost
   to some other force and nobody was told.
4. Direction-constraint conformance — which stated directions does the
   final layout actually violate (南赡部洲 west_of 西牛贺洲)? This is also
   the feedback channel for constraint cleaning: a constraint the layout
   keeps violating is a candidate for demotion.
5. Sea-on-land — regression alarm for the class of bug 南海 was.

The audit never mutates anything and must never break map serving: the
call site wraps it in try/except.
"""

from __future__ import annotations

import math
from typing import Any

from src.models.chapter_fact import classify_spatial_relation

# Names starting with a cardinal and ending in one of these suffixes carry a
# canvas-level direction prior. Kingdoms/cities are excluded: 东土大唐's 东
# is relative to 天竺, not to the canvas.
_CARDINAL_SUFFIXES = ("洲", "海", "洋", "岛", "海湾")
_CARDINALS = {
    "东": (1.0, 0.0),
    "南": (0.0, 1.0),   # canvas y grows downward = south
    "西": (-1.0, 0.0),
    "北": (0.0, -1.0),
}

# 8-wind tolerance: the expected bearing plus its two neighbours pass.
_DIR_TO_DEG = {
    "east_of": 0, "southeast_of": 45, "south_of": 90, "southwest_of": 135,
    "west_of": 180, "northwest_of": 225, "north_of": 270, "northeast_of": 315,
}


def _bearing_deg(dx: float, dy: float) -> float:
    """Canvas bearing in degrees: 0 = east, 90 = south, counterclockwise-negative free."""
    return math.degrees(math.atan2(dy, dx)) % 360


def _angular_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _is_sea_body(loc: dict) -> bool:
    """Local copy of the sea-body predicate (visualization_service has the
    runtime one; importing it here would be circular)."""
    icon = loc.get("icon", "generic")
    if icon == "island":
        return False
    if icon == "ocean":
        return True
    if icon != "water":
        return False
    loc_type = loc.get("type", "")
    return loc_type in ("海", "洋", "海洋", "大海", "海域") or loc.get("name", "").endswith(("海", "洋"))


def _ring_contains(pts: list, x: float, y: float) -> bool:
    """Even-odd point-in-polygon; pure Python so the audit has no heavy deps."""
    inside = False
    n = len(pts)
    for i in range(n):
        j = i - 1
        xi, yi = pts[i][0], pts[i][1]
        xj, yj = pts[j][0], pts[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
    return inside


def _on_land(landmasses: list, x: float, y: float) -> bool:
    for lm in landmasses or []:
        ring = lm.get("coastline") or []
        if len(ring) < 3 or not _ring_contains(ring, x, y):
            continue
        in_hole = any(
            len(h) >= 3 and _ring_contains(h, x, y)
            for h in lm.get("holes") or []
        )
        if not in_hole:
            return True
    return False


def audit_map_layout(
    locations: list[dict],
    layout_data: list[dict],
    spatial_constraints: list[dict],
    landmasses: list[dict],
    canvas_width: float,
    canvas_height: float,
    *,
    max_findings: int = 20,
) -> dict[str, Any]:
    """Run all five plausibility checks. Returns a JSON-serialisable report.

    Lists are capped at `max_findings` entries; the full counts are always
    in `summary`. `ok` means zero violations across every check.
    """
    coords = {
        item["name"]: (item["x"], item["y"])
        for item in layout_data
        if item.get("name") and not item.get("is_portal")
    }
    loc_by_name = {loc["name"]: loc for loc in locations}
    cx, cy = canvas_width / 2, canvas_height / 2
    diagonal = math.hypot(canvas_width, canvas_height)

    # ── 1. Named-cardinal conformance ────────────────────────────────
    cardinal_violations = []
    cardinal_checked = 0
    # Dead-band around the centre line: a few percent of canvas across the
    # line is noise (西洋大海 pinned 336 px east of centre on an 8000 px
    # canvas is still "the western sea" for every practical purpose).
    dead_x = 0.10 * canvas_width / 2
    dead_y = 0.10 * canvas_height / 2
    for name, (x, y) in coords.items():
        if not name or name[0] not in _CARDINALS or not name.endswith(_CARDINAL_SUFFIXES):
            continue
        cardinal_checked += 1
        vx, vy = _CARDINALS[name[0]]
        # Clearly wrong side of the canvas on the named axis = violation.
        if (
            (vx != 0 and (x - cx) * vx <= -dead_x)
            or (vy != 0 and (y - cy) * vy <= -dead_y)
        ):
            cardinal_violations.append({
                "name": name,
                "expected": name[0],
                "actual_dx": round(x - cx),
                "actual_dy": round(y - cy),
            })

    # ── 2. Sibling spread (same parent, same tier) ───────────────────
    sibling_violations = []
    sibling_groups: dict[tuple, list[str]] = {}
    for loc in locations:
        parent = loc.get("parent")
        tier = loc.get("tier", "")
        if parent and tier in ("continent",):
            sibling_groups.setdefault((parent, tier), []).append(loc["name"])
    min_spread = 0.05 * diagonal
    for (parent, tier), names in sibling_groups.items():
        placed = [n for n in names if n in coords]
        if len(placed) < 3:
            continue
        worst_pair = None
        worst_d = min_spread
        for i, a in enumerate(placed):
            for b in placed[i + 1:]:
                d = math.dist(coords[a], coords[b])
                if d < worst_d:
                    worst_d = d
                    worst_pair = (a, b)
        if worst_pair:
            sibling_violations.append({
                "parent": parent,
                "tier": tier,
                "closest_pair": list(worst_pair),
                "distance": round(worst_d),
                "threshold": round(min_spread),
            })

    # ── 3. Containment consistency (high-confidence contains) ────────
    # Threshold scales with the container's tier: 天下 legitimately spans
    # the canvas, but a hall must keep its rooms close (红楼梦's garden map
    # flagged 暖阁 1456 px from its 花厅 — a real defect).
    _CONTAINMENT_LIMIT = {
        "world": 0.40, "continent": 0.35, "region": 0.25,
        "kingdom": 0.20, "city": 0.15,
    }
    containment_violations = []
    for c in spatial_constraints:
        if classify_spatial_relation(c.get("relation_type", "")) != "hierarchy":
            continue
        if c.get("confidence") != "high":
            continue
        src, tgt = c.get("source"), c.get("target")
        if src not in coords or tgt not in coords:
            continue
        src_tier = (loc_by_name.get(src) or {}).get("tier", "")
        limit = _CONTAINMENT_LIMIT.get(src_tier, 0.12) * diagonal
        d = math.dist(coords[src], coords[tgt])
        if d > limit:
            containment_violations.append({
                "source": src,
                "target": tgt,
                "source_tier": src_tier,
                "distance": round(d),
                "threshold": round(limit),
                "value": c.get("value", ""),
            })
    containment_violations.sort(key=lambda v: -v["distance"])

    # ── 4. Direction-constraint conformance ──────────────────────────
    direction_violations = []
    direction_checked = 0
    for c in spatial_constraints:
        if c.get("relation_type") != "direction":
            continue
        expected = _DIR_TO_DEG.get(c.get("value", ""))
        if expected is None or c.get("confidence") not in ("high", "medium"):
            continue
        src, tgt = c.get("source"), c.get("target")
        if src not in coords or tgt not in coords:
            continue
        direction_checked += 1
        # Convention (verified against satisfied pairs): source is <value> of target.
        dx = coords[src][0] - coords[tgt][0]
        dy = coords[src][1] - coords[tgt][1]
        if dx == 0 and dy == 0:
            gap = 180.0
        else:
            gap = _angular_gap(_bearing_deg(dx, dy), expected)
        if gap > 45:
            direction_violations.append({
                "source": src,
                "target": tgt,
                "value": c["value"],
                "confidence": c["confidence"],
                "angular_error": round(gap),
            })
    direction_violations.sort(key=lambda v: (-v["angular_error"]))

    # ── 5. Sea on land ───────────────────────────────────────────────
    sea_on_land = []
    for name, (x, y) in coords.items():
        loc = loc_by_name.get(name)
        if not loc or not _is_sea_body(loc):
            continue
        if _on_land(landmasses, x, y):
            sea_on_land.append({"name": name, "x": round(x), "y": round(y)})

    summary = {
        "cardinal": f"{cardinal_checked - len(cardinal_violations)}/{cardinal_checked} ok",
        "sibling_spread": f"{len(sibling_violations)} groups too close",
        "containment": f"{len(containment_violations)} high-conf contains violated",
        "direction": f"{direction_checked - len(direction_violations)}/{direction_checked} ok",
        "sea_on_land": f"{len(sea_on_land)} seas on land",
    }
    violation_count = (
        len(cardinal_violations)
        + len(sibling_violations)
        + len(containment_violations)
        + len(direction_violations)
        + len(sea_on_land)
    )
    return {
        "ok": violation_count == 0,
        "violation_count": violation_count,
        "summary": summary,
        "cardinal_violations": cardinal_violations[:max_findings],
        "sibling_spread_violations": sibling_violations[:max_findings],
        "containment_violations": containment_violations[:max_findings],
        "direction_violations": direction_violations[:max_findings],
        "sea_on_land": sea_on_land[:max_findings],
    }
