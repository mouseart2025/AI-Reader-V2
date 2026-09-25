"""Measure layout geometry quality from a dumped coordinate CSV.

Input CSV needs columns: name, parent, tier, coord_layer, x, y
(produced by ai-reader-internal/scripts/map-layout-verify/dump_layout.py).

Usage:
    python scripts/measure_layout_quality.py coords.csv --canvas 8000x4500
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.layout_metrics import (  # noqa: E402
    compute_layout_metrics,
    format_layout_metrics,
)


def load_csv(path: str) -> tuple[dict, dict, dict, dict]:
    coords: dict[str, tuple[float, float]] = {}
    parents: dict[str, str] = {}
    tiers: dict[str, str] = {}
    layers: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("x") in ("", None) or row.get("y") in ("", None):
                continue
            name = row["name"]
            coords[name] = (float(row["x"]), float(row["y"]))
            if row.get("parent"):
                parents[name] = row["parent"]
            if row.get("tier"):
                tiers[name] = row["tier"]
            if row.get("coord_layer"):
                layers[name] = row["coord_layer"]
    return coords, parents, tiers, layers


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--canvas", default="8000x4500", help="WxH the layout was solved on")
    ap.add_argument("--json", help="also write the metrics as JSON here")
    args = ap.parse_args()

    width, height = (float(v) for v in args.canvas.lower().split("x"))
    coords, parents, tiers, layers = load_csv(args.csv)
    metrics = compute_layout_metrics(coords, parents, tiers, layers, (width, height))

    print(f"# {args.csv}")
    print(format_layout_metrics(metrics))

    if args.json:
        Path(args.json).write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nwritten: {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
