"""Bake a terrain variant WITHOUT changing the noise seed.

Any harness that wants to compare two terrain recipes has to keep the real map
from being overwritten. The obvious way to do that is to pass a throwaway
`novel_id`, because `generate_terrain` names its output directory after it. That
is wrong, and silently so:

    seed_base = _stable_seed(novel_id)          # md5(novel_id)

the same string also selects the noise seed. A per-variant id therefore rewrites
the seed, and every variant bakes a DIFFERENT WORLD, so a comparison between two
variants measures the worlds and not the recipes. Measured on 西游记: two bakes
of the *same* recipe, one under `_rs_g00` and one under `_r2_r0`, differ by
max 174 and mean 43.9 of 255, with 99.7 % of pixels changing.

This already cost one round of decisions. `sweep.py`, the one-at-a-time
isolation that picked the shipped v6 recipe, baked v5 / nor / clean / soft /
v2ish / class4 under six different ids, so each "variant" was a different world
as well as a different recipe, and the ranking it produced has to be treated as
unverified until it is redone on one world.

The fix: pass the REAL novel_id and redirect only the output path. There is
exactly one call site of `terrain_path_for` (inside `generate_terrain`), so
patching it is surgical and no production signature has to change for the sake
of a test.

Use `self_check()` once per session — a harness that cannot reproduce its own
bake cannot compare anything.

    from bake_terrain_variant import bake, self_check
    self_check(OUT, NOVEL, locs, layout, canvas_width=cw, canvas_height=ch)
    bake(OUT / "iso_soft.png", NOVEL, locs, layout,
         canvas_width=cw, canvas_height=ch,
         _RELIEF_GAIN=0.10, _TEXTURE_AMPLITUDE=0.03,
         _VARIATION_STRENGTH=6.0, _PAPER_STRENGTH=0.0)
"""
import contextlib
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image

from src.services import map_layout_service as M

# Every module-level dial a variant might move. Reset explicitly on each bake so
# a variant cannot inherit the previous one's settings — an inherited dial is the
# same defect as an inherited seed, one level down.
DIALS = ("_RELIEF_GAIN", "_RIDGE_GAIN", "_TEXTURE_AMPLITUDE",
         "_VARIATION_STRENGTH", "_PAPER_STRENGTH", "_BLUR_FRAC",
         "_CLASS_OCTAVES", "_MOIST_OCTAVES", "_RELIEF_OCTAVES",
         "_RIDGE_BASE_WL", "_RIDGE_OCTAVES")


@contextlib.contextmanager
def _redirected(out_path: Path):
    saved = M.terrain_path_for
    M.terrain_path_for = lambda _nid: out_path
    try:
        yield
    finally:
        M.terrain_path_for = saved


def bake(out_path: Path, novel_id: str, locations, layout,
         size: int | None = None, canvas_width: int | None = None,
         canvas_height: int | None = None, **overrides) -> Path:
    """Bake one variant to `out_path`, seeded from the REAL `novel_id`."""
    saved = {k: getattr(M, k) for k in DIALS if hasattr(M, k)}
    try:
        for k, v in saved.items():
            setattr(M, k, v)                     # reset, never inherit
        for k, v in overrides.items():
            if k not in saved:
                raise KeyError(f"unknown dial {k!r}; known: {sorted(saved)}")
            setattr(M, k, v)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with _redirected(out_path):
            p = M.generate_terrain(locations, layout, novel_id, size=size,
                                   canvas_width=canvas_width,
                                   canvas_height=canvas_height)
    finally:
        for k, v in saved.items():
            setattr(M, k, v)
    if p is None:
        raise RuntimeError("generate_terrain returned None")
    return Path(p)


def digest(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()[:12]


def max_abs_diff(a: Path, b: Path) -> tuple[int, float]:
    x = np.asarray(Image.open(a).convert("RGB"), dtype=np.int16)
    y = np.asarray(Image.open(b).convert("RGB"), dtype=np.int16)
    if x.shape != y.shape:
        return (-1, -1.0)
    d = np.abs(x - y)
    return (int(d.max()), float(d.mean()))


def self_check(out_dir: Path, novel_id: str, locations, layout,
               **kwargs) -> bool:
    """Prove the harness before using it. Returns True if it is trustworthy.

    Bakes the same variant twice under the real id and once under a fake one.
    The first pair must be identical; the second must not. That second number is
    the size of the mistake this module exists to prevent, so it is printed
    rather than asserted — it is evidence, not a condition.
    """
    a = bake(out_dir / "_chk_real_a.png", novel_id, locations, layout, **kwargs)
    b = bake(out_dir / "_chk_real_b.png", novel_id, locations, layout, **kwargs)
    c = bake(out_dir / "_chk_fake.png", "not-a-novel-id", locations, layout,
             **kwargs)
    m, mean = max_abs_diff(a, b)
    ok = m == 0
    print(f"  same novel_id twice:  max|diff| {m}  mean {mean:.4f}"
          f"  {'DETERMINISTIC' if ok else '*** NOT DETERMINISTIC ***'}")
    m2, mean2 = max_abs_diff(a, c)
    print(f"  real id vs a fake id: max|diff| {m2}  mean {mean2:.1f}"
          f"  <- the seed confound this module removes")
    for p in (a, b, c):
        p.unlink(missing_ok=True)
    return ok
