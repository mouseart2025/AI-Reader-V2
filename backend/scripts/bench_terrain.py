"""What does terrain resolution actually cost, on real data?

`generate_terrain` defaults to size=1024 and no caller overrides it, so an
8000x4500 canvas gets an image stretched 7.8x. Before changing that, the price
of a larger bake has to be a number rather than a guess.

Run from `backend/` with the backend venv. Writes only to a throwaway novel dir
(`_terrain_bench`) and removes it.
"""
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.infra.config import DATA_DIR            # noqa: E402
from src.services.map_layout_service import generate_terrain  # noqa: E402

NOVEL = "2f19030d-66e2-4ab5-9593-2d67f07af006"
BENCH = "_terrain_bench"
SIZES = [1024, 2048, 3072, 4096, 6144]
# 127.0.0.1, never `localhost`: a system HTTP proxy intercepts the name here and
# answers 502 (see the project notes).
API = (f"http://127.0.0.1:8000/api/novels/{NOVEL}/map"
       "?chapter_start=1&chapter_end=100")


def fetch() -> dict:
    with urllib.request.urlopen(API, timeout=180) as r:
        return json.loads(r.read().decode())


def main() -> None:
    data = fetch()
    locations = data.get("locations") or []
    canvas = data.get("canvas_size") or {}
    cw, ch = canvas.get("width"), canvas.get("height")
    layout = {i["name"]: (i["x"], i["y"]) for i in data.get("layout") or []}
    print(f"locations {len(locations)}  layout {len(layout)}  canvas {cw}x{ch}")
    if not layout or not cw:
        print("!! no layout on canvas in the response; nothing to measure")
        return

    from PIL import Image
    bench_dir = DATA_DIR / "maps" / BENCH
    print(f"\n{'size':>6}{'bake s':>9}{'PNG MB':>9}{'upscale':>10}"
          f"{'MPx':>8}")
    for size in SIZES:
        t0 = time.perf_counter()
        path = generate_terrain(locations, layout, BENCH,
                                size=size, canvas_width=cw, canvas_height=ch)
        dt = time.perf_counter() - t0
        if not path:
            print(f"{size:>6}  failed")
            continue
        p = Path(path)
        with Image.open(p) as im:
            w, h = im.size
        print(f"{size:>6}{dt:>9.2f}{p.stat().st_size/1e6:>9.2f}"
              f"{cw/w:>9.2f}x{w*h/1e6:>7.1f}")

    if bench_dir.exists():
        shutil.rmtree(bench_dir, ignore_errors=True)
    print(f"\ncleaned {bench_dir}")


if __name__ == "__main__":
    main()
