#!/usr/bin/env python3
"""proto_coastline_rmd.py — 离线原型：给海岸线加上**真正的**多尺度粗糙度。

## 为什么不是"多加几个倍频的平滑位移"

现役 `_distort_coastline` 沿法向叠加两个频率的 OpenSimplex 位移。实测结果是
**局部斜率在 ε=3…400（两个半数量级）上恒为 1.02–1.08** —— 曲线在**每一个**可测尺度上都光滑。

机理：沿法向的**平滑位移场**是一个微分同胚。微分同胚把光滑曲线映成光滑曲线，
所以**无论叠加多少个倍频，D 都恒等于 1**。倍频只改变形状，不产生可分形性。

要 D>1，粗糙度必须长在**折线自身**里：每一级细分都要**引入新顶点与不可忽略的转角**。
这就是中点位移（random midpoint displacement, RMD）：第 k 级插入中点，法向位移的
标准差按 σ_k = σ0 · 2^(−kH) 衰减。

对 fBm 型曲线有 **D = 2 − H**。要 D ≈ 1.25（真实海岸线）⇒ **H = 0.75**。

## 这个脚本干什么

离线把已导出的海岸线重采样到"基础步长"，再跑 RMD，**用已经校准过的分形维数尺子
（量程 28–186，Koch L4 在该量程上复现 1.2619）**量出结果，扫 (H, σ0, 层数)，
找出落在 1.15–1.35 的参数。**先在这里调好再动生产代码** —— 省一次后端重启与重烘焙。

## 用法

  python3 scripts/proto_coastline_rmd.py /tmp/coastlines.json --sweep
  python3 scripts/proto_coastline_rmd.py /tmp/coastlines.json --h 0.75 --sigma 0.35 --levels 6
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from probe_coastline_morphology import boxcount, parse_path, sample_cubics  # noqa: E402

# 尺子自证过的量程（见 probe_coastline_morphology.calibrate）：
# 在这段上 Koch L4 量到 1.2524（真值 1.2619），正多边形 1.0358。
CAL_SIZES = [28.0, 41.0, 60.0, 88.0, 128.0, 186.0]
BAND = (1.15, 1.35)


def resample_ring(pts: np.ndarray, step: float) -> np.ndarray:
    """按弧长把闭合环重采样成近似等距点。

    现役环是 2.2 canvas 单位一段（远超需要），而且结构尺度在别处 —— 所以**必须先粗化**，
    否则 RMD 的每一级都在跟一个已经过采样的折线较劲，顶点数会炸而形貌不变。
    """
    d = np.hypot(*np.diff(np.vstack([pts, pts[:1]]), axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(d)])
    total = s[-1]
    if total <= 0:
        return pts
    n = max(8, int(round(total / step)))
    target = np.linspace(0.0, total, n, endpoint=False)
    x = np.interp(target, s, np.concatenate([pts[:, 0], pts[:1, 0]]))
    y = np.interp(target, s, np.concatenate([pts[:, 1], pts[:1, 1]]))
    return np.stack([x, y], axis=1)


def rmd_ring(pts: np.ndarray, levels: int, amp0: float, h: float, rng) -> np.ndarray:
    """闭合环上的中点位移：σ_k = amp0 · 2^(−kH)。

    ⚠️ 第一版把位移写成「当前边长的比例」，等价于 σ_k ∝ 2^(−k(H+1)) ——
    **衰减指数被偷偷加了一**，于是曲线比预期更光滑、D 反而被压到 1 以下。
    标准形是**绝对幅度**按 2^(−kH) 衰减（幅度按环大小给出，见 build）。
    """
    cur = pts
    for k in range(levels):
        sigma = amp0 * (0.5 ** (k * h))
        a = cur
        b = np.roll(cur, -1, axis=0)
        mid = (a + b) * 0.5
        t = b - a
        L = np.hypot(t[:, 0], t[:, 1])
        L[L < 1e-9] = 1e-9
        nx, ny = -t[:, 1] / L, t[:, 0] / L
        d = rng.normal(0.0, sigma, len(cur))
        nxt = np.empty((len(cur) * 2, 2))
        nxt[0::2] = a
        nxt[1::2] = mid + np.stack([nx * d, ny * d], axis=1)
        cur = nxt
    return cur


def ring_amp0(pts: np.ndarray, base_step: float, sigma_frac: float) -> float:
    """幅度按环大小给：小岛用更小的 base，否则自己的 RMD 会把它撕碎。"""
    perim = float(np.hypot(*np.diff(np.vstack([pts, pts[:1]]), axis=0).T).sum())
    return sigma_frac * min(base_step, max(perim / 8.0, base_step / 8.0))


def build(shapes, base_step, levels, sigma_frac, h, seed=11) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = []
    for shp in shapes:
        cubics, _ = parse_path(shp["d"])
        if not cubics:
            continue
        ring = sample_cubics(cubics, 4)
        coarse = resample_ring(ring, base_step)
        amp0 = ring_amp0(coarse, base_step, sigma_frac)
        out.append(rmd_ring(coarse, levels, amp0, h, rng))
    return np.concatenate(out, axis=0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--group", default="coastline")
    ap.add_argument("--base-step", type=float, default=48.0)
    ap.add_argument("--h", type=float, default=0.75)
    ap.add_argument("--sigma", type=float, default=0.35, help="σ0 作为相邻点距的比例")
    ap.add_argument("--levels", type=int, default=6)
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()

    data = json.loads(Path(args.dump).read_text())
    shapes = data["groups"][args.group]

    # 基线：现状
    base = np.concatenate([sample_cubics(parse_path(s["d"])[0], 8) for s in shapes], axis=0)
    r = boxcount(base, CAL_SIZES)
    print(f"现状：D = {r['D']:.4f} (R² {r['r2']:.4f})  顶点 {len(base)}"
          f"   目标带 {BAND[0]}–{BAND[1]}")

    if args.sweep:
        print(f"\n扫参（base_step {args.base_step}, levels {args.levels}）")
        print(f"{'H':>6}{'σ0':>7}{'D':>9}{'R²':>8}{'顶点数':>10}   判定")
        print("-" * 56)
        best = []
        for h in (0.5, 0.6, 0.7, 0.75, 0.8, 0.85):
            for sig in (0.2, 0.3, 0.4, 0.5):
                pts = build(shapes, args.base_step, args.levels, sig, h)
                rr = boxcount(pts, CAL_SIZES)
                ok = BAND[0] <= rr["D"] <= BAND[1]
                print(f"{h:>6.2f}{sig:>7.2f}{rr['D']:>9.4f}{rr['r2']:>8.4f}{len(pts):>10d}"
                      f"   {'✅ 在带内' if ok else ''}")
                if ok:
                    best.append((abs(rr["D"] - 1.25), h, sig, rr["D"], len(pts)))
        if best:
            best.sort()
            _, h, sig, D, n = best[0]
            print(f"\n最接近 D=1.25：H={h} σ0={sig} ⇒ D={D:.4f}，顶点 {n}")
        else:
            print("\n⚠️ 没有参数落进带内 —— 调整 base_step 或 levels 再扫")
        return

    pts = build(shapes, args.base_step, args.levels, args.sigma, args.h)
    rr = boxcount(pts, CAL_SIZES)
    print(f"\nRMD H={args.h} σ0={args.sigma} levels={args.levels} base_step={args.base_step}")
    print(f"  D = {rr['D']:.4f} (R² {rr['r2']:.4f})  顶点 {len(pts)}"
          f"  [{BAND[0]}, {BAND[1]}] ⇒ "
          + ("✅" if BAND[0] <= rr["D"] <= BAND[1] else "❌"))
    print("\n  局部斜率：")
    for s, n in boxcount(base, [28, 41, 60, 88, 128, 186])["table"]:
        print(f"    ε={s:>5.0f}  N={n:>7d}")


if __name__ == "__main__":
    main()
