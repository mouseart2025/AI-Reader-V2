#!/usr/bin/env python3
"""probe_style_lock.py — 把「跨层关系」变成一条可执行的契约。

## 为什么要有它

这个仓里已经出过**三次**同一形态的缺陷，每一次都是"量的是量、缺的是关系"：

| # | 断掉的关系 | 症状 |
|---|---|---|
| 1 | `OCEAN_DENSITY` ÷ 格距 | `CELL_PX` 从 16 翻到 32，密度按旧格距标定 ⇒ 波浪被静默四分之一 |
| 2 | 墨水 ÷ 底色 | `COLORS_LIGHT.water` 被设成**复合海面自身的值** ⇒ 波浪看不见，任何单个常量都显示不出原因 |
| 3 | 记号尺寸 ÷ 间距 | 只放大尺寸不动间距 ⇒ 记号横跨两格、99% 重叠（印花） |
| 4 | 浅滩 ÷ 海岸线 | 浅滩从**未位移且未裁剪**的掩膜追出 ⇒ 两个"画面上不存在的形状"上的带子 |

四次里有三次是**事后重测**才发现的，而每一次的守卫都只覆盖了"量"、没覆盖"关系"。
这份脚本就是把这几条关系**固化成一张会自动判红绿的表**，让下一次断掉时立刻可见。

## 设计纪律（都是本仓踩出来的）

- **不重写判定**：每个检查都调用已有的量具函数（`probe_map_visual` / `probe_coastline_morphology`
  / `probe_shelf_anchor`），本文件只负责**喂输入、套阈值、汇总**。
- **阈值必须写出出处**：文献口径、图层自己的口径、还是我的判断 —— 三者在表里分开标。
- **报"边际"而不只是红绿**：距阈值 20% 以内的项标 ⚠️，否则"刚好过"会被当成"很安全"。

## 用法

  # 先采三张图（同一视口、同一缩放）
  node scripts/probe_map_dom.cjs                                  # -> /tmp/map_pw.png + 掩膜/符号/标记
  node scripts/probe_map_dom.cjs --hide ".loc-plate" --shot /tmp/p_off.png
  node scripts/probe_map_dom.cjs --hide "#terrain"    --shot /tmp/t_off.png
  node scripts/dump_coastlines.cjs <URL> --out /tmp/coast.json

  python3 scripts/probe_style_lock.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import probe_map_visual as V  # noqa: E402
from probe_coastline_morphology import boxcount, parse_path, sample_cubics  # noqa: E402
from probe_shelf_anchor import anchor_stats  # noqa: E402

# 海岸线分形维数的量程：**必须是尺子自证过的那一段**
# （probe_coastline_morphology.py --calibrate：Koch L4 在 [22,256] 上复现 1.2524 vs 真值 1.2619）
D_SIZES = [28.0, 41.0, 60.0, 88.0, 128.0, 186.0]

ROWS: list[dict] = []


def record(name: str, value, ok: bool | None, threshold: str, source: str, note: str = ""):
    ROWS.append({"name": name, "value": value, "ok": ok, "threshold": threshold,
                 "source": source, "note": note})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", default="/tmp/map_pw.png")
    ap.add_argument("--plates-off", default="/tmp/map_pw_off.png")
    ap.add_argument("--terrain-off", default="/tmp/map_terrain_off.png")
    ap.add_argument("--mask", default="/tmp/map_mask.json")
    ap.add_argument("--symbols", default="/tmp/map_symbols.json")
    ap.add_argument("--marks", default="/tmp/map_marks.json")
    ap.add_argument("--coast", default="/tmp/coastlines.json")
    args = ap.parse_args()

    need = lambda p: Path(p).exists()  # noqa: E731
    im = Image.open(args.shot).convert("RGB") if need(args.shot) else None
    mask = json.loads(Path(args.mask).read_text()) if need(args.mask) else None

    # ── 1. 陆海价值分离 ──────────────────────────────────────────
    if im and mask:
        ls = V.split_land_sea_by_mask(im, mask)
        d = abs(ls["land"]["lumY"] - ls["sea"]["lumY"])
        record("陆海价值分离 ΔL", f"{d:.1f} 级", d >= 20, "≥ 20", "本仓既有口径（<20 视为糊）")
    else:
        record("陆海价值分离 ΔL", "n/a", None, "≥ 20", "缺图或掩膜")

    # ── 2. 地面层不是摆设，且海上有纹理 ──────────────────────────
    #
    # ⚠️ 阈值只能取**实测锚点**，不能取我的直觉。我第一版写的是"海上高频贡献 ≥ 2%"，
    # 而当前**目视明明有波浪**的状态实测只有 0.16% —— 那条阈值会把一个健康状态判红。
    # 原因是这个量被整片海摊平了（`sea.px` 80 万，绝大多数像素没有记号），
    # 而且海面的高频主要来自别的层。两个实测锚点：
    #   - 缺陷状态（第 2 号）：mean_abs 0.058、hf_delta **−0.02%**
    #   - 当前状态（目视有纹理）：mean_abs 0.213、hf_delta **+0.16%**、share_gt2 2.74%
    # 所以判据是"**符号** + 有墨像素占比"，不是"幅度够大"。
    if im and need(args.terrain_off) and mask:
        off = Image.open(args.terrain_off).convert("RGB")
        c = V.layer_contribution(im, off, mask)
        sea, land = c["sea"], c["land"]
        record("海上纹理·高频符号", f"{sea['hf_delta_pct']:+.2f}%", sea["hf_delta_pct"] > 0,
               "> 0", "实测锚点：缺陷时 −0.02%（等于没有）",
               "幅度无意义（被整片海摊平），只看符号")
        record("海上纹理·有墨像素", f"{sea['share_gt2'] * 100:.2f}%", sea["share_gt2"] >= 0.01,
               "≥ 1%", "我的判断（缺陷时此值更低）",
               f"mean|ΔL| {sea['mean_abs']} 级")
        record("陆上地面层高频", f"{land['hf_delta_pct']:+.2f}%", land["hf_delta_pct"] > 0,
               "> 0", "实测锚点（层不应是摆设）")
    else:
        record("海上纹理·高频符号", "n/a", None, "> 0", "缺 --terrain-off 图")

    # ── 3. 间距 ÷ 尺寸（该层自己的验收口径）──────────────────────
    if mask and need(args.symbols):
        g = V.gap_ratio(mask, json.loads(Path(args.symbols).read_text()))
        if "error" in g:
            record("间距÷尺寸", g["error"], None, "≥1.5（该层口径）", "terrainHints.ts 注释")
        else:
            r, rp10 = g["ratio_median"], g["ratio_p10"]
            record("间距÷尺寸 中位", r, r >= 1.5, "≥ 1.5",
                   "**图层自己的口径**（terrainHints.ts，作者用它把 CELL_PX 16→32）")
            record("间距÷尺寸 p10", rp10, rp10 >= 1.0, "≥ 1.0（硬地板）",
                   "我的判断：<1.0 即实心重叠", f"{g['land_symbols']} 个陆地记号")
    else:
        record("间距÷尺寸", "n/a", None, "≥ 1.5", "缺掩膜或符号")

    # ── 4. 节点预算 ─────────────────────────────────────────────
    if need(args.symbols):
        sym = json.loads(Path(args.symbols).read_text())
        n = len(sym["items"])
        record("地面记号数", n, n <= 1400, "≤ 1400", "NODE_BUDGET（terrainHints.ts）")

    # ── 5. 标签光学可读性 ───────────────────────────────────────
    if im and need(args.plates_off) and need(args.marks):
        ink = V.label_ink_stats(im, Image.open(args.plates_off).convert("RGB"),
                                json.loads(Path(args.marks).read_text()))
        med, over25 = ink["loss_median"], ink["labels_losing_over_25pct"]
        record("标签对比度损失 中位", f"{med}%", med <= 2.0, "≤ 2%",
               "本仓既有基线 0.0%", f"p90 {ink['loss_p90']}%，测到 {ink['labels_measured']} 个标签")
        record("标签损失 >25% 的个数", over25, over25 == 0, "= 0",
               "我的判断（>25% 才算读不出）")
    else:
        record("标签对比度损失 中位", "n/a", None, "≤ 2%", "缺 --plates-off 图")

    # ── 6. 海岸线分形维数 ───────────────────────────────────────
    if need(args.coast):
        data = json.loads(Path(args.coast).read_text())
        sh = data["groups"].get("coastline") or []
        pts = [sample_cubics(parse_path(s["d"])[0], 8) for s in sh if parse_path(s["d"])[0]]
        if pts:
            r = boxcount(np.concatenate(pts, 0), D_SIZES)
            d = r["D"]
            marginal = 1.15 <= d <= 1.35 and (d < 1.19 or d > 1.31)
            record("海岸线分形维数 D", round(d, 4), 1.15 <= d <= 1.35, "∈ [1.15, 1.35]",
                   "**文献口径**：真实海岸线（Mandelbrot 量英国 ≈1.25）",
                   ("⚠️ 贴近边界" if marginal else "") + f" R²={r['r2']:.4f}")
    else:
        record("海岸线分形维数 D", "n/a", None, "∈ [1.15, 1.35]", "缺 --coast")

    # ── 7. 浅滩与海岸线同源 ─────────────────────────────────────
    if need(args.coast):
        st = anchor_stats(args.coast, cell=4.0)
        if "error" in st:
            record("浅滩在陆内的比例", st["error"], None, "≤ 2%", "同源判据")
        else:
            w = st["worst_inside_pct"]
            record("浅滩在陆内的比例", f"{w}%", w <= 2.0, "≤ 2%",
                   "我的判断（量化量级 ≈0.5%，缺陷时 6.5%）",
                   f"{st['shelf_rings']} 圈浅滩；栅格 4 单位")
    else:
        record("浅滩在陆内的比例", "n/a", None, "≤ 2%", "缺 --coast")

    # ── 输出 ────────────────────────────────────────────────────
    print("=" * 108)
    print("地图跨层关系契约（style lock）")
    print("=" * 108)
    print(f"{'检查项':<26}{'实测':>14}{'阈值':>16}{'判定':>6}   出处 / 备注")
    print("-" * 108)
    n_fail = n_pass = n_skip = 0
    for r in ROWS:
        mark = "  —  " if r["ok"] is None else ("✅ PASS" if r["ok"] else "❌ FAIL")
        if r["ok"] is None:
            n_skip += 1
        elif r["ok"]:
            n_pass += 1
        else:
            n_fail += 1
        val = r["value"] if isinstance(r["value"], str) else f"{r['value']}"
        print(f"{r['name']:<26}{val:>14}{r['threshold']:>16}{mark}   {r['source']}")
        if r["note"]:
            print(f"{'':<26}{'':>14}{'':>16}{'':>6}   └ {r['note']}")
    print("-" * 108)
    print(f"PASS {n_pass}   FAIL {n_fail}   SKIP {n_skip}")
    marginal = [r["name"] for r in ROWS if r["ok"] and "⚠️" in (r["note"] or "")]
    if marginal:
        print(f"⚠️ 边际（距阈值 20% 内，别当作很安全）：{', '.join(marginal)}")
    if n_fail:
        print("\n❌ 有关系已经离开带内 —— 这与本仓前四次缺陷同一形态：量没变，关系变了。")
        sys.exit(1)


if __name__ == "__main__":
    main()
