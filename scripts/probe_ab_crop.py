#!/usr/bin/env python3
"""probe_ab_crop.py — 两张同机位截图在 **1:1** 下的并排对账。

## 为什么需要它

今天的教训：`k=9.75` 的 LOD 改前 / 改后看全幅图，会得出"改后几乎没纹理了"的结论。
那是**缩放的假象** —— 截图 1600 px 被显示端缩到 ~1080 px，1.1 px 的描边被压到
0.74 px；改前的记号长 65 px（长线，缩小后仍在），改后只有 17 px（短线，缩小后消失）。
**同一个笔宽，长度不同，在缩小视图里的存活率就不同。** 拿缩小视图比两种尺度，
比的是显示缩放，不是图层。

所以比"纹理在不在了"必须 **1:1 裁同一块**，并且把两张并排放在一张输出图上，
省得靠记忆对比（记忆里的对比会被上一张图污染 —— 今天已经栽过一次：
"v10 vs v13"两张其实都是 v13）。

## 用法

  python3 scripts/probe_ab_crop.py A.png B.png --rect 400,300,400,260 \
      --labels legacy,LOD --out /tmp/ab.png [--zoom 2]

`--zoom` 是**最近邻**整数放大，只为看清像素，不引入插值（插值会自己造纹理）。
默认 1:1。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("shots", nargs="+")
    ap.add_argument("--rect", required=True, metavar="X,Y,W,H")
    ap.add_argument("--labels", default="A,B", metavar="A,B")
    ap.add_argument("--zoom", type=int, default=1)
    ap.add_argument("--out", default="/tmp/ab_crop.png")
    args = ap.parse_args()

    x, y, w, h = (int(v) for v in args.rect.split(","))
    labels = args.labels.split(",") if args.labels else [Path(p).stem for p in args.shots]
    if len(labels) != len(args.shots):
        sys.exit(f"--labels 需要 {len(args.shots)} 个逗号分隔的名字")

    tiles = []
    for path in args.shots:
        im = Image.open(path).convert("RGB")
        if x + w > im.width or y + h > im.height:
            sys.exit(f"{Path(path).name} 只有 {im.size}，装不下 rect {args.rect}")
        t = im.crop((x, y, x + w, y + h))
        if args.zoom > 1:
            t = t.resize((t.width * args.zoom, t.height * args.zoom), Image.NEAREST)
        tiles.append(t)

    bar = 26
    gap = 8
    n = len(tiles)
    out = Image.new(
        "RGB",
        (tiles[0].width * n + gap * (n - 1), tiles[0].height + bar),
        (24, 24, 28),
    )
    for i, t in enumerate(tiles):
        out.paste(t, (i * (t.width + gap), bar))
    d = ImageDraw.Draw(out)
    for i, lab in enumerate(labels):
        d.text((i * (tiles[0].width + gap) + 6, 6), lab, fill=(235, 235, 240))

    out.save(args.out)
    print(f"1:{args.zoom} 裁 {args.rect} 并排 {n} 张 -> {args.out}  ({out.width}x{out.height})")


if __name__ == "__main__":
    main()
