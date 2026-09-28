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
import json
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


def split_land_sea_by_mask(im: Image.Image, mask: dict):
    """用 DOM 给出的真实陆地掩膜统计陆海 —— **这是唯一可信的分割方式**。

    按颜色冷暖分（R-B 符号）在这张图上不成立：陆地自己的羊皮纸污渍带冷色斑块，
    会被算成海。实测那样得到的 ΔL=17.3「陆海偏糊」，而掩膜一算发现是
    **量具把陆地的冷色块算进了海**。掩膜来自 `#coastline-ocean` 的
    `isPointInFill`（见 `probe_map_dom.cjs`），是渲染器自己的答案。
    """
    px = im.load()
    step = mask["step"]
    land = [0, 0, 0, 0]
    sea = [0, 0, 0, 0]
    for j, row in enumerate(mask["sea"]):
        for i, ch in enumerate(row):
            x = int(mask["x"] + i * step + step / 2)
            y = int(mask["y"] + j * step + step / 2)
            if x >= im.size[0] or y >= im.size[1]:
                continue
            r, g, b = px[x, y][:3]
            bucket = sea if ch == "1" else land
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


def structure_stats(im: Image.Image, mask: dict) -> dict:
    """陆地是否有**地域结构**，而不是均质噪声。

    为什么要在**掩膜网格上**算而不是逐像素：单像素梯度在这张图上几乎全是
    纸纹与符号噪声；读者的"这块地是山、那块是林"发生在几十像素的尺度上。
    网格间距本来就是 8px，正好把细噪声滤掉、留下大形。

    两个判据 + 两个合成对照（自校准，避免拿没有基准的数下结论）：

    - **coarse/fine**：粗尺度能量 ÷ 细尺度能量。有大地形时两者同阶；糊成一片的
      噪声里细尺度压倒一切，比值趋近 0。
    - **方向一致性 coherence**：(λ1-λ2)/(λ1+λ2) 的陆地均值。成列的山脉有主方向，
      随机噪声的各向异性为零。
    - 对照：白噪声（下界）与一维脊线场（上界）。
    """
    import numpy as np
    from scipy.ndimage import uniform_filter

    grid = np.asarray(im.convert("L"), dtype=np.float32)
    step = mask["step"]
    x0, y0 = int(mask["x"]), int(mask["y"])
    rows, cols = len(mask["sea"]), len(mask["sea"][0])
    G = np.zeros((rows, cols), dtype=np.float32)
    land = np.zeros((rows, cols), dtype=bool)
    for j in range(rows):
        for i in range(cols):
            x = min(im.size[0] - 1, x0 + int(i * step + step / 2))
            y = min(im.size[1] - 1, y0 + int(j * step + step / 2))
            G[j, i] = grid[y, x]
            land[j, i] = mask["sea"][j][i] != "1"

    def metrics(arr, region):
        coarse = uniform_filter(arr, 9)
        fine = arr - coarse
        c = float(coarse[region].std())
        f = float(fine[region].std())
        gy, gx = np.gradient(arr)
        Jxx = uniform_filter(gx * gx, 5)[region]
        Jyy = uniform_filter(gy * gy, 5)[region]
        Jxy = uniform_filter(gx * gy, 5)[region]
        tr = Jxx + Jyy
        ok = tr > 1e-6
        coh = np.sqrt((Jxx[ok] - Jyy[ok]) ** 2 + 4 * Jxy[ok] ** 2) / tr[ok]
        return {"coarse_fine": round(c / max(f, 1e-6), 3),
                "coherence": round(float(coh.mean()) if coh.size else 0.0, 3)}

    n = land.sum()
    if n < 50:
        return {"error": "陆地格点太少，掩膜可能不对"}
    out = {"land_cells": int(n), "land": metrics(G, land)}

    # ── 合成对照（同一套代码跑，量具自校准）──
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 30, G.shape).astype(np.float32)
    yy, xx = np.mgrid[0 : G.shape[0], 0 : G.shape[1]]
    ridges = (np.sin(xx / 4.0) * 40 + rng.normal(0, 4, G.shape)).astype(np.float32)
    out["control_white_noise"] = metrics(noise, land)
    out["control_ridge_field"] = metrics(ridges, land)
    return out


