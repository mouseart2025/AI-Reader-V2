#!/usr/bin/env python3
"""地图视觉体检：把"好不好看"压成可复算的数。

用法：
    python scripts/probe_map_visual.py SHOT.png [--label-box X,Y,W,H ...]

为什么需要它：美术方向如果没有判据，就只能靠"看起来好像好一点"，
而那是分母效应最容易骗人的地方。这里给三条**能算**的判据：

1. **价值结构（value structure）** —— 海与陆的平均亮度差。
   地图读不读得出来，首先取决于陆海是否分离。按冷暖分色做分割
   （羊皮纸暖 → R>B；海水冷 → B>R），报 ΔL 与占比。
   经验线：ΔL < 10 级 ⇒ 陆海糊成一片（"大陆像污渍"）。
2. **标签墨色** —— 在给定的标签框里取最暗像素，与它所在的背景比对比度。
   小字要求 ≥ 4.5:1（WCAG AA），低于 3:1 基本等于没写。
3. **调色** —— 量化后的主色及其占比，用来盯"整幅是不是只剩一个色阶"。

⚠️ 这台量具只做**分割与统计**，不重写渲染逻辑（渲染在被测前端里）。
分割判据是 R-B 的符号，这是本项目的实际调色决定的（暖陆冷海）；
若哪天改成冷陆暖海，这个判据必须先改，否则量出来的是错的东西。
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from PIL import Image


def _lum(rgb) -> float:
    """相对亮度（sRGB, WCAG 定义）。"""
    def ch(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (ch(float(v)) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg) -> float:
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def split_land_sea(im: Image.Image, roi=None):
    """按 R-B 符号分陆/海，返回各自的平均色与占比。

    ⚠️ **必须给 roi**。第一版在全图上跑，把页面的深色 chrome
    （`rgb(8,8,8)`，占 22.7%）也算进了"海"里 —— 于是 ΔL 报成 57.2「分离清楚」，
    而地图本身的值域其实只有 144–200。**量具把非被测像素算进来，就会给出
    一个漂亮且错误的结论**。所以这里默认要求调用方指定地图视口。
    """
    px = im.load()
    if roi:
        x0, y0, x1, y1 = roi
    else:
        x0, y0, x1, y1 = 0, 0, im.size[0], im.size[1]
    land = [0, 0, 0, 0]
    sea = [0, 0, 0, 0]
    for y in range(y0, y1, 2):
        for x in range(x0, x1, 2):
            r, g, b = px[x, y][:3]
            bucket = land if (r - b) > 0 else sea
            bucket[0] += r
            bucket[1] += g
            bucket[2] += b
            bucket[3] += 1
    out = {}
    for name, b in (("land", land), ("sea", sea)):
        n = max(b[3], 1)
        avg = (b[0] / n, b[1] / n, b[2] / n)
        out[name] = {"share": b[3] / max(land[3] + sea[3], 1), "avg": avg,
                     "lumY": round(_lum(avg) * 255, 1)}
    return out


def darkest_in(im: Image.Image, box):
    x, y, w, h = box
    c = im.crop((x, y, x + w, y + h)).convert("RGB")
    px = list(c.getdata())
    px.sort(key=_lum)
    dark = px[: max(1, len(px) // 50)]          # 最暗的 2%
    avg = tuple(sum(p[i] for p in dark) / len(dark) for i in range(3))
    return avg, px[len(px) // 2]                 # (墨色, 该框的中位色=近似背景)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("shot")
    ap.add_argument("--roi", metavar="X0,Y0,X1,Y1",
                    help="只统计地图视口（必给，否则页面 chrome 会污染统计）")
    ap.add_argument("--label-box", action="append", default=[],
                    metavar="X,Y,W,H", help="标签取样框，可给多次")
    ap.add_argument("--crop", metavar="X,Y,W,H",
                    help="把该区域放大 6 倍另存，用来人眼复核取样框是否框到字")
    ap.add_argument("--top-colors", type=int, default=0)
    args = ap.parse_args()

    im = Image.open(args.shot).convert("RGB")
    print(f"# {Path(args.shot).name}  {im.size[0]}x{im.size[1]}")

    if args.crop:
        x, y, w, h = (int(v) for v in args.crop.split(","))
        c = im.crop((x, y, x + w, y + h))
        c = c.resize((c.width * 6, c.height * 6), Image.NEAREST)
        out = "/tmp/probe_crop.png"
        c.save(out)
        print(f"裁切放大 6x -> {out}  ({w}x{h} @ {x},{y})")

    roi = tuple(int(v) for v in args.roi.split(",")) if args.roi else None
    if roi is None:
        print("⚠️ 未给 --roi：统计会被页面 chrome 污染，结论不可用")

    ls = split_land_sea(im, roi)
    land, sea = ls["land"], ls["sea"]
    print("\n[价值结构]")
    print(f"  陆 占比 {land['share'] * 100:5.1f}%  均色 rgb({land['avg'][0]:.0f},"
          f"{land['avg'][1]:.0f},{land['avg'][2]:.0f})  亮度 {land['lumY']}")
    print(f"  海 占比 {sea['share'] * 100:5.1f}%  均色 rgb({sea['avg'][0]:.0f},"
          f"{sea['avg'][1]:.0f},{sea['avg'][2]:.0f})  亮度 {sea['lumY']}")
    delta = abs(land["lumY"] - sea["lumY"])
    print(f"  ΔL(海陆亮度差) = {delta:.1f} 级"
          f"   {'✅ ≥20 分离清楚' if delta >= 20 else '⚠️ <20 陆海偏糊' if delta >= 10 else '❌ <10 糊成一片'}")
    print(f"  陆海亮度对比度 = {contrast(land['avg'], sea['avg']):.2f}:1")

    for spec in args.label_box:
        x, y, w, h = (int(v) for v in spec.split(","))
        ink, bg = darkest_in(im, (x, y, w, h))
        ratio = contrast(ink, bg)
        print(f"\n[标签框 {spec}]  墨色 rgb({ink[0]:.0f},{ink[1]:.0f},{ink[2]:.0f})"
              f"  背景 rgb({bg[0]},{bg[1]},{bg[2]})")
        print(f"  对比度 {ratio:.2f}:1"
              f"   {'✅ AA' if ratio >= 4.5 else '⚠️ 偏弱' if ratio >= 3 else '❌ 读不出'}")

    if args.top_colors:
        q = im.convert("RGB")
        data = list(q.getdata())
        step = max(1, len(data) // 200_000)
        c = Counter((r // 8 * 8, g // 8 * 8, b // 8 * 8) for r, g, b in data[::step])
        print(f"\n[主色 top{args.top_colors}]（量化到 8 级）")
        for col, n in c.most_common(args.top_colors):
            print(f"  rgb{col}  {n / sum(c.values()) * 100:5.1f}%")


if __name__ == "__main__":
    sys.exit(main())
