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


def hierarchy_stats(mask: dict, symbols: dict, marks: dict) -> dict:
    """图形层级：读者能不能一眼把"地点标记"和"地面纹理"分开。

    V3 那条待办里有两句话，但只有一句是可算的：

      1. "浅色底板比它里面的字形更主导" —— 用 `plate_diameter / icon_width` 直接驳回或确认。
      2. "地面符号与地点标记**同尺寸同重量**，两种语言打架" —— 这句至今没数。
         判据就是**两个尺寸分布的重叠**：同尺寸必然落在同一个尺寸带里。

    重叠用两个口径，因为"重叠"可以被糊弄：

    - `sep`：每个 tier 的标记中位尺寸 ÷ 陆地地面符号中位尺寸。>3 才算读者能一眼分级。
    - `marks_in_ground_band`：**标记**里有多少个的尺寸落进地面符号的 [p10,p90] 带。

    第二条第一版写反了：我拿**最小 tier**（building，5.7–6.0px）的尺寸带去撞地面符号，
    而那个带整个落在地面分布**下方**，于是得到 `0 / 110 = 0.0%` —— 一个漂亮的无用答案。
    分母也选错了：问"两种语言撞不撞车"，被数的不该是地面符号，而是**标记** ——
    读者的困惑发生在"这是地物还是纹理"的判读上，标记是少数派、是那个本该跳出来的东西。
    现在改成数标记，并且用**地面墨迹自己的分布**当靶子，而不是拿某个 tier 去当靶子。

    密度一并报，因为"重量"不只是单个符号的大小：一整屏都是同尺寸的符号，
    即使每个都比标记小，图面也会读成"一层纹理上撒了些东西"而不是"地物在地面上"。

    统计功效：陆地符号几百个、标记 91 个，中位数是稳的。`marks_in_ground_band` 的分母
    是标记数（91），所以分辨率到 1% 是有意义的 —— 但它**不**回答"读者是不是真的会混淆"，
    只回答"两种语言有没有共用一个尺寸带"。
    """
    import numpy as np

    step = mask["step"]
    x0, y0 = int(mask["x"]), int(mask["y"])
    rows, cols = len(mask["sea"]), len(mask["sea"][0])

    def on_land(sx: int, sy: int) -> bool:
        i = (sx - x0) // step
        j = (sy - y0) // step
        return 0 <= j < rows and 0 <= i < cols and mask["sea"][j][i] != "1"

    ground = [float(it[3]) for it in symbols["items"]
              if len(it) > 3 and it[3] and on_land(it[1], it[2])]
    if len(ground) < 20:
        return {"error": f"陆地地面符号只有 {len(ground)} 个，算不了尺寸分布"}
    G = np.array(ground)

    by_tier: dict[str, list[float]] = {}
    plate_ratio: dict[str, list[float]] = {}
    all_marks: list[float] = []
    for m in marks["items"]:
        iw, ih = m["icon"]
        if iw <= 0:
            continue
        all_marks.append(max(iw, ih))
        by_tier.setdefault(m["tier"], []).append(max(iw, ih))
        pw, ph = m.get("plate", [0, 0])
        if pw > 0 and iw > 0:
            plate_ratio.setdefault(m["tier"], []).append(pw / iw)

    g_med = float(np.median(G))
    g_band = [float(np.percentile(G, 10)), float(np.percentile(G, 90))]
    out: dict = {
        "ground_on_land": len(G),
        "ground_p10": round(g_band[0], 1),
        "ground_median": round(g_med, 1),
        "ground_p90": round(g_band[1], 1),
        "tiers": {},
    }

    for t in sorted(by_tier, key=lambda k: -float(np.median(by_tier[k]))):
        v = np.array(by_tier[t])
        out["tiers"][t] = {
            "n": len(v),
            "icon_median": round(float(np.median(v)), 1),
            "icon_min": round(float(v.min()), 1),
            "icon_max": round(float(v.max()), 1),
            "sep_vs_ground": round(float(np.median(v)) / max(g_med, 1e-6), 2),
            "plate_over_icon": (round(float(np.median(plate_ratio[t])), 3)
                                if plate_ratio.get(t) else None),
        }

    # 被数的是标记，不是地面符号；靶子是地面自己的墨迹分布。
    M = np.array(all_marks)
    in_band = (M >= g_band[0]) & (M <= g_band[1])
    out["marks_total"] = len(M)
    out["marks_in_ground_band"] = int(in_band.sum())
    out["marks_in_ground_band_share"] = round(float(in_band.mean()), 4)
    out["marks_below_ground_p10"] = int((M < g_band[0]).sum())
    out["marks_above_ground_p90"] = int((M > g_band[1]).sum())
    if plate_ratio:
        all_pr = [r for v in plate_ratio.values() for r in v]
        out["plate_over_icon_median"] = round(float(np.median(all_pr)), 3)
    return out


