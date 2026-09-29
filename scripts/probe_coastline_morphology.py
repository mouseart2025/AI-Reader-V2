#!/usr/bin/env python3
"""probe_coastline_morphology.py — 海岸线「像不像海岸线」的定量判据。

## 判据：盒计数分形维数（box-counting fractal dimension）

真实海岸线的分形维数有**公认区间**：Mandelbrot 量英国海岸得 ≈1.25，
多数实测海岸落在 **1.15–1.35**。这不是"我觉得像不像"，是一个可以复算的数：

- **D ≈ 1.00** ⇒ 在量程内是直线/平滑曲线（**太光滑**，像用尺子拉的）
- **D ≈ 1.25** ⇒ 有自相似的海湾与岬角（**像海岸线**）
- **D ≳ 1.45** ⇒ 接近噪声/自回避随机游走（**太碎**，像静电）

做法：把polyline 栅格化到边长 ε 的方格，数被占的格数 N(ε)，
在 log N vs log(1/ε) 上取斜率即 D。**必须报量程** —— D 只在被量的尺度区间上有意义，
而"在哪个尺度上取斜率"正是最容易作弊的地方。

## 为什么必须带合成对照

D 对**量程**极其敏感，而且"太光滑"是一个否定结论。所以每次都在**同一量程**上跑：

| 对照 | 已知 D | 用途 |
|---|---|---|
| 正多边形 | ≈1.00 | 阴性：尺子必须给出 1.0 |
| Koch 雪花（4 级）| log4/log3 = **1.2619** | 阳性：**已知真值**，尺子必须复现它 |
| 抖动圆（幅度 2/5/10% 半径）| 1.0→1.3 单调 | 同构档：给"多少抖动算够"提供标尺 |

★ 阳性对照是**解析已知**的，所以这一步同时校准了实现与量程 —— 尺子先自证，再量被测物。

## 用法

  node scripts/dump_coastlines.cjs <URL> --out /tmp/coastlines.json
  python3 scripts/probe_coastline_morphology.py /tmp/coastlines.json --group coastline
  python3 scripts/probe_coastline_morphology.py --calibrate
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

# ── path d 解析 ────────────────────────────────────────────────
_TOKEN = re.compile(r"[MmLlCcSsQqTtHhVvZz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?")


def parse_path(d: str):
    """返回 (cubics, endpoints)。

    本仓的海岸线由 rough.js 生成：**每一段都是独立的 `M x y C ...`**，
    也就是输入折线的每个顶点对都被单独画成一个三次贝塞尔（各带自己的手绘抖动）。
    所以解析要按"逐段不连续"来对待，不能假设 Z/闭合。
    """
    toks = _TOKEN.findall(d or "")
    i = 0
    cx = cy = 0.0
    start = None
    cubics: list[tuple[float, float, float, float, float, float, float, float]] = []
    ends: list[tuple[float, float]] = []

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    while i < len(toks):
        t = toks[i]
        if t in "Mm":
            i += 1
            x, y = num(), num()
            if t == "m":
                x, y = cx + x, cy + y
            cx, cy = x, y
            start = (x, y)
            ends.append((x, y))
        elif t in "Cc":
            i += 1
            a, b, c, e, x, y = (num() for _ in range(6))
            if t == "c":
                a, b, c, e, x, y = (cx + a, cy + b, cx + c, cy + e, cx + x, cy + y)
            cubics.append((cx, cy, a, b, c, e, x, y))
            cx, cy = x, y
            ends.append((x, y))
        elif t in "Ll":
            i += 1
            x, y = num(), num()
            if t == "l":
                x, y = cx + x, cy + y
            # 直线段用退化的三次表示，保证下游只有一种情形
            cubics.append((cx, cy, cx, cy, x, y, x, y))
            cx, cy = x, y
            ends.append((x, y))
        elif t in "Zz":
            i += 1
            if start:
                cx, cy = start
        else:
            # 不认识的命令：跳过它的数字，别把整条 path 丢掉
            i += 1
    return cubics, ends


def sample_cubics(cubics, per_seg: int = 8) -> list[tuple[float, float]]:
    """把每段三次贝塞尔按 per_seg 个点采样成折线（含端点）。"""
    if not cubics:
        return []
    t = np.linspace(0.0, 1.0, per_seg + 1)
    mt = 1.0 - t
    b0, b1, b2, b3 = mt**3, 3 * mt**2 * t, 3 * mt * t**2, t**3
    pts = []
    for (x0, y0, x1, y1, x2, y2, x3, y3) in cubics:
        xs = b0 * x0 + b1 * x1 + b2 * x2 + b3 * x3
        ys = b0 * y0 + b1 * y1 + b2 * y2 + b3 * y3
        pts.append(np.stack([xs, ys], axis=1))
    return np.concatenate(pts, axis=0)


# ── 盒计数 ─────────────────────────────────────────────────────
def boxcount(pts: np.ndarray, sizes) -> dict:
    """对点序列按 sizes 逐档栅格化，返回 N(ε) 与拟合出的 D。"""
    if len(pts) < 2:
        return {"error": "点太少"}
    seg_a, seg_b = pts[:-1], pts[1:]
    seg_len = np.hypot(*(seg_b - seg_a).T)
    rows = []
    for s in sizes:
        # 每段按 s/2 采样，保证不会跨过整格
        occ = set()
        for k in range(len(seg_a)):
            n = max(2, int(math.ceil(seg_len[k] / (s / 2.0))) + 1)
            tt = np.linspace(0.0, 1.0, min(n, 4096))
            xs = seg_a[k, 0] + (seg_b[k, 0] - seg_a[k, 0]) * tt
            ys = seg_a[k, 1] + (seg_b[k, 1] - seg_a[k, 1]) * tt
            occ.update(zip((xs / s).astype(np.int64), (ys / s).astype(np.int64)))
        rows.append((s, len(occ)))
    s_arr = np.array([r[0] for r in rows], dtype=float)
    n_arr = np.array([r[1] for r in rows], dtype=float)
    good = n_arr > 0
    lx, ly = np.log(1.0 / s_arr[good]), np.log(n_arr[good])
    if len(lx) < 2:
        return {"error": "有效档太少"}
    slope, intercept = np.polyfit(lx, ly, 1)
    pred = slope * lx + intercept
    ss_res = float(((ly - pred) ** 2).sum())
    ss_tot = float(((ly - ly.mean()) ** 2).sum())
    return {
        "D": float(slope),
        "r2": 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else float("nan"),
        "range": (float(s_arr[good].min()), float(s_arr[good].max())),
        "table": rows,
    }


# ── 合成对照 ───────────────────────────────────────────────────
def ctrl_polygon(n=200, r=1000.0):
    t = np.linspace(0, 2 * np.pi, n + 1)
    return np.stack([r * np.cos(t), r * np.sin(t)], axis=1)


def ctrl_koch(level=4, side=1800.0):
    """Koch 雪花，D = log4/log3 = 1.2619（解析已知）。"""
    pts = [np.array([0.0, 0.0]), np.array([side, 0.0]), np.array([side / 2, side * math.sqrt(3) / 2])]
    for _ in range(level):
        out = []
        for k in range(len(pts)):
            a, b = pts[k], pts[(k + 1) % len(pts)]
            d = (b - a) / 3.0
            p1 = a + d
            p3 = a + 2 * d
            # 顶点朝外：按序旋转 -60°
            rot = np.array([[math.cos(-math.pi / 3), -math.sin(-math.pi / 3)],
                            [math.sin(-math.pi / 3), math.cos(-math.pi / 3)]])
            p2 = p1 + rot @ d
            out += [a, p1, p2, p3]
        pts = out
    pts.append(pts[0])
    return np.array(pts)


def ctrl_jittered(n=600, r=1000.0, amp=0.05, seed=3):
    """抖动圆：同构档，给出"多少抖动对应多少 D"的标尺。"""
    rng = np.random.default_rng(seed)
    t = np.linspace(0, 2 * np.pi, n + 1)
    rr = r * (1.0 + rng.normal(0, amp, n + 1))
    return np.stack([rr * np.cos(t), rr * np.sin(t)], axis=1)


def calibrate(sizes):
    """尺子先自证 —— 但**必须在将要使用的量程内**自证。

    第一版这张表里我写了几组"期望值"，结果全对不上，而错的是**期望**不是实现：
    - 正多边形给 1.07 而不是 1.00（采样步长在格角处多占格，尺度无关的高估）；
    - Koch **L2** 给 1.11，而解析值是 1.2619 —— 因为 L2 最细的特征尺度是 side/9≈200，
      量程 4–828 里有一大半在"比最细特征还细"的地方，那里曲线就是直线，斜率被拉向 1；
      L4 最细特征 ≈22，所以同量程下给 1.24，已经接近真值。
    - "抖动圆"给 1.26–1.56 而不是我写的 1.0–1.2：**逐点独立抖动半径**时，
      抖动幅度（±2% × r = 20）已经超过点间距（≈10），所以它不是"变粗糙的圆"，
      而是高频噪声。它是个**阶梯**，没有解析值。

    ⇒ 结论：**D 只在"落在被测对象特征尺度之间"的量程上有意义**，
    而"量程怎么选"正是最容易把结论做过的地方。所以这一步改成：
    **在将要用的那一段量程上，验证尺子能复现 Koch 的解析真值。**
    """
    koch = ctrl_koch(4)
    print("# 尺子自证：把 Koch L4（解析 D = log4/log3 = 1.2619）在**不同的子量程**上量")
    print("  （L4 的特征尺度：最细 ≈1800/81 ≈ 22，最粗 1800）\n")
    print(f"{'量程 ε':<26}{'D':>8}{'R²':>8}{'与真值差':>10}")
    print("-" * 54)
    best = None
    for lo, hi in [(4, 828), (8, 200), (12, 300), (16, 400), (22, 570), (22, 256), (30, 400)]:
        sub = [s for s in sizes if lo <= s <= hi]
        if len(sub) < 4:
            continue
        r = boxcount(koch, sub)
        diff = r["D"] - 1.2619
        print(f"[{lo:>4} – {hi:>4}]{'':<13}{r['D']:>8.4f}{r['r2']:>8.4f}{diff:>+10.4f}")
        if best is None or abs(diff) < abs(best[2]):
            best = ((lo, hi), r["D"], diff, sub)
    (lo, hi), D, diff, sub = best
    print(f"\n最接近真值的量程：[{lo} – {hi}]  量到 {D:.4f}（差 {diff:+.4f}）"
          f"  ⇒ **地图也必须用这一段量程**，否则读数与标尺不同源")
    print("  该量程上的盒边长：" + ",".join(f"{s:.0f}" for s in sub))

    print("\n# 同构阶梯（无解析值，只提供「多少抖动对应多少 D」的标尺）")
    print(f"{'对照':<26}{'D':>8}{'R²':>8}   读法")
    print("-" * 66)
    rows = [
        ("正多边形（阴性）", ctrl_polygon(), "D 应最低（1.0 附近 + 采样偏差）"),
        ("抖动圆 ±2%", ctrl_jittered(amp=0.02), "高频噪声：已高于多边形"),
        ("抖动圆 ±5%", ctrl_jittered(amp=0.05), "更碎"),
        ("抖动圆 ±10%", ctrl_jittered(amp=0.10), "更碎"),
    ]
    for lab, pts, note in rows:
        r = boxcount(pts, sub)
        print(f"{lab:<26}{r['D']:>8.4f}{r['r2']:>8.4f}   {note}")
    return sub


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", nargs="?", help="dump_coastlines.cjs 的产物")
    ap.add_argument("--group", default="coastline")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--sizes", default="4,6,9,13,19,28,41,60,88,128,186,270,392,570,828",
                    help="盒边长（canvas 单位）")
    ap.add_argument("--range", metavar="LO,HI",
                    help="限定量程（用 --calibrate 选出的那一段；不给就用全量程）")
    ap.add_argument("--per-seg", type=int, default=8, help="每段贝塞尔的采样点数")
    args = ap.parse_args()

    sizes = [float(v) for v in args.sizes.split(",")]

    if args.calibrate or not args.dump:
        sub = calibrate(sizes)
        if not args.dump:
            print("\n（下一步：用上面选出的量程对地图取数，--range LO,HI）")
            return
    else:
        sub = sizes

    if args.range:
        lo, hi = (float(v) for v in args.range.split(","))
        sub = [s for s in sizes if lo <= s <= hi]
        if len(sub) < 3:
            sys.exit(f"--range {args.range} 在这套 --sizes 里只剩 {len(sub)} 档，太少")

    data = json.loads(Path(args.dump).read_text())
    shapes = data["groups"].get(args.group)
    if not shapes:
        sys.exit(f"{args.dump} 里没有 group {args.group}")

    all_pts = []
    n_cubics = 0
    seglens = []
    for shp in shapes:
        cubics, _ends = parse_path(shp["d"])
        n_cubics += len(cubics)
        if not cubics:
            continue
        pts = sample_cubics(cubics, args.per_seg)
        all_pts.append(pts)
        # 每段的弦长（canvas 单位）—— 反映"输入折线有多密"
        for (x0, y0, _a, _b, _c, _e, x3, y3) in cubics:
            seglens.append(math.hypot(x3 - x0, y3 - y0))

    pts = np.concatenate(all_pts, axis=0) if all_pts else np.zeros((0, 2))
    seglens = np.array(seglens) if seglens else np.zeros(0)

    print(f"# {Path(args.dump).name}  group={args.group}")
    print(f"  path {len(shapes)} 条，三次段 {n_cubics} 个，采样点 {len(pts)} 个")
    if seglens.size:
        q = np.percentile(seglens, [10, 50, 90, 99])
        print(f"  段弦长（canvas 单位）：p10 {q[0]:.3f}  p50 {q[1]:.3f}  p90 {q[2]:.3f}  p99 {q[3]:.3f}")
        tot = float(seglens.sum())
        print(f"  总长（按弦长累加，非闭合环）{tot:.0f} canvas 单位")
    bounds = (pts.min(axis=0), pts.max(axis=0))
    print(f"  范围 x {bounds[0][0]:.0f}–{bounds[1][0]:.0f}  y {bounds[0][1]:.0f}–{bounds[1][1]:.0f}")

    r = boxcount(pts, sub)
    if "error" in r:
        sys.exit(r["error"])
    print(f"\n分形维数 D = {r['D']:.4f}   R² = {r['r2']:.4f}"
          f"   量程 ε {r['range'][0]:.0f}–{r['range'][1]:.0f} canvas 单位"
          f"（{len(sub)} 档）")
    band = (1.15, 1.35)
    print(f"真实海岸线公认区间 {band[0]}–{band[1]}")
    print("判定：" + ("✅ 落在区间内" if band[0] <= r["D"] <= band[1]
                    else "⚠️ 偏光滑（像尺子拉的）" if r["D"] < band[0]
                    else "⚠️ 偏碎（像静电）"))
    print("\nlog-log 表（斜率即 D；某段变平 = 该尺度以上没有结构）：")
    prev = None
    for s, n in r["table"]:
        d = "" if prev is None else f"  局部斜率 {-(math.log(n) - math.log(prev[1])) / (math.log(s) - math.log(prev[0])):.3f}"
        print(f"  ε={s:>6.0f}  N={n:>8d}{d}")
        prev = (s, n)
    print("\n★ 局部斜率随 ε 的系统性变化比总 D 更有信息量："
          "\n  粗尺度斜率低而细尺度斜率高 ⇒ 『远看平滑、近看有碎屑』，"
          "通常是'低多边形折线 + 逐段噪声'的签名。")


if __name__ == "__main__":
    main()
