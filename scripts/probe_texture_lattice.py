#!/usr/bin/env python3
"""probe_texture_lattice.py — 一块地面纹理是「格子印花」还是「质地」。

## 为什么需要它

今天反复卡在同一个判断上：`k≈9.75` 的深海，旧版是一排排巨大的浪弧（看起来像编织
席子），新版是细密的小浪（看起来像织物）。**"像席子"和"像织物"都是我肉眼说的，
而肉眼看缩略图会被显示缩放骗**（1.1 px 描边在 17 px 记号上存活率远低于在 65 px
记号上）。所以需要一个不靠眼睛的判据。

## 判据：原点以外的自相关峰

- **格点/印花**（stamped lattice）：同样的记号按固定间距重复 ⇒ 自相关在
  **间距的整数倍处出现明显峰**（二维上是十字/环状的峰）。这是规律性的定义。
- **质地**（texture）：各向同性、无特征尺度的噪声 ⇒ 自相关从原点**单调衰减**，
  离原点越远越小，没有孤立的峰。

所以判据就是：**去掉原点邻域后，自相关的最大值**（越大越像印花），以及
**出现这个峰的距离**（应当≈记号间距，可以对上 `probe_ground_lod.cjs` 量到的中心距）。

## 怎么做

1. 裁矩形 → 灰阶；
2. **高通**：减去高斯模糊（去掉大尺度的明暗梯度 —— 否则自相关会被那片渐变主导，
   得到"整块都在同一个方向"的假结论）；
3. 去均值、归一化，FFT 算自相关；
4. 报：高频能量、离原点最近第一个峰的半径与强度、以及"峰 ÷ 同半径环上的中位"，
   最后一项才是**各向异性**的度量（环状峰 = 有格子；十字峰 = 只有行/列对齐）。

## 用法

  python3 scripts/probe_texture_lattice.py A.png B.png \
      --rect 300,260,420,260 --labels legacy,LOD
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage


def analyse(path: str, rect: tuple[int, int, int, int], hp_sigma: float = 6.0) -> dict:
    x, y, w, h = rect
    im = Image.open(path).convert("L")
    if x + w > im.width or y + h > im.height:
        sys.exit(f"{Path(path).name} 只有 {im.size}，装不下 rect {rect}")
    a = np.asarray(im.crop((x, y, x + w, y + h)), dtype=np.float64)
    return _analyse_array(a, hp_sigma)


def _blob(a: np.ndarray, cy: int, cx: int, s: float, amp: float = 1.0) -> None:
    h, w = a.shape
    r = int(3 * s) + 1
    y0, y1 = max(0, cy - r), min(h, cy + r + 1)
    x0, x1 = max(0, cx - r), min(w, cx + r + 1)
    if y0 >= y1 or x0 >= x1:
        return
    yy = np.arange(y0, y1)[:, None] - cy
    xx = np.arange(x0, x1)[None, :] - cx
    a[y0:y1, x0:x1] += amp * np.exp(-(yy**2 + xx**2) / (2.0 * s * s))


def synthetic_controls(w: int, h: int, pitch: float, seed: int = 7) -> list[tuple[str, np.ndarray]]:
    """合成对照阶梯。

    **被测对象本身就是"带抖动 + 抽稀的格点"**，所以对照必须覆盖从"完美格点"到
    "纯噪声"的整条谱。否则"峰低"只说明这把尺子量不出被测对象，不说明被测对象没有
    周期性 —— 这正是 2026-09-29 差一点犯的错（下"没有格子"的结论时只有阴性对照）。

    阳性（完美格点）必须起峰；阴性（白噪声、随机斑）必须不起峰；中间两档
    （抖动格点、抖动+抽稀 28%）才是与被测对象同构的那一档，判据要拿它标定。
    峰的强度与振幅无关（归一化自相关），所以对照不需要匹配亮度。
    """
    rng = np.random.default_rng(seed)

    def grid_positions(jitter: float) -> list[tuple[int, int]]:
        pos = []
        for yy in np.arange(pitch / 2, h, pitch):
            for xx in np.arange(pitch / 2, w, pitch):
                pos.append(
                    (
                        int(yy + rng.normal(0, jitter * pitch)),
                        int(xx + rng.normal(0, jitter * pitch)),
                    )
                )
        return pos

    out: list[tuple[str, np.ndarray]] = []

    a = np.zeros((h, w))
    for cy, cx in grid_positions(0.0):
        _blob(a, cy, cx, 2.2)
    out.append(("对照·完美格点", a))

    a = np.zeros((h, w))
    for cy, cx in grid_positions(0.30):
        _blob(a, cy, cx, 2.2)
    out.append(("对照·抖动格点", a))

    a = np.zeros((h, w))
    for cy, cx in grid_positions(0.30):
        if rng.random() < 0.28:
            _blob(a, cy, cx, 2.2)
    out.append(("对照·抖动+抽稀28%", a))

    n = max(1, int((w / pitch) * (h / pitch)))
    a = np.zeros((h, w))
    for _ in range(n):
        _blob(a, int(rng.integers(0, h)), int(rng.integers(0, w)), 2.2)
    out.append(("对照·随机斑", a))

    out.append(("对照·白噪声", rng.normal(0.0, 1.0, (h, w))))
    return out


def _analyse_array(a: np.ndarray, hp_sigma: float, controls: bool = False) -> dict:
    """自相关判据本体。**合成对照也走这一条**，否则对照与被测就不是同一把尺子。"""

    # 高通：去掉大尺度明暗梯度
    hp = a - ndimage.gaussian_filter(a, hp_sigma)
    hf_std = float(hp.std())

    # 加窗（避免边界环绕把周期假象做出来），
    wy = np.hanning(hp.shape[0])[:, None]
    wx = np.hanning(hp.shape[1])[None, :]
    z = hp * wy * wx
    z = z - z.mean()
    n = z.size
    F = np.fft.fft2(z)
    ac = np.real(np.fft.ifft2(F * np.conj(F)))
    ac = np.fft.fftshift(ac) / ac[0, 0]  # 原点归一化到 1

    cy, cx = np.array(ac.shape) // 2
    yy, xx = np.mgrid[0 : ac.shape[0], 0 : ac.shape[1]]
    r = np.hypot(yy - cy, xx - cx)

    # 排除原点邻域：半径 < rmin 的都在"记号自身"的尺度内，不是重复性
    rmin, rmax = 8.0, 90.0
    band = (r >= rmin) & (r <= rmax)
    if not band.any():
        return {"error": "裁剪太小"}
    peak_val = float(ac[band].max())
    pi = np.argmax(np.where(band, ac, -np.inf))
    py, px = np.unravel_index(pi, ac.shape)
    peak_r = float(np.hypot(py - cy, px - cx))
    # 各向异性：同一个半径环上，峰比环上的中位高多少
    ring = (r >= peak_r - 2) & (r <= peak_r + 2)
    ring_med = float(np.median(ac[ring]))
    aniso = float(peak_val / ring_med) if ring_med > 1e-9 else float("inf")

    # 整块的自相关半衰半径：质地应当是"离原点远了就归零"
    prof = []
    for rr in range(6, 100, 6):
        sel = (r >= rr - 3) & (r <= rr + 3)
        prof.append((rr, float(np.median(ac[sel]))))

    return {
        "hf_std": hf_std,
        "peak": peak_val,
        "peak_radius_px": peak_r,
        "ring_median": ring_med,
        "aniso": aniso,
        "profile": prof,
        "land_grid": None,
        "n": n,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("shots", nargs="+")
    ap.add_argument("--rect", required=True, metavar="X,Y,W,H")
    ap.add_argument("--labels", default=None, metavar="A,B")
    ap.add_argument("--hp-sigma", type=float, default=6.0,
                    help="高通尺度（px）：必须显著小于记号间距，否则会把记号本身也滤掉")
    ap.add_argument("--controls", type=float, default=None, metavar="PITCH",
                    help="额外跑合成对照阶梯（值 = 记号间距 px，应与被测的实测间距同量级）。"
                         "阳性对照起峰、阴性对照不起峰，判据才成立")
    args = ap.parse_args()

    rect = tuple(int(v) for v in args.rect.split(","))
    labels = args.labels.split(",") if args.labels else [Path(p).stem for p in args.shots]
    if len(labels) != len(args.shots):
        sys.exit("--labels 个数要和图数一致")

    rows = []
    for p, lab in zip(args.shots, labels):
        r = analyse(p, rect, args.hp_sigma)
        if "error" in r:
            sys.exit(r["error"])
        rows.append((lab, r))

    if args.controls:
        _, _, cw, ch = rect
        print(f"# 合成对照阶梯（间距 {args.controls}px，与图同尺寸 {cw}x{ch}，走同一条判据）")
        for lab, arr in synthetic_controls(cw, ch, args.controls):
            rows.append((lab, _analyse_array(arr, args.hp_sigma)))

    print(f"# 裁 {args.rect}  高通 σ={args.hp_sigma}px  {len(rows)} 张\n")
    print(f"{'样本':<14}{'高频std':>9}{'离原点峰':>10}{'峰半径px':>10}{'环中位':>9}{'各向异性':>10}")
    print("-" * 64)
    for lab, r in rows:
        print(f"{lab:<14}{r['hf_std']:>9.3f}{r['peak']:>10.4f}"
              f"{r['peak_radius_px']:>10.1f}{r['ring_median']:>9.4f}{r['aniso']:>10.2f}")

    print("\n自相关径向剖面（环上中位，离原点越远越小 = 质地；某半径起不来 = 格子）：")
    for lab, r in rows:
        prof = "  ".join(f"{rr}:{v:+.3f}" for rr, v in r["profile"])
        print(f"  {lab:<14}{prof}")

    print("\n读法：")
    print("  · 离原点峰 >≈ 0.10 且峰半径与记号间距吻合 ⇒ **格子/印花**（视场读作重复图案）")
    print("  · 各向异性 >≈ 1.6 ⇒ 环上有孤立峰 ⇒ 只在一个方向重复（行对齐）")
    print("  · 峰低（<0.05）而高频 std 不低 ⇒ **质地**（无特征尺度，是想要的）")


if __name__ == "__main__":
    main()
