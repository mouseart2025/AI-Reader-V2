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
4. **在同一个裁剪尺寸上算合成对照阶梯**（每次自动跑，不是可选项），报「峰 ÷ 随机斑地板」。

## 两个已踩过的坑（都写进了实现）

★ **原始峰的基线随裁剪尺寸变**：420x260 上「随机斑」地板 ≈0.099，260x130 上明显更高。
  ⇒ **跨尺寸比原始峰无效**，必须比 ÷地板。我曾拿 260x130 的原始峰（0.089–0.205）去比
  420x260 上标定的 0.099，差点把纯粹的**尺寸效应**报成"出货档在多数区域都是格子"。

★ **页面 chrome 会伪装成纹理**：深缩放帧上 y=85 那一排（顶部 chips 条，白字＋深色圆角底）
  给出 HF std **30.4**、峰 **0.94**，而真正的海面是 HF 1.5–7.5、峰 0.05–0.2。
  ⇒ 判据必须自己认出来（峰 >0.5 或高频离群即标 ⚠️ 并排除出"最差区域"结论），
  否则它会**默默回答一个它无法知道是畸形的问题**。同一件事本仓探针早有先例：
  `probe_map_visual.py` 在既无 `--mask` 又无 `--roi` 时会警告"统计会被页面 chrome 污染"。

## 用法

  python3 scripts/probe_texture_lattice.py A.png B.png \
      --rect 300,260,420,260 --labels legacy,LOD [--controls 24]

