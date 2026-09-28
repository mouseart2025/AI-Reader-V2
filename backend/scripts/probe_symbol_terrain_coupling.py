#!/usr/bin/env python3
"""符号密度是否跟着**地形**走 —— 用与生产同一套量。

    python scripts/probe_symbol_terrain_coupling.py [--shot SHOT.png]

## 为什么要有这个脚本

判据来自制图规范（GB/T 20257.1-2017 的「相应式」、Lehmann 的 hachure 规则）：
**密度随实地疏密，陡则密、平则留白**。所以要到的是「块内符号数 vs 块内地形起伏」
的**正相关**。

## 上一版量具错在哪（这是本脚本存在的全部理由）

第一版把 x 轴取成**合成画面**里的局部起伏 —— 而合成画面里叠着羊皮纸噪点、
区域洗、海面填充。纸纹与地形无关，于是它给 x 轴灌了噪声，**把相关系数系统性地
拉向 0**（实测把 +0.01 压成 −0.111）。我据此把两次本可能有用的修改判成"失败并
回退"，还把这个判断写进了代码注释。

现在 x 轴取**该层真正消费的那个量**：与 `NovelMap.tsx` 的采样器同一套算法
（384px 降采样 → 可分离盒模糊 R=6 求局部均值 → 按自身 p95 归一），作用在
`terrain.v*.png` 本身。**量具与被测方共用同一个量**，这是本仓反复栽过的那条。

## 需要的输入

  /tmp/map_mask.json     陆地掩膜（probe_map_dom.cjs 产出）
  /tmp/map_symbols.json  地面符号位置（同上）
  视口变换                probe_map_dom.cjs 打印的 `viewportTransform:`
  地形图                  <数据目录>/maps/<novel_id>/terrain.v*.png

`--shot` 只用于把 screen→canvas 变换读进来做校验，缺省时用上次实测值。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CANVAS_W, CANVAS_H = 8000, 4500          # 西游记 overworld
SAMPLER_W = 384                          # 与 NovelMap 的采样器一致
SAMPLER_BLUR_R = 6                       # 同上


def box1d(x: np.ndarray, r: int, axis: int) -> np.ndarray:
    pad = np.pad(x, [(r, r) if i == axis else (0, 0) for i in range(x.ndim)], mode="edge")
    c = np.cumsum(pad, axis=axis)
    n = x.shape[axis]
    if axis == 0:
        return (c[2 * r:2 * r + n] - c[:n]) / (2 * r + 1)
    return (c[:, 2 * r:2 * r + n] - c[:, :n]) / (2 * r + 1)


def anomaly_field(terrain_png: Path) -> np.ndarray:
    """复刻采样器：返回 [-1,1] 的有符号起伏场。"""
    im = Image.open(terrain_png).convert("RGB")
    w = SAMPLER_W
    h = max(1, round(w * CANVAS_H / CANVAS_W))
    a = np.asarray(im.resize((w, h), Image.LANCZOS), dtype=np.float64)
    lum = 0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1] + 0.0722 * a[:, :, 2]
    mean = box1d(box1d(lum, SAMPLER_BLUR_R, 1), SAMPLER_BLUR_R, 0)
    dev = lum - mean
    p95 = float(np.percentile(np.abs(dev), 95)) or 1.0
    return np.clip(dev / p95, -1.0, 1.0)


def viewport_transform(text: str | None) -> tuple[float, float, float]:
    if text:
        t = re.search(r"translate\(([-\d.]+)[, ]+([-\d.]+)\)", text)
        s = re.search(r"scale\(([\d.]+)\)", text)
        if t and s:
            return float(t.group(1)), float(t.group(2)), float(s.group(1))
    return -40.720986122955196, 39.35882626560198, 0.17985890379099154


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--novel", default="3b2ef56c-1a55-466a-a7d1-34272446a198")
    ap.add_argument("--mask", default="/tmp/map_mask.json")
    ap.add_argument("--symbols", default="/tmp/map_symbols.json")
    ap.add_argument("--viewport", default=None, help="probe_map_dom.cjs 打印的变换串")
    args = ap.parse_args()

    from src.infra.config import DATA_DIR

    maps = sorted((DATA_DIR / "maps" / args.novel).glob("terrain.v*.png"))
    if not maps:
        sys.exit(f"找不到地形图：{DATA_DIR / 'maps' / args.novel}")
    terrain = maps[-1]

    anom = anomaly_field(terrain)
    tx, ty, k = viewport_transform(args.viewport)
    print(f"地形 {terrain.name} {terrain.stat().st_size // 1024}KB  "
          f"anomaly std {anom.std():.3f}  视口 t=({tx:.1f},{ty:.1f}) k={k:.3f}")

    mask = json.loads(Path(args.mask).read_text())
    sym = json.loads(Path(args.symbols).read_text())
    step, x0, y0 = mask["step"], int(mask["x"]), int(mask["y"])
    land = np.array([[c != "1" for c in row] for row in mask["sea"]], dtype=bool)
    rows, cols = land.shape
    h, w = anom.shape

    def sample(cx: float, cy: float) -> float:
        ix = int(np.clip(round(cx / CANVAS_W * w), 0, w - 1))
        iy = int(np.clip(round(cy / CANVAS_H * h), 0, h - 1))
        return float(anom[iy, ix])

    print(f"\n{'块':>5}{'有效块':>7}{'低起伏':>9}{'高起伏':>9}{'高/低':>8}{'r':>9}")
    for b, thr in ((20, 0.7), (16, 0.7), (12, 0.65), (8, 0.6), (6, 0.5), (4, 0.4)):
        bh, bw = rows // b, cols // b
        px_block = b * step
        cnt, amp = [], []
        for bj in range(bh):
            for bi in range(bw):
                sl = (slice(bj * b, (bj + 1) * b), slice(bi * b, (bi + 1) * b))
                if land[sl].mean() < thr:
                    continue
                vals = []
                for jj in range(bj * b, (bj + 1) * b, 2):
                    for ii in range(bi * b, (bi + 1) * b, 2):
                        sx = x0 + ii * step + step / 2
                        sy = y0 + jj * step + step / 2
                        vals.append(abs(sample((sx - tx) / k, (sy - ty) / k)))
                if not vals:
                    continue
                bcx = x0 + bi * px_block + px_block / 2
                bcy = y0 + bj * px_block + px_block / 2
                n = sum(
                    1
                    for c, sx, sy, *_ in sym["items"]
                    if c != "water"
                    and abs(sx - bcx) < px_block / 2
                    and abs(sy - bcy) < px_block / 2
                )
                cnt.append(n)
                amp.append(float(np.mean(vals)))
        cnt = np.asarray(cnt, float)
        amp = np.asarray(amp, float)
        hi = amp >= np.median(amp)
        r = float(np.corrcoef(cnt, amp)[0, 1]) if len(cnt) > 2 else float("nan")
        print(f"{b:>5}{len(cnt):>7}{cnt[~hi].mean():>9.2f}{cnt[hi].mean():>9.2f}"
              f"{cnt[hi].mean() / max(cnt[~hi].mean(), 1e-6):>7.2f}x{r:>+9.3f}")
    print("\n目标：r ≥ +0.4，高/低 ≥ 1.5x（制图规范：密度随实地）")


if __name__ == "__main__":
    main()
