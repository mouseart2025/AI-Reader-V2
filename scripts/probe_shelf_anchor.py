#!/usr/bin/env python3
"""probe_shelf_anchor.py — 浅滩带与**画出来的**海岸线是不是同一个源。

## 为什么量这个

浅滩圈是从 `land_mask` 追踪出来的，而 `land_mask` 来自**未位移**的轮廓；
画出来的海岸线却是那条轮廓经过位移（如今还加了中点位移，幅度 ~30 canvas 单位）之后的形状。

⇒ 若两者不同源，沿岸会出现两类可见缺陷：
- 海岸线**向外鼓**的地方：浅滩圈的内边界落在**陆地里面** ⇒ 带子从岸上开始，可见的浅滩条变窄甚至消失；
- 海岸线**向内退**的地方：浅滩圈与画出来的岸之间留出**一圈露底的缝**。

这类缺陷是"同一个东西必须量自同一个源"的典型（本仓已栽过两次），
而且它**不会**在"平均宽度"这类统计里露头 —— 得看**逐点的符号**。

## 判据

把画出来的海岸线圈栅格化成陆地掩膜，再沿每条浅滩圈逐点问两件事：
1. **符号**：这个点在陆地里面吗？在里面的比例就是"带子起点落在岸上"的比例。
2. **距离**：到最近海岸线顶点的距离（KD-tree），给分位数。

健康：内圈的点几乎全在**海**里，且到岸距离就是设计宽度、**方差不大**且**不穿过零**。

## 用法

  node scripts/dump_coastlines.cjs <URL> --out /tmp/coast.json
  python3 scripts/probe_shelf_anchor.py /tmp/coast.json [--cell 8]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).parent))
from probe_coastline_morphology import parse_path, sample_cubics  # noqa: E402


def rings_of(shapes, per_seg=6):
    out = []
    for s in shapes:
        cubics, _ = parse_path(s["d"])
        if not cubics:
            continue
        out.append(sample_cubics(cubics, per_seg))
    return out


def anchor_stats(dump: str, cell: float = 8.0) -> dict:
    """浅滩锚点统计。**判据本体**，`main()` 与 `probe_style_lock.py` 共用这一个实现。"""
    data = json.loads(Path(dump).read_text())
    coast_shapes = data["groups"].get("coastline") or []
    shelf_shapes = data["groups"].get("shelf") or []
    if not coast_shapes or not shelf_shapes:
        return {"error": "dump 里缺少 coastline 或 shelf"}

    coasts = rings_of(coast_shapes)
    shelves = rings_of(shelf_shapes)
    all_coast = np.concatenate(coasts, 0)
    all_shelf = np.concatenate(shelves, 0)
    both = np.concatenate([all_coast, all_shelf], 0)
    lo = np.floor(both.min(axis=0) / cell) * cell - 4 * cell
    hi = np.ceil(both.max(axis=0) / cell) * cell + 4 * cell
    W = int((hi[0] - lo[0]) / cell) + 1
    H = int((hi[1] - lo[1]) / cell) + 1

    img = Image.new("1", (W, H), 0)
    d = ImageDraw.Draw(img)
    for r in coasts:
        pts = [((p[0] - lo[0]) / cell, (p[1] - lo[1]) / cell) for p in r]
        if len(pts) >= 3:
            d.polygon(pts, fill=1)
    # ⚠️ PIL 是 (宽=x, 高=y)，np.asarray 出来是 [行=y, 列=x]。索引必须是 [ys, xs]。
    mask = np.asarray(img, dtype=bool)
    tree = cKDTree(all_coast)

    rings = []
    for i, r in enumerate(shelves):
        xs = np.clip(((r[:, 0] - lo[0]) / cell).astype(int), 0, mask.shape[1] - 1)
        ys = np.clip(((r[:, 1] - lo[1]) / cell).astype(int), 0, mask.shape[0] - 1)
        inside = float(mask[ys, xs].mean() * 100.0)
        dist, _ = tree.query(r)
        p = np.percentile(dist, [10, 50, 90, 99])
        rings.append({
            "index": i, "points": int(len(r)), "inside_land_pct": round(inside, 2),
            "dist_p10": round(float(p[0]), 1), "dist_p50": round(float(p[1]), 1),
            "dist_p90": round(float(p[2]), 1), "dist_p99": round(float(p[3]), 1),
        })

    return {
        "cell": cell,
        "coast_rings": len(coasts),
        "shelf_rings": len(shelves),
        "grid": (W, H),
        "land_pct": round(float(mask.mean() * 100.0), 1),
        "rings": rings,
        "worst_inside_pct": round(max((r["inside_land_pct"] for r in rings), default=0.0), 2),
        "max_dist_p50": round(max((r["dist_p50"] for r in rings), default=0.0), 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--cell", type=float, default=8.0, help="陆地栅格化的格边长（canvas 单位）")
    args = ap.parse_args()

    st = anchor_stats(args.dump, args.cell)
    if "error" in st:
        sys.exit(st["error"])
    print(f"栅格 {st['grid'][0]}x{st['grid'][1]} @ {st['cell']} 单位   "
          f"海岸线 {st['coast_rings']} 圈   浅滩 {st['shelf_rings']} 圈")
    print(f"陆地占画布 {st['land_pct']}%")
    print(f"\n{'浅滩圈':<8}{'点数':>7}{'在陆内':>9}{'距离 p10':>10}{'p50':>8}{'p90':>8}{'p99':>8}")
    print('-' * 58)
    for r in st["rings"]:
        print(f"#{r['index']:<7}{r['points']:>7}{r['inside_land_pct']:>8.1f}%"
              f"{r['dist_p10']:>10.1f}{r['dist_p50']:>8.1f}{r['dist_p90']:>8.1f}{r['dist_p99']:>8.1f}")

    worst = st["worst_inside_pct"]
    print(f"\n最差的一圈有 {worst:.1f}% 的采样点落在**陆地里面**")
    print("   ⚠️ 残余比例随栅格变细而下降 ⇒ 是量化伪影；而且 `#sea-mask` 用同一组 landmasses")
    print("      把 `#shelf` 裁到海里，所以几何上的亚格重叠在**着色时就被抹掉**。")
    if worst > 5:
        print("❌ 判定：浅滩带的起点锚在一个画面上不存在的形状上 —— 与海岸线**不同源**。")
    elif worst > 2:
        print("△ 判定：少量重叠，高于量化量级，值得查。")
    else:
        print("✅ 判定：浅滩带贴的是画出来的海岸线，两者同源。")


if __name__ == "__main__":
    main()