def label_ink_stats(im_on: Image.Image, im_off: Image.Image, marks: dict) -> dict:
    """标签被压住之后**还读不读得出来** —— 光学口径，不是几何口径。

    为什么必须有这一条：`probe_map_dom.cjs` 报的遮挡是**几何**的（标签盒 ∩ 底板盒的面积），
    而底板改完之后只剩 alpha 0.30 的一层雾。**深色文字底下垫一层 30% 白雾会抬高对比度、
    不是抹掉它** —— 所以几何口径在改动之后会**高估**残余问题。
    （这一条是被自己的改动逼出来的：几何计数降不下来的两个标签，
    在图上看其实已经恢复可读了。）

    判法：同一个标签的屏幕盒，在两态图里各取
      ink = 框内亮度 p5（最暗的墨）   bg = 框内亮度 p95（最亮的底/白描边）
      contrast = (bg+5)/(ink+5)
    底板若真的压在字上，它会把**墨也一起提亮** ⇒ ink 上移 ⇒ contrast 掉。
    两态对比即得"这块底板对这条标签造成了多少可读性损失"。

    用同一份 `labelRect`（取自 ON 态）量两张图 —— 标签位置由碰撞求解器给，
    两态下可能不同，所以**必须**用同一组框，否则量到的是"标签挪了位置"而不是"被压住"。
    这也是这条判据的边界：它只回答"同一位置上的墨有没有被冲淡"，不回答"标签挪到哪更好"。
    """
    import numpy as np

    def lum(im):
        a = np.asarray(im.convert("RGB"), dtype=np.float64)
        return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]

    Lon, Loff = lum(im_on), lum(im_off)
    H, W = Lon.shape

    rows = []
    for m in marks["items"]:
        r = m.get("labelRect")
        if not r or not m.get("labelText"):
            continue
        x, y, w, h = (int(round(v)) for v in r)
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(W, x + w), min(H, y + h)
        if x1 - x0 < 6 or y1 - y0 < 6:
            continue

        def stat(L):
            crop = L[y0:y1, x0:x1].ravel()
            ink = float(np.percentile(crop, 5))
            bg = float(np.percentile(crop, 95))
            return ink, bg, (bg + 5.0) / (ink + 5.0)

        i_on, b_on, c_on = stat(Lon)
        i_off, b_off, c_off = stat(Loff)
        rows.append({
            "text": m["labelText"], "tier": m["tier"],
            "contrast_on": round(c_on, 2), "contrast_off": round(c_off, 2),
            "ink_on": round(i_on, 1), "ink_off": round(i_off, 1),
            "loss": round(1 - c_on / max(c_off, 1e-6), 3),
        })

    if not rows:
        return {"error": "没有可量的标签框（marks JSON 里没有 labelRect）"}
    losses = np.array([r["loss"] for r in rows])
    rows.sort(key=lambda r: -r["loss"])
    return {
        "labels_measured": len(rows),
        "loss_median": round(float(np.median(losses)), 4),
        "loss_p90": round(float(np.percentile(losses, 90)), 4),
        "labels_losing_over_10pct": int((losses > 0.10).sum()),
        "labels_losing_over_25pct": int((losses > 0.25).sum()),
        "labels_ink_darker_when_plate_on": int(sum(1 for r in rows if r["ink_on"] > r["ink_off"])),
        "worst": rows[:10],
        "best": rows[-3:],
    }


