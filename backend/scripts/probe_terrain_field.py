"""Does the biome field ever reach the colourful part of the palette?

The land renders grey even with the terrain at full opacity, so the question is
whether the Whittaker palette is too muted or the field never samples the cells
that have colour. Guessing here would be a coin flip, so this records the real
elevation/moisture arrays by wrapping `_spread_unit` — the actual function
`generate_terrain` calls — rather than re-deriving the field in the probe (a
probe that re-implements its subject just reproduces the bug it is hunting).

Run from `backend/` with the backend venv. Writes only to a throwaway novel dir.
"""
import json
import shutil
import sys
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.services.map_layout_service as mls      # noqa: E402
from src.infra.config import DATA_DIR               # noqa: E402

NOVEL = "2f19030d-66e2-4ab5-9593-2d67f07af006"
BENCH = "_terrain_bench"
API = (f"http://127.0.0.1:8000/api/novels/{NOVEL}/map"
       "?chapter_start=1&chapter_end=100")

captured: list[np.ndarray] = []
_orig_spread = mls._spread_unit


def spy(field, *a, **kw):
    out = _orig_spread(field, *a, **kw)
    captured.append(np.asarray(out, dtype=np.float64))
    return out


def main() -> None:
    mls._spread_unit = spy
    with urllib.request.urlopen(API, timeout=180) as r:
        data = json.loads(r.read().decode())
    locations = data.get("locations") or []
    canvas = data.get("canvas_size") or {}
    cw, ch = canvas.get("width"), canvas.get("height")
    layout = {i["name"]: (i["x"], i["y"]) for i in data.get("layout") or []}

    # 256 keeps the probe fast; the field is resolution-independent because
    # every radius is a fraction of the raster's long side.
    mls.generate_terrain(locations, layout, BENCH, size=256,
                         canvas_width=cw, canvas_height=ch)
    mls._spread_unit = _orig_spread

    for i, f in enumerate(captured[:2]):
        name = "elevation" if i == 0 else "moisture"
        q = np.percentile(f, [0, 5, 25, 50, 75, 95, 100])
        print(f"{name:<10} min {q[0]:.3f}  p5 {q[1]:.3f}  p25 {q[2]:.3f}  "
              f"med {q[3]:.3f}  p75 {q[4]:.3f}  p95 {q[5]:.3f}  max {q[6]:.3f}")
        # grid index the renderer uses (generate_terrain: e_idx = elev * 4)
        idx = np.clip(f * 4.0, 0.0, 4.0)
        lo = np.clip(np.floor(idx).astype(int), 0, 3)
        print(f"{'':<10} grid cell index histogram (0-3):",
              [f"{v*100:.0f}%" for v in
               np.bincount(lo.ravel(), minlength=4)[:4] / lo.size])

    if len(captured) >= 2:
        elev, moist = captured[0], captured[1]
        ei = np.clip(np.floor(np.clip(elev * 4, 0, 4)).astype(int), 0, 3)
        mi = np.clip(np.floor(np.clip(moist * 4, 0, 4)).astype(int), 0, 3)
        grid = np.array(mls._WHITTAKER_GRID, dtype=float)
        used = np.zeros((4, 4), dtype=int)
        for a in range(4):
            for b in range(4):
                used[a, b] = int(((ei == a) & (mi == b)).sum())
        total = used.sum()
        print("\nwhich palette cells the field actually visits "
              f"(row = elevation, col = moisture), % of land pixels:")
        for a in range(4):
            row = "  ".join(f"{used[a, b]/total*100:5.1f}" for b in range(4))
            print(f"  e{a}  {row}")
        pxl = grid[ei, mi].reshape(-1, 3)
        chroma = (pxl.max(axis=1) - pxl.min(axis=1))
        print(f"\nbiome colour chroma actually visited: mean {chroma.mean():.1f}"
              f"  p95 {np.percentile(chroma, 95):.1f}  max {chroma.max():.1f}")
        print(f"biome colour L* proxy (mean channel): {pxl.mean():.1f}")
        print(f"never visited cells (of 16): "
              f"{int((used == 0).sum())} → "
              f"{[(a, b) for a in range(4) for b in range(4) if used[a, b] == 0]}")

    shutil.rmtree(DATA_DIR / "maps" / BENCH, ignore_errors=True)


if __name__ == "__main__":
    main()
