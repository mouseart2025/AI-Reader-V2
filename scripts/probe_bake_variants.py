#!/usr/bin/env python3
"""Compare baked terrain recipes on the region-variety criterion, with no browser.

Why this exists as its own probe: the criterion was first run on browser
screenshots, and that produced a wrong verdict. One of the frames used as a
"baseline" turned out to be missing every label and mark — the screenshot was
taken before the render settled — and ink removal moves the ratios. A second
pair of frames compared "v10 vs v13" when both were actually v13, because the
served version had moved on. Neither error is detectable from the numbers; both
are structural to measuring through a renderer.

The bake is a file. Comparing files has no timing, no cache and no settled-state
to get wrong, and the labels and marks are identical across variants anyway, so
they cannot be the thing that differs.

    python scripts/probe_bake_variants.py                       # 全部发现到的
    python scripts/probe_bake_variants.py v10 v13                # 指定
    python scripts/probe_bake_variants.py --dir <maps_dir> v10 v13

It imports `region_variety` from `probe_map_visual.py` rather than re-deriving
it, so this and the browser-side criterion can never drift apart — the same
reason `_frameclock.py` is shared in the audit repo.

Land is taken from the raster itself (the sea is blue, the land is warm), not
from a DOM mask: a DOM mask belongs to a screen rectangle, and the raster is
4096 px across its own canvas. Different question, different mask.
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from probe_map_visual import region_variety  # noqa: E402

DEFAULT_MAPS = Path.home() / ".ai-reader-v2" / "maps"


def land_mask_from_raster(im: Image.Image, step: int = 16) -> dict:
    """Sea is blue, land is warm. Coarse grid, majority vote per cell."""
    a = np.asarray(im.convert("RGB"), dtype=np.int16)
    h, w = a.shape[:2]
    warm = a[..., 0] > a[..., 2] + 6
    cols, rows = w // step, h // step
    sea = [
        "".join(
            "0" if warm[j * step:(j + 1) * step, i * step:(i + 1) * step].mean() > 0.5 else "1"
            for i in range(cols)
        )
        for j in range(rows)
    ]
    return {"x": 0, "y": 0, "step": step, "cols": cols, "rows": rows, "sea": sea}


def variants_in(maps_dir: Path) -> list[Path]:
    found = sorted(maps_dir.glob("terrain.v*.png"))
    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("versions", nargs="*", help="例如 v10 v13；缺省则用目录里全部")
    ap.add_argument("--dir", default=str(DEFAULT_MAPS), help="maps 根目录")
    ap.add_argument("--novel", default=None, help="novel_id；缺省取含版本文件最多的那个目录")
    ap.add_argument("--block", type=int, action="append", default=[], help="块大小，可给多次")
    args = ap.parse_args()

    root = Path(args.dir)
    if args.novel:
        maps = [root / args.novel]
    else:
        cands = [d for d in root.iterdir() if d.is_dir() and list(d.glob("terrain.v*.png"))]
        if not cands:
            print(f"在 {root} 下没找到任何 terrain.v*.png")
            return
        cands.sort(key=lambda d: -len(list(d.glob("terrain.v*.png"))))
        maps = [cands[0]]
    blocks = args.block or [128, 256]

    for maps_dir in maps:
        files = variants_in(maps_dir)
        if args.versions:
            want = {v if v.startswith("v") else f"v{v}" for v in args.versions}
            files = [f for f in files if f.stem.replace("terrain.", "") in want]
        if not files:
            print(f"{maps_dir}: 没有匹配的产物")
            continue

        print(f"# {maps_dir}")
        header = f"{'版本':<7}{'尺寸':<12}" + "".join(
            f"{('blk%d 明度 彩度 冷暖' % b):<26}" for b in blocks
        )
        print(header)
        for f in files:
            im = Image.open(f).convert("RGB")
            m = land_mask_from_raster(im)
            row = f"{f.stem.replace('terrain.', ''):<7}{str(im.size):<12}"
            for b in blocks:
                rv = region_variety(im, m, b)["channels"]
                if "error" in rv.get("lum", {}):
                    row += f"{'样本不足':<26}"
                else:
                    row += (f"{rv['lum']['ratio']:<9.3f}{rv['sat']['ratio']:<7.3f}"
                            f"{rv['warm']['ratio']:<10.3f}")
            print(row)
        print("  （三个数都是**块间 std ÷ 块内 std**；>1 = 区域之间比区域内部更像"
              "两个地方。<1 才是缺陷。全陆地块只取陆地占比 >0.7 的。）")


if __name__ == "__main__":
    main()