def symbol_stats(mask: dict, symbols: dict, block: int = 128) -> dict:
    """地面符号的**密度 / 地域分化 / 连片度** —— "读得出地貌"的可算判据。

    为什么不是看亮度：读者判断"这里是山、那边是林"，靠的是**符号的分布**，
    不是底下的洗层。所以这些量必须在符号层面算。

    - **密度**：陆地内符号数 ÷ 陆地面积 ×1000。太稀 ⇒ 陆地只能读成一张洗过的纸。
    - **地域纯度**：把陆地切成 block×block 的块，每块取占优类别；纯度高 ⇒ 块与块
      明显不同（"这片是山地、那片是平原"）；接近 1/类别数 ⇒ 到处都一样，是撒盐。
    - **连片度**：相邻块占优类别相同的比例。高 ⇒ 成片（山成脉、林成片）。
    """
    step = mask["step"]
    x0, y0 = int(mask["x"]), int(mask["y"])
    rows, cols = len(mask["sea"]), len(mask["sea"][0])

    def is_land(i: int, j: int) -> bool:
        return 0 <= j < rows and 0 <= i < cols and mask["sea"][j][i] != "1"

    # 每块一个 Counter
    blocks: dict[tuple[int, int], dict[str, int]] = {}
    land_cells = 0
    for j in range(rows):
        for i in range(cols):
            if is_land(i, j):
                land_cells += 1
    for item in symbols["items"]:
        cat, sx, sy = item[0], item[1], item[2]
        i = (sx - x0) // step
        j = (sy - y0) // step
        if not is_land(i, j):
            continue  # 海上的浪不属于陆地地貌
        key = (int(i * step) // block, int(j * step) // block)
        blocks.setdefault(key, {})
        blocks[key][cat] = blocks[key].get(cat, 0) + 1

    land_area_1000 = land_cells * step * step / 1000.0
    on_land = sum(sum(c.values()) for c in blocks.values())

    # 只统计"块内至少有 3 个符号"的块 —— 少于 3 个时"占优"是噪声，不是地域
    solid = {k: v for k, v in blocks.items() if sum(v.values()) >= 3}
    purity = 0.0
    if solid:
        purity = sum(max(v.values()) / sum(v.values()) for v in solid.values()) / len(solid)
    same = tot_nb = 0
    for (bx, by), v in solid.items():
        dom = max(v, key=v.get)
        for nb in ((bx + 1, by), (bx, by + 1)):
            w = solid.get(nb)
            if w:
                tot_nb += 1
                if max(w, key=w.get) == dom:
                    same += 1
    cat_tot: dict[str, int] = {}
    for v in blocks.values():
        for k, n in v.items():
            cat_tot[k] = cat_tot.get(k, 0) + n
    return {
        "symbols_total": symbols["total"],
        "symbols_on_land": on_land,
        "by_category_on_land": dict(sorted(cat_tot.items(), key=lambda kv: -kv[1])),
        "density_per_1000px2": round(on_land / max(land_area_1000, 1e-6), 3),
        "blocks_with_3plus": len(solid),
        "regional_purity": round(purity, 3),
        "contiguity": round(same / tot_nb, 3) if tot_nb else None,
    }


def gap_ratio(mask: dict, symbols: dict) -> dict:
    """复刻该图层**自己的验收口径**：最近邻中位间距 ÷ 符号中位尺寸。

    `terrainHints.ts` 头部的注释里就是这么判的：比值 <1.5 读者看到的是"一片填充"，
    >1.5 才是"地上的符号"。作者用这套度量决定把 `CELL_PX` 从 16 加倍到 32。
    这里复刻它的意义是：**我的改动由该层自己的标准判分，而不是我的口味**；
    并且它顺带校准我的量具 —— 在 CELL_PX=32 时应复现作者报的 fit 视口 ≈2.27。
    """
    import numpy as np

    step = mask["step"]
    x0, y0 = int(mask["x"]), int(mask["y"])
    rows, cols = len(mask["sea"]), len(mask["sea"][0])

    pts = []
    for item in symbols["items"]:
        cat, sx, sy = item[0], item[1], item[2]
        size = item[3] if len(item) > 3 else 0.0
        i = (sx - x0) // step
        j = (sy - y0) // step
        if not (0 <= j < rows and 0 <= i < cols) or mask["sea"][j][i] == "1":
            continue
        pts.append((float(sx), float(sy), float(size)))
    if len(pts) < 8:
        return {"error": f"陆地符号只有 {len(pts)} 个，算不了间距"}

    P = np.array([[p[0], p[1]] for p in pts])
    S = np.array([p[2] for p in pts])
    # 最近邻距离（样本量几百，直接算全对距离矩阵）
    d = np.sqrt(((P[:, None, :] - P[None, :, :]) ** 2).sum(-1))
    np.fill_diagonal(d, np.inf)
    nn = d.min(axis=1)
    return {
        "land_symbols": len(pts),
        "gap_median": round(float(np.median(nn)), 1),
        "gap_p10": round(float(np.percentile(nn, 10)), 1),
        "mark_median": round(float(np.median(S)), 1),
        "ratio_median": round(float(np.median(nn) / max(np.median(S), 1e-6)), 2),
        "ratio_p10": round(float(np.percentile(nn, 10) / max(np.median(S), 1e-6)), 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("shot")
    ap.add_argument("--roi", metavar="X0,Y0,X1,Y1",
                    help="只统计地图视口（用颜色分割时必给，否则页面 chrome 会污染统计）")
    ap.add_argument("--mask", metavar="JSON", default="/tmp/map_mask.json",
                    help="DOM 真实陆地掩膜（probe_map_dom.cjs 产出）；给了就用它分割，最可信")
    ap.add_argument("--label-box", action="append", default=[],
                    metavar="X,Y,W,H", help="标签取样框，可给多次")
    ap.add_argument("--crop", metavar="X,Y,W,H",
                    help="把该区域放大 6 倍另存，用来人眼复核取样框是否框到字")
    ap.add_argument("--top-colors", type=int, default=0)
    ap.add_argument("--structure", action="store_true",
                    help="陆地是否有地域结构（需 --mask；含白噪声/脊线场两个自校准对照）")
    ap.add_argument("--symbols", metavar="JSON", nargs="?", const="/tmp/map_symbols.json",
                    default=None,
                    help="地面符号分布（probe_map_dom.cjs 产出）：密度/地域纯度/连片度")
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
    ls = None
    if args.mask and Path(args.mask).exists():
        mask = json.loads(Path(args.mask).read_text())
        ls = split_land_sea_by_mask(im, mask)
        print(f"\n[分割方式] DOM 陆地掩膜 {args.mask}（{mask['cols']}x{mask['rows']} @{mask['step']}px）"
              f"  ✅ 渲染器自己的答案")
    else:
        if roi is None:
            print("⚠️ 既无 --mask 又无 --roi：统计会被页面 chrome 污染，结论不可用")
        ls = split_land_sea(im, roi)
        print("\n[分割方式] 按颜色冷暖猜（R-B 符号）—— 陆地冷色斑块会被误判成海，仅作粗看")
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

    if args.structure:
        if not (args.mask and Path(args.mask).exists()):
            print("\n⚠️ --structure 需要 --mask（地域结构必须在真实陆地掩膜内统计）")
        else:
            st = structure_stats(im, json.loads(Path(args.mask).read_text()))
            print("\n[地域结构] 在陆地掩膜内统计")
            if "error" in st:
                print(f"  {st['error']}")
            else:
                for label, key in (("本次渲染", "land"), ("对照·白噪声", "control_white_noise"),
                                   ("对照·脊线场", "control_ridge_field")):
                    v = st[key]
                    print(f"  {label:12} coarse/fine {v['coarse_fine']:>6}   "
                          f"方向一致性 {v['coherence']:>5}")
                print(f"  陆地格点 {st['land_cells']}")
                print("  读法：coarse/fine 越接近 0 越像只有细噪声；方向一致性接近白噪声 ⇒ 无地貌走向。")

    if args.symbols:
        if not (args.mask and Path(args.mask).exists()):
            print("\n⚠️ --symbols 需要 --mask（密度必须按陆地面积算）")
        elif not Path(args.symbols).exists():
            print(f"\n⚠️ 找不到 {args.symbols}（先用 probe_map_dom.cjs 采集）")
        else:
            sym = json.loads(Path(args.symbols).read_text())
            ss = symbol_stats(json.loads(Path(args.mask).read_text()), sym)
            print("\n[地面符号] 只在陆地内统计（海上的浪不计入地貌）")
            print(f"  符号总数 {ss['symbols_total']}，其中陆地 {ss['symbols_on_land']}"
                  f"   （渲染器自报预算 NODE_BUDGET=1400 ⇒ 用了 "
                  f"{ss['symbols_on_land'] / 1400 * 100:.0f}%）")
            print(f"  密度 {ss['density_per_1000px2']} 个/千像素"
                  f"   （≈ 每 {1 / max(ss['density_per_1000px2'], 1e-9) * 1000 ** 0.5:.0f}×"
                  f"{1 / max(ss['density_per_1000px2'], 1e-9) * 1000 ** 0.5:.0f} 像素一个）")
            print(f"  陆地类别分布 {ss['by_category_on_land']}")
            print(f"  有效块(≥3 符号) {ss['blocks_with_3plus']} 个"
                  f"   地域纯度 {ss['regional_purity']}"
                  f"   连片度 {ss['contiguity']}")
            print("  读法：纯度接近 1/类别数 ⇒ 到处一个样（撒盐）；"
                  "接近 1 且连片度高 ⇒ 成片的地域（山成脉、林成片）。")

            g = gap_ratio(json.loads(Path(args.mask).read_text()), sym)
            print("\n[间距比] 该图层自己的验收口径（terrainHints.ts 头部注释）")
            if "error" in g:
                print(f"  {g['error']}")
            else:
                print(f"  陆地符号 {g['land_symbols']}   最近邻中位间距 {g['gap_median']}px"
                      f"（p10 {g['gap_p10']}px）   符号中位尺寸 {g['mark_median']}px")
                print(f"  比值 中位 {g['ratio_median']}   p10 {g['ratio_p10']}"
                      f"    （<1.5 读成一片填充；≥1.5 才是'地上的符号'）")
                print(f"  作者在 CELL_PX=32 时报 fit 视口 ≈2.27 —— 复现得上就说明量具可信。")

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
