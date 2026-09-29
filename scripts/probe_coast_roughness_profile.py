#!/usr/bin/env python3
"""probe_coast_roughness_profile.py — 海岸线的粗糙度**沿程分布**，以及它是否跟随地貌。

## 为什么需要它（这条是用户给的，我的判据看不见）

盒计数分形维数只回答"**平均**够不够粗"，它**结构上看不见粗糙度在哪儿**。
实测后果：整条海岸线被均匀加了 ~30 单位的锯齿，于是

- **山海交界**处该有的锯齿有了；
- **平原与海交界**处**同样**有 —— 而真实地貌那里是平滑的；
- 整幅图因此读作"被撕碎的补丁"。

★ 用户的原话："正常的地貌，只有在山海交界的地方，才会有锯齿较多情况，对于平原和海交界
的地方，海岸线通常都很平滑。" 这是**地貌学常识**，而我的判据对它完全失明 ——
因为我只有一个全局标量，且**均匀度不在它的量程里**。

## 判据：弯曲度（sinuosity）沿程分布 × 地貌代理

- **弯曲度** = 窗口内**弧长 ÷ 弦长**。这是地貌学标准量：平直岸段 ≈1.00–1.03，
  岬湾岸段 1.05–1.15，强锯齿 >1.20。窗口取 ~300 canvas 单位。
- **地貌代理**：到"崎岖类地点"（山/洞/林/岭/谷）与"平缓类地点"（水/城/国/平原）的**最近距离**。
  用**数据**而不是用我生成时的权重函数 —— 否则就是在量自己的定义。

**健康的样子**：崎岖近处的弯曲度显著高于平缓近处（有分离）。
**当前的样子预期**：两组几乎重合 —— 那正是"均匀施加"的指纹。

## 用法

  python3 scripts/probe_coast_roughness_profile.py /tmp/coastlines.json /tmp/map.json [--window 300]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).parent))
from probe_coastline_morphology import boxcount, parse_path, sample_cubics  # noqa: E402

RUGGED_ICONS = {"mountain", "cave", "forest", "desert"}
RUGGED_CHARS = ("山", "洞", "林", "岭", "谷", "峰", "坡", "崖")
SMOOTH_CHARS = ("水", "河", "湖", "海", "城", "国", "州", "村", "镇", "府", "宫", "寺")


def classify(loc: dict) -> str:
    icon = (loc.get("icon") or "").strip()
    if icon in RUGGED_ICONS:
        return "rugged"
    if icon in {"water", "city", "plains", "palace"}:
        return "smooth"
    name = loc.get("name") or ""
    typ = loc.get("type") or ""
    hay = name + typ
    if any(c in hay for c in RUGGED_CHARS):
        return "rugged"
    if any(c in hay for c in SMOOTH_CHARS):
        return "smooth"
    return "other"


def rings_from_map_json(path: str) -> list[np.ndarray]:
    """从 `/api/novels/<id>/map` 的产物里读**有序**的海岸线环。

    ⚠️ **必须用后端环，不能用 DOM 里 `d` 的采样序列。** 原因（实测）：
    rough.js 把每个输入段画成**两个**独立的 sketchy 三次（实测 10432 个三次 ÷ 5216 个输入顶点 = 2.0），
    而 `d` 里这些三次**不按环链接**（相邻三次首尾相接率 0.0%）。于是"按顺序取相邻点求和"
    会把**笔画之间的跳变**（最大 70 单位）也当成折线，导致：
      - 弧长被算成 2 倍（167842 vs 后端 83602）；
      - 弯曲度算出 **6.06** —— 物理上不可能（海岸线 ≤ ~2），量具坏了的信号；
      - 盒计数分形维数被抬到 1.2314（有序环上是 1.2041）。
    后端环是有序的、无歧义，且 two-pass 描边带来的额外粗糙度是**均匀**的，
    不影响"粗糙度沿程分布"这个问题。
    """
    mp = json.loads(Path(path).read_text())
    return [np.asarray(lm["coastline"], dtype=float) for lm in mp["landmasses"]]


def rings_from_shapes(shapes) -> list[np.ndarray]:
    """把 dump 里的 path 拆成**一圈一圈**（次级退路；首选用 `rings_from_map_json`）。

    拆法不靠"逐段首尾相接"（rough.js 每段都是独立三次、端点带各自的抖动，不共享端点），
    而是按**步长突变**切：相邻采样点的距离远大于中位步长处即为环的边界。
    注意这只解决"跨环跳变"，**不解决**"同一环内笔画之间不链接"——所以有后端环时不要用它。
    """
    rings: list[np.ndarray] = []
    for s in shapes:
        cubics, _ = parse_path(s["d"])
        if not cubics:
            continue
        pts = sample_cubics(cubics, 6)
        if len(pts) < 8:
            continue
        step = np.hypot(*np.diff(pts, axis=0).T)
        med = float(np.median(step))
        if med <= 0:
            continue
        cut = np.where(step > max(20.0 * med, 200.0))[0]
        bounds = np.concatenate([[-1], cut, [len(pts) - 1]])
        for i in range(len(bounds) - 1):
            seg = pts[bounds[i] + 1:bounds[i + 1] + 1]
            if len(seg) >= 8:
                rings.append(seg)
    return rings


def sinuosity_along(P: np.ndarray, window: float, step: float) -> np.ndarray:
    """沿闭合折线按弧长滑窗算 弧长÷弦长。返回与窗中心一一对应的 (x, y, sinuosity)。"""
    Q = np.vstack([P, P[:1]])
    seg = np.hypot(*np.diff(Q, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    total = s[-1]
    out = []
    t = window / 2.0
    while t < total - window / 2.0:
        a = np.searchsorted(s, t - window / 2.0)
        b = np.searchsorted(s, t + window / 2.0)
        if b > a:
            chord = float(np.hypot(*(Q[b] - Q[a])))
            if chord > 1e-6:
                out.append((float(Q[a][0]), float(Q[a][1]), (s[b] - s[a]) / chord))
        t += step
    return np.array(out) if out else np.zeros((0, 3))


def per_class_dimension(
    rings: list[np.ndarray],
    rugged_xy: np.ndarray,
    smooth_xy: np.ndarray,
    sizes,
    radius: float = 400.0,
    min_len: float = 1200.0,
) -> dict:
    """**按地貌分类**分别量分形维数 —— 逐**连续弧**量，再按弧长加权平均。

    ## 为什么必须分类量

    盒计数维数是**平均**量。拿整条海岸线的 D 去比"真实海岸线 1.15–1.35"，隐含假设了
    **整条海岸线同质** —— 而真实海岸线不同质：平原岸（荷兰、墨西哥湾）在几公里尺度上接近平直，
    岩石岸（挪威、苏格兰）才 1.3+。用户给的模型（山海交界才有锯齿）正是这个事实。
    所以判据必须分类：崎岖类 ≈ 岩石岸，平缓类 ≈ 平原岸。

    ## ⚠️ 为什么不能把同类的弧**拼起来**量（第一版就是这么错的）

    把 15 段弧拼成一个点集去盒计数，N(ε) 至少有"弧的段数"这个地板：每个弧在粗尺度上也占格子。
    于是**段数多的那一类被系统性压低**。实测这个伪影强到**把结论反过来**：
    崎岖类量到 1.126、平缓类 1.142，而同一份几何的弯曲度说崎岖（1.19）明显比平缓（1.10）粗。
    **两个判据互相矛盾时先怀疑量具** —— 拼弧就是那个坏掉的量具。

    正确做法：**每段连续弧单独量 D，再按弧长加权平均**。要求弧长 ≫ 量程上限（这里 ≥1200 对 ε≤186，
    比值 6.5），否则斜率被端点效应污染。
    """
    tr, ts = cKDTree(rugged_xy), cKDTree(smooth_xy)
    arcs: dict[str, list[np.ndarray]] = {"rugged": [], "smooth": []}
    for r in rings:
        if len(r) < 8:
            continue
        d_r, _ = tr.query(r)
        d_s, _ = ts.query(r)
        cls = np.full(len(r), "other", dtype=object)
        cls[(d_r <= radius) & (d_r < d_s)] = "rugged"
        cls[(d_s <= radius) & (d_s <= d_r)] = "smooth"
        start = 0
        for i in range(1, len(cls) + 1):
            if i == len(cls) or cls[i] != cls[start]:
                if cls[start] in arcs:
                    arcs[cls[start]].append(r[start:i])
                start = i

    out = {}
    for k, segs in arcs.items():
        Ds, Ws = [], []
        for seg in segs:
            if len(seg) < 8:
                continue
            arc = float(np.hypot(*np.diff(seg, axis=0).T).sum())
            if arc < min_len:
                continue
            rr = boxcount(seg, sizes)  # 开口折线，不要闭合成环
            if "error" in rr or rr["r2"] < 0.98:
                continue
            Ds.append(rr["D"])
            Ws.append(arc)
        if not Ds:
            out[k] = None
            continue
        Ds, Ws = np.array(Ds), np.array(Ws)
        out[k] = {
            "D": float((Ds * Ws).sum() / Ws.sum()),
            "D_min": float(Ds.min()),
            "D_max": float(Ds.max()),
            "arcs": int(len(Ds)),
            "arc_len": float(Ws.sum()),
            "min_len": min_len,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("coast", help="dump_coastlines.cjs 的产物")
    ap.add_argument("map_json", help="/api/novels/<id>/map 的产物（内含 locations 与 layout）")
    ap.add_argument("--window", type=float, default=300.0, help="弯曲度窗口（canvas 单位）")
    ap.add_argument("--step", type=float, default=40.0)
    args = ap.parse_args()

    mp = json.loads(Path(args.map_json).read_text())
    locs, layout = mp["locations"], mp["layout"]
    pos = {l["name"]: (l["x"], l["y"]) for l in layout}

    rugged, smooth = [], []
    for loc in locs:
        xy = pos.get(loc.get("name"))
        if not xy:
            continue
        k = classify(loc)
        (rugged if k == "rugged" else smooth if k == "smooth" else []).append(xy)
    rugged, smooth = np.array(rugged), np.array(smooth)
    print(f"地点分类：崎岖类 {len(rugged)}   平缓类 {len(smooth)}   （未分类 {len(locs) - len(rugged) - len(smooth)}）")
    if len(rugged) < 3 or len(smooth) < 3:
        sys.exit("两类地点太少，判据不成立")

    tr, ts = cKDTree(rugged), cKDTree(smooth)
    # 优先用后端的有序环（DOM 的 d 序列不能用来算有序量，见 `rings_from_map_json`）
    rings = rings_from_map_json(args.map_json)
    arcs = [float(np.hypot(*np.diff(np.vstack([r, r[:1]]), axis=0).T).sum()) for r in rings]
    print(f"海岸线拆出 {len(rings)} 圈，各圈弧长 " +
          ", ".join(f"{a:.0f}" for a in sorted(arcs, reverse=True)[:6]))
    prof = np.concatenate([sinuosity_along(r, args.window, args.step) for r in rings], 0) \
        if rings else np.zeros((0, 3))
    if not len(prof):
        sys.exit("弯曲度剖面为空")
    d_r, _ = tr.query(prof[:, :2])
    d_s, _ = ts.query(prof[:, :2])

    sin = prof[:, 2]
    print(f"\n弯曲度（窗口 {args.window:.0f} 单位，{len(sin)} 个采样）")
    q = np.percentile(sin, [10, 50, 90, 99])
    print(f"  p10 {q[0]:.3f}   p50 {q[1]:.3f}   p90 {q[2]:.3f}   p99 {q[3]:.3f}   均值 {sin.mean():.3f}")

    # 按"更靠近哪一类地点"分箱
    print(f"\n{'分组':<26}{'n':>6}{'弯曲度 中位':>12}{'p90':>8}")
    print('-' * 54)
    # 分组用**最近者归类**（无半径）。半径版是初版：它需要在地图之间重新调参
    # （fixture 的岸到锚点 p10 是 624，西游是 300 以内），**参数在替判据干活**就是坏味道。
    groups = [
        ("最近者是崎岖", d_r < d_s),
        ("最近者是平缓", d_s <= d_r),
    ]
    med = {}
    for name, m in groups:
        if m.sum() < 5:
            print(f"{name:<26}{m.sum():>6}{'样本太少':>12}")
            continue
        med[name] = float(np.median(sin[m]))
        print(f"{name:<26}{m.sum():>6}{med[name]:>12.3f}{np.percentile(sin[m], 90):>8.3f}")

    print("\n★ 判据：**崎岖组应显著高于平缓组**（这是地貌学常识，也是用户给的模型）。")
    a = med.get("最近者是崎岖")
    b = med.get("最近者是平缓")
    if a is not None and b is not None:
        print(f"   崎岖侧 {a:.3f}  平缓侧 {b:.3f}   差 {a - b:+.3f}")
        if a - b < 0.02:
            print("   ❌ 两者几乎重合 ⇒ 粗糙度是**均匀施加**的，与地貌无关。")
        elif a - b < 0.05:
            print("   △ 有分离但很弱。")
        else:
            print("   ✅ 分离明显，粗糙度跟随地貌。")
    print("\n参考区间（地貌学口径，弯曲度）：平直岸 1.00–1.03   岬湾岸 1.05–1.15   强锯齿 >1.20")


if __name__ == "__main__":
    main()
