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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--cell", type=float, default=8.0, help="陆地栅格化的格边长（canvas 单位）")
    args = ap.parse_args()

    data = json.loads(Path(args.dump).read_text())
    coast_shapes = data["groups"].get("coastline") or []
    shelf_shapes = data["groups"].get("shelf") or []
    if not coast_shapes or not shelf_shapes:
        sys.exit("dump 里缺少 coastline 或 shelf")

    coasts = rings_of(coast_shapes)
    shelves = rings_of(shelf_shapes)

    all_coast = np.concatenate(coasts, 0)
    # 范围必须**同时**覆盖海岸线与浅滩：浅滩在陆地之外，会超出海岸线的包围盒。
    # （第一版只用了海岸线，于是浅滩点落出栅格 → IndexError。）
    all_shelf = np.concatenate(shelves, 0)
    both = np.concatenate([all_coast, all_shelf], 0)
    lo = np.floor(both.min(axis=0) / args.cell) * args.cell - 4 * args.cell
    hi = np.ceil(both.max(axis=0) / args.cell) * args.cell + 4 * args.cell
    W = int((hi[0] - lo[0]) / args.cell) + 1
    H = int((hi[1] - lo[1]) / args.cell) + 1
    print(f"栅格 {W}x{H} @ {args.cell} 单位   海岸线 {len(coasts)} 圈 / {len(all_coast)} 点"
          f"   浅滩 {len(shelves)} 圈")

    # 陆地掩膜：把画出来的海岸线圈填成实心
    img = Image.new("1", (W, H), 0)
    d = ImageDraw.Draw(img)
    for r in coasts:
        pts = [((p[0] - lo[0]) / args.cell, (p[1] - lo[1]) / args.cell) for p in r]
        if len(pts) >= 3:
            d.polygon(pts, fill=1)
    # ⚠️ PIL 的 Image 是 (宽= x, 高= y)，而 np.asarray 出来的数组是 [行=y, 列=x]。
    # 第一版写成 `mask[xs, ys]`，形状与语义都错 → IndexError。索引必须是 [ys, xs]。
    mask = np.asarray(img, dtype=bool)          # 形状 (H, W)，即 mask[y, x]
    print(f"陆地占画布 {mask.mean() * 100:.1f}%  栅格数组形状 {mask.shape}（= H x W）")

    tree = cKDTree(all_coast)

    print(f"\n{'浅滩圈':<8}{'点数':>7}{'在陆内':>9}{'距离 p10':>10}{'p50':>8}{'p90':>8}{'p99':>8}")
    print('-' * 58)
    worst = 0.0
    for i, r in enumerate(shelves):
        xs = np.clip(((r[:, 0] - lo[0]) / args.cell).astype(int), 0, mask.shape[1] - 1)
        ys = np.clip(((r[:, 1] - lo[1]) / args.cell).astype(int), 0, mask.shape[0] - 1)
        inside = mask[ys, xs]
        dist, _ = tree.query(r)
        p = np.percentile(dist, [10, 50, 90, 99])
        frac = inside.mean() * 100
        worst = max(worst, frac)
        print(f"#{i:<7}{len(r):>7}{frac:>8.1f}%{p[0]:>10.1f}{p[1]:>8.1f}{p[2]:>8.1f}{p[3]:>8.1f}")

    print(f"\n最差的一圈有 {worst:.1f}% 的采样点落在**陆地里面**")
    if worst > 5:
        print("❌ 判定：浅滩带的起点锚在一个画面上不存在的形状上 ——")
        print("   它与画出来的海岸线**不是同一个源**。修法是让浅滩从**位移之后**的海岸线派生。")
    elif worst > 1:
        print("△ 判定：少量重叠，可能是栅格化误差量级；结合目视判断。")
    else:
        print("✅ 判定：浅滩带贴的是画出来的海岸线，两者同源。")

    print("\n读法：第 3 列是**逐点符号** —— 平均宽度统计看不出这个问题，符号能。")
    print("      '露底的缝' 表现为距离 p10 明显偏大而不是偏小；两者要一起看。")


if __name__ == "__main__":
    main()