def layer_contribution(im_on: Image.Image, im_off: Image.Image, mask: dict) -> dict:
    """某一层**到底给画面贡献了多少** —— 关掉它、在掩膜内逐像素差。

    这是这条线上一直缺的那一类判据。`--symbols` / `--hierarchy` 量的是**符号放得对不对**，
    `gap_ratio` 量的是**间距比**，机制探针量的是**密度跟随哪个场** ——
    没有一个回答"这一层在屏幕上还剩多少墨"。实测：地面符号层在 fit 缩放下
    陆地平均 |ΔL| 只有 **0.75 级**、高频能量 **−0.4%**，
    而它上面已经调过三支 PR 的密度与坡度耦合。

    两个量，缺一不可：

    - `meanAbs`：平均 |ΔL|（0–255）。这是"平均多少墨"，但对**细而稀疏**的东西会偏小 ——
      几十个 15px 的淡符号摊到 31 万像素上，均值天然被稀释。
    - `hf`：高频能量 std（图减自身的盒模糊）。这是"**纹理**还剩多少"，
      与面积无关，才是眼睛读作"有质地"的那个量。

    只报 meanAbs 会得出"这层没用"；只报 hf 会看不出"变淡了"。**两个一起报**，
    并且要用 `--hide <selector>` 拍对照图 —— 关掉的方式与 `probe_map_dom.cjs` 同一套，
    不另写一份判定。
    """
    import numpy as np

    def lum(im):
        a = np.asarray(im.convert("RGB"), dtype=np.float64)
        return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]

    Lon, Loff = lum(im_on), lum(im_off)
    H = min(Lon.shape[0], Loff.shape[0])
    W = min(Lon.shape[1], Loff.shape[1])
    Lon, Loff = Lon[:H, :W], Loff[:H, :W]

    step, x0, y0 = mask["step"], int(mask["x"]), int(mask["y"])
    sea = mask["sea"]
    land_m = np.zeros((H, W), bool)
    sea_m = np.zeros((H, W), bool)
    for j, row in enumerate(sea):
        for i, ch in enumerate(row):
            y, x = y0 + j * step, x0 + i * step
            if 0 <= y < H and 0 <= x < W:
                (sea_m if ch == "1" else land_m)[y:y + step, x:x + step] = True

    def box(L, k=5):
        p = np.pad(L, k // 2, mode="edge")
        c = np.cumsum(np.cumsum(p, 0), 1)
        S = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
        return S[:L.shape[0], :L.shape[1]] / (k * k)

    def hf(L, m):
        blur = box(box(L))
        r = L[:blur.shape[0], :blur.shape[1]] - blur
        return float(r[m[:r.shape[0], :r.shape[1]]].std())

    d = np.abs(Lon - Loff)
    out = {}
    for name, m in (("land", land_m), ("sea", sea_m)):
        if not m.any():
            continue
        v = d[m]
        out[name] = {
            "px": int(v.size),
            "mean_abs": round(float(v.mean()), 3),
            "p90": round(float(np.percentile(v, 90)), 2),
            "p99": round(float(np.percentile(v, 99)), 2),
            "share_gt2": round(float((v > 2).mean()), 4),
            "hf_on": round(hf(Lon, m), 3),
            "hf_off": round(hf(Loff, m), 3),
        }
        o = out[name]
        o["hf_delta_pct"] = round((o["hf_on"] / max(o["hf_off"], 1e-9) - 1) * 100, 2)
    return out


def region_variety(im: Image.Image, mask: dict, block: int = 128) -> dict:
    """陆地在**区域尺度**上分化了没有 —— 「读得出地理」的可算形式。

    为什么要这一条：`--structure` 量的是**局部**有没有走向，`--contrib` 量的是**某层**的墨量，
    两个都回答不了"这四块大陆是不是同一块地"。实测（2026-09-28）三块大陆的均色
    只差约 15 级明度，而各大陆内部的纹理幅度是 36–50 级 —— 也就是说
    **区域之间的差异小于区域内部纹理的 1/3**，图在统计上是**均质**的。

    判据 = **块间 std ÷ 块内 std**（块内取各块 std 的均值）：

    - **比值 < 1** ⇒ 任意两地的差别比任一块地内部的纹路还小 ⇒ 读者读到的是
      "同一种质地铺满全图"，不是"山地 / 平原 / 林"。
    - **比值 > 1** ⇒ 区域之间真的不同（块与块能分辨），这才叫有地理。

    三个通道分开报，因为它们背后是三个不同的旋钮：**明度**（高度分布的均值）、
    **彩度**（调色板的饱和度跨度）、**冷暖**（色相方向）。一个烘焙配方可能只动其中一个 ——
    实测 `_RELIEF_MASK` 只动**幅度**（块内 std），不动**均值**，所以明度比值 0.179：
    省份之间"粗糙程度"不同，但都不是"另一种颜色"。

    口径与边界：块只取**几乎全陆地**（陆地占比 >0.7）的，否则块均值被海面拉走；
    块边取整会造成边缘浪费，所以只报块数。块大小 128px 是"区域"的量级 ——
    **换块大小会换出不同的比值**，结论只在同一块大小下可比。
    """
    import numpy as np

    a = np.asarray(im.convert("RGB"), dtype=np.float64)
    H, W = a.shape[:2]
    step, x0, y0 = mask["step"], int(mask["x"]), int(mask["y"])
    land = np.zeros((H, W), bool)
    for j, row in enumerate(mask["sea"]):
        for i, ch in enumerate(row):
            y, x = y0 + j * step, x0 + i * step
            if 0 <= y < H and 0 <= x < W:
                land[y:y + step, x:x + step] = ch != "1"

    mx, mn = a.max(-1), a.min(-1)
    chans = {
        "sat": (mx - mn) / np.maximum(mx, 1e-6),
        "warm": (a[..., 0] - a[..., 2]) / 255.0,
        "lum": (0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]) / 255.0,
    }

    out: dict = {"block": block, "blocks": 0, "channels": {}}
    for name, f in chans.items():
        means, stds = [], []
        for by in range(0, H - block + 1, block):
            for bx in range(0, W - block + 1, block):
                m = land[by:by + block, bx:bx + block]
                if m.mean() > 0.7:
                    v = f[by:by + block, bx:bx + block][m]
                    means.append(float(v.mean()))
                    stds.append(float(v.std()))
        if len(means) < 12:
            out["channels"][name] = {"error": f"全陆地块只有 {len(means)} 个"}
            continue
        between = float(np.std(means))
        within = float(np.mean(stds))
        out["channels"][name] = {
            "blocks": len(means),
            "between": round(between, 4),
            "within": round(within, 4),
            "ratio": round(between / max(within, 1e-9), 3),
        }
        out["blocks"] = max(out["blocks"], len(means))
    return out


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
    ap.add_argument("--hierarchy", metavar="MARKS_JSON", nargs="?",
                    const="/tmp/map_marks.json", default=None,
                    help="图形层级：标记 vs 地面符号的尺寸分带（需 --mask），"
                         "MARKS_JSON 由 probe_map_dom.cjs 产出")
    ap.add_argument("--label-ink", metavar="PLATE_OFF_SHOT",
                    help="标签可读性的光学口径：本图（底板在）对比 PLATE_OFF_SHOT"
                         "（`.loc-plate` 全关），逐标签比同一位置的墨迹对比度。"
                         "几何遮挡会高估残余问题，这条是它的校正。")
    ap.add_argument("--marks", metavar="JSON", default="/tmp/map_marks.json",
                    help="标记几何（probe_map_dom.cjs 产出），--label-ink 与 --hierarchy 共用")
    ap.add_argument("--contrib", metavar="LAYER_OFF_SHOT",
                    help="某层贡献了多少画面：本图（层在）对比 LAYER_OFF_SHOT"
                         "（用 probe_map_dom.cjs --hide <sel> 拍），掩膜内报平均 |ΔL| 与高频能量")
    ap.add_argument("--region-variety", action="store_true",
                    help="陆地在区域尺度上分化了没有（块间 std ÷ 块内 std；<1 即全图同一种质地）")
    ap.add_argument("--block", type=int, default=128,
                    help="--region-variety 的块大小（px）；比值只在同一块大小下可比")
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

    if args.hierarchy:
        if not (args.mask and Path(args.mask).exists()):
            print("\n⚠️ --hierarchy 需要 --mask（只看陆地：海上的东西不是地物）")
        elif not Path(args.hierarchy).exists():
            print(f"\n⚠️ 找不到 {args.hierarchy}（先用 probe_map_dom.cjs 采集）")
        else:
            hs = hierarchy_stats(json.loads(Path(args.mask).read_text()),
                                 json.loads(Path(args.symbols or "/tmp/map_symbols.json").read_text()),
                                 json.loads(Path(args.hierarchy).read_text()))
            print("\n[图形层级] 地点标记 vs 地面符号（都在真实屏幕像素下，同一把尺子）")
            if "error" in hs:
                print(f"  {hs['error']}")
            else:
                print(f"  陆地地面符号 {hs['ground_on_land']} 个"
                      f"   尺寸 p10/中位/p90 = {hs['ground_p10']} / {hs['ground_median']}"
                      f" / {hs['ground_p90']} px")
                print(f"\n  {'tier':<12}{'n':>5}{'标记中位':>10}{'最小':>8}{'最大':>8}"
                      f"{'÷地面中位':>11}{'底板/字形':>11}")
                for t, v in hs["tiers"].items():
                    pr = v["plate_over_icon"]
                    print(f"  {t:<12}{v['n']:>5}{v['icon_median']:>10}{v['icon_min']:>8}"
                          f"{v['icon_max']:>8}{v['sep_vs_ground']:>11}"
                          f"{(f'{pr:.2f}' if pr is not None else 'n/a'):>11}")
                print(f"\n  地面符号的尺寸带 p10–p90 = {hs['ground_p10']}–{hs['ground_p90']} px"
                      f"（中位 {hs['ground_median']}）")
                print(f"  落进这一带的地点标记 {hs['marks_in_ground_band']} / "
                      f"{hs['marks_total']} = {hs['marks_in_ground_band_share'] * 100:.0f}%"
                      f"   （比它还小的 {hs['marks_below_ground_p10']} 个，"
                      f"比它大的 {hs['marks_above_ground_p90']} 个）")
                if "plate_over_icon_median" in hs:
                    print(f"  底板直径 ÷ 字形实绘宽度（全体中位）= "
                          f"{hs['plate_over_icon_median']:.2f}")
                print("  读法：sep_vs_ground 是「标记比地面符号大几倍」—— ≥3 读者能一眼分级；"
                      "≈1 就是两种语言同尺寸。marks_in_ground_band 是**标记**落进底部纹理"
                      "尺寸带的比例 —— 这一批就是读者分不出「地物 vs 纹理」的那一批。"
                      "底板/字形 >1 说明底盘比字形宽，那一眼看到的是底板不是图形。")

    if args.label_ink:
        if not Path(args.label_ink).exists():
            print(f"\n⚠️ 找不到对照图 {args.label_ink}（用 probe_map_dom.cjs --hide '.loc-plate' 拍一张）")
        elif not Path(args.marks).exists():
            print(f"\n⚠️ 找不到 {args.marks}（先用 probe_map_dom.cjs 采集，且要在**底板开**的那一次）")
        else:
            mk = json.loads(Path(args.marks).read_text())
            li = label_ink_stats(im, Image.open(args.label_ink).convert("RGB"), mk)
            print("\n[标签可读性·光学口径] 底板在 vs 底板全关，同一组标签框")
            if "error" in li:
                print(f"  {li['error']}")
            else:
                print(f"  量到 {li['labels_measured']} 个标签   对比度损失 中位 "
                      f"{li['loss_median'] * 100:.1f}%   p90 {li['loss_p90'] * 100:.1f}%")
                print(f"  损失 >10% 的 {li['labels_losing_over_10pct']} 个；"
                      f">25% 的 {li['labels_losing_over_25pct']} 个")
                print(f"  底板反而让墨更深（对比度变好）的 {li['labels_ink_darker_when_plate_on']} 个")
                print(f"\n  {'标签':<10}{'tier':<11}{'底板在':>8}{'底板关':>8}{'损失':>8}")
                for r in li["worst"]:
                    print(f"  {r['text']:<10}{r['tier']:<11}{r['contrast_on']:>8}"
                          f"{r['contrast_off']:>8}{r['loss'] * 100:>7.1f}%")
                print("  读法：几何遮挡计数（probe_map_dom.cjs）只回答「框有没有被盖住」；"
                      "这一条回答「盖住之后还读不读得出来」。"
                      "深色墨底下垫一层浅雾会**抬高**对比度，所以几何口径在改造之后会高估残余问题。")

    if args.contrib:
        if not Path(args.contrib).exists():
            print(f"\n⚠️ 找不到对照图 {args.contrib}（用 probe_map_dom.cjs --hide <sel> 拍一张）")
        elif not (args.mask and Path(args.mask).exists()):
            print("\n⚠️ --contrib 需要 --mask（贡献必须按陆地/海面分开算）")
        else:
            co = layer_contribution(im, Image.open(args.contrib).convert("RGB"),
                                    json.loads(Path(args.mask).read_text()))
            print("\n[图层贡献] 关掉该层前后，在渲染器自己的掩膜内逐像素差")
            if not co:
                print("  掩膜内没有像素")
            for name, label in (("land", "陆地"), ("sea", "海上")):
                o = co.get(name)
                if not o:
                    continue
                print(f"  {label} 像素 {o['px']}")
                print(f"    平均 |ΔL| {o['mean_abs']} 级   p90 {o['p90']}   p99 {o['p99']}"
                      f"   变化 >2 级的占比 {o['share_gt2'] * 100:.2f}%")
                print(f"    高频能量 std  {o['hf_off']} → {o['hf_on']}"
                      f"   （{o['hf_delta_pct']:+.2f}%）")
            print("  读法：平均 |ΔL| 对**细而稀疏**的东西会偏小（几十个 15px 淡符号摊到几十万像素上）；"
                  "高频能量才是眼睛读作「有质地」的那个量。两个一起看："
                  "均值小而高频也为零 ⇒ 这层没上屏；均值小但高频明显 ⇒ 只够看成细纹理。")

    if args.region_variety:
        if not (args.mask and Path(args.mask).exists()):
            print("\n⚠️ --region-variety 需要 --mask（块统计必须在陆地里做，否则被海面拉走）")
        else:
            rv = region_variety(im, json.loads(Path(args.mask).read_text()), args.block)
            print(f"\n[区域分化] 陆地块 {args.block}px，块间 std ÷ 块内 std（>1 才叫有地理）")
            for name, zh, why in (("lum", "明度", "高度分布的均值在各区域是否不同"),
                                  ("sat", "彩度", "调色板的饱和度跨度"),
                                  ("warm", "冷暖", "色相方向")):
                c = rv["channels"].get(name, {})
                if "error" in c:
                    print(f"  {zh}  {c['error']}")
                else:
                    verdict = "✅ 区域可分辨" if c["ratio"] > 1 else "❌ 全图同一种质地"
                    print(f"  {zh}  块数 {c['blocks']:3}   块间 {c['between']:.4f}"
                          f"   块内 {c['within']:.4f}   比值 {c['ratio']:.3f}   {verdict}")
            print("  读法：比值 <1 ⇒ 任意两地的差别比任一块地内部的纹路还小。"
                  f"换 --block 会换出不同比值，{args.block} 的结论只与同块大小可比。")

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