`--controls` 只用来指定对照的记号间距（默认 24px）；对照阶梯本身每次都会跑。
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

    # ── 边缘污染：一条 chrome 横带只需要 2% 的面积就能把 HF 从 1.8 抬到 9.4 ──
    #
    # 实测：深缩放帧的地图视口最上面 ~12px 是顶部 chips 条（浅底深字、硬边）。
    # 710x520 的裁区里它只占 2%，于是全局中位护栏（中位×4）**拦不住**，
    # 峰的数值也没异常 —— 但 HF std 从纯海面的 1.8 变成 9.4，所有"填充档之间"的
    # 比较都建立在它上面。**横幅污染是低幅度、大面积外的一类，必须专门量。**
    # 判据：上/下 5% 行的 HF 若超过中段 3 倍，就是边框污染。
    # ⚠️ 只在**行方向**判：chrome 条是水平的。竖条（右栏）要靠横向裁区避开或换 x。
    rows_hf = hp.std(axis=1)  # 每行的高频强度
    band = max(1, rows_hf.size // 20)
    edge_hf = float(max(rows_hf[:band].mean(), rows_hf[-band:].mean()))
    mid_hf = float(rows_hf[band:-band].mean()) if rows_hf.size > 2 * band else float("nan")
    edge_ratio = edge_hf / mid_hf if mid_hf and mid_hf > 1e-9 else float("nan")

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
        "edge_ratio": edge_ratio,
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

    # ── 合成对照：**每次都在同一条 rect 上算，不再是可选项** ──
    #
    # 理由不是好看，是"峰的基线随裁剪尺寸变"：420x260 上「随机斑」地板 ≈0.099，
    # 260x130 上明显更高。所以**跨裁剪尺寸比原始峰是无效的** —— 我用 260x130 的
    # 原始峰（0.089–0.205）去比 420x260 上标定的 0.099 地板，差点把纯粹的
    # **尺寸效应**报成"出货档在多数区域都是格子"。
    # ⇒ 原始峰只在**同尺寸**地板之上才有意义，所以地板必须和被测同一把尺子、同时算出来。
    _, _, cw, ch = rect
    pitch = args.controls if args.controls else 24.0
    ctrl_rows = [
        (lab, _analyse_array(arr, args.hp_sigma)) for lab, arr in synthetic_controls(cw, ch, pitch)
    ]
    floor_row = next((r for lab, r in ctrl_rows if "随机斑" in lab), None)
    floor = floor_row["peak"] if floor_row else float("nan")
    floor_r = floor_row["peak_radius_px"] if floor_row else float("nan")
    noise_row = next((r for lab, r in ctrl_rows if "白噪声" in lab), None)

    # ── 污染护栏 ──────────────────────────────────────────────
    #
    # 页面 chrome 会给出**不可能属于纹理**的统计量。实测深缩放帧上 y=85 那一排
    # （顶部 chips 条：白字 + 深色圆角底）给出 HF std 30.4、离原点峰 **0.94**，
    # 而真正的海面是 HF 1.5–7.5、峰 0.05–0.2。判据必须自己认出这种输入，
    # 否则它会**默默回答一个它无法知道是畸形的问题** —— 它刚才就是这么干的，
    # 而我差一点把答案写进结论。
    hf_vals = sorted(r["hf_std"] for _, r in rows)
    med_hf = hf_vals[len(hf_vals) // 2] if hf_vals else 0.0
    hf_bar = max(4.0 * med_hf, med_hf + 8.0)
    # ⚠️ 这条护栏是**相对**的，所以当**多数**取样区都是 chrome 时它会被自己毒死
    # （实测：三条全是污染时中位 30.4 ⇒ 门槛 121，一条都没拦住）。
    # 真正救场的是下面那条**绝对**判据 `peak > 0.5` —— 纹理不可能有这种规律性。
    # 两条都留：绝对判据管"整批都脏"，相对判据管"只有一块脏"。

    def verdict(r: dict) -> str:
        er = r.get("edge_ratio")
        if er is not None and er == er and er > 3.0:
            return f"⚠️污染(边缘带高频×{er:.1f})"
        if r["peak"] > 0.5:
            return "⚠️污染(峰>0.5，非纹理)"
        if r["hf_std"] > hf_bar:
            return "⚠️污染(高频离群)"
        ratio = r["peak"] / floor if floor > 1e-9 else float("inf")
        if ratio <= 1.0:
            return "✅ 不高于随机放置"
        if ratio <= 1.6:
            return "△ 略高于随机放置"
        return "❌ 有周期性"

    print(f"# 裁 {args.rect}  高通 σ={args.hp_sigma}px  图 {len(rows)} 张")
    print(f"# 同尺寸合成地板（随机斑，间距 {pitch}px）：峰 {floor:.4f} @ {floor_r:.0f}px"
          f"   白噪声峰 {noise_row['peak']:.4f}" if noise_row else "")
    print(f"# 高频离群护栏：中位 {med_hf:.3f} × 4 = {hf_bar:.3f}\n")
    print(f"{'样本':<14}{'高频std':>9}{'边缘比':>8}{'离原点峰':>10}{'峰÷地板':>9}"
          f"{'峰半径px':>10}{'各向异性':>10}  评语")
    print("-" * 104)
    for lab, r in rows:
        ratio = r["peak"] / floor if floor > 1e-9 else float("inf")
        er = r.get("edge_ratio")
        print(f"{lab:<14}{r['hf_std']:>9.3f}{(f'{er:.2f}' if er == er else 'n/a'):>8}"
              f"{r['peak']:>10.4f}{ratio:>9.2f}"
              f"{r['peak_radius_px']:>10.1f}{r['aniso']:>10.2f}  {verdict(r)}")

    print("\n[合成对照阶梯 · 同一把尺子]")
    for lab, r in ctrl_rows:
        print(f"{lab:<16}{r['hf_std']:>9.3f}{r['peak']:>10.4f}"
              f"{'—':>9}{r['peak_radius_px']:>10.1f}{r['aniso']:>10.2f}")

    flagged = [lab for lab, r in rows if verdict(r).startswith("⚠️")]
    if flagged:
        print(f"\n⚠️ 以下裁剪区被判定为受页面 chrome 污染，**不得**参与任何「最差区域」结论："
              f"\n   {', '.join(flagged)}")

    print("\n自相关径向剖面（环上中位，离原点越远越小 = 质地；某半径起不来 = 格子）：")
    for lab, r in rows:
        prof = "  ".join(f"{rr}:{v:+.3f}" for rr, v in r["profile"])
        print(f"  {lab:<14}{prof}")

    print("\n读法：")
    print("  · **只看「峰÷地板」**：>1.6 才叫有周期性；≤1.0 表示不比随机放置更有规律。")
    print("  · 原始峰的基线随裁剪尺寸变 ⇒ 跨尺寸比原始峰无效，必须比 ÷地板。")
    print("  · 各向异性 >≈ 1.6 ⇒ 环上有孤立峰 ⇒ 只在一个方向重复（行对齐）。")
    print("  · ⚠️ 标记的行是页面 chrome，不是地图内容 —— 换裁剪位置，别解释它。")
    print("  · 「边缘比」= 上/下 5% 行的 HF ÷ 中段：>3 说明裁区压到了横条 chrome，"
          "即使全局统计看不出来。")


if __name__ == "__main__":
    main()
