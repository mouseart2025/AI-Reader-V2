"""SDF 调优 v2 — 让常见类型颜色更有区分度"""
import numpy as np
from PIL import Image
import random, time

random.seed(42)
BG = np.array([240, 228, 208], dtype=np.float64) / 255.0

# ─── 新配色：常见类型也有明显差异 ───
COLORS_V6 = {
    "realm":    np.array([210, 190, 145]) / 255.0,  # 金色调
    "kingdom":  np.array([175, 190, 140]) / 255.0,  # 橄榄绿
    "city":     np.array([195, 165, 125]) / 255.0,  # 赭石
    "town":     np.array([200, 185, 145]) / 255.0,  # 暖沙
    "org":      np.array([165, 145, 200]) / 255.0,  # 紫
    "mountain": np.array([155, 140, 110]) / 255.0,  # 深棕
    "hill":     np.array([180, 170, 130]) / 255.0,  # 黄土
    "forest":   np.array([120, 165, 100]) / 255.0,  # 绿
    "water":    np.array([110, 160, 200]) / 255.0,  # 蓝
    "ocean":    np.array([80, 135, 190]) / 255.0,    # 深蓝
    "desert":   np.array([210, 180, 115]) / 255.0,  # 沙金
    "valley":   np.array([155, 185, 130]) / 255.0,  # 浅绿
    "plain":    np.array([205, 195, 160]) / 255.0,  # 浅驼
}

# 修仙小说真实类型分布（大量 kingdom/city/org）
REAL_TYPE_WEIGHTS = {
    "kingdom": 25, "city": 20, "org": 15, "mountain": 10,
    "town": 8, "forest": 5, "water": 5, "ocean": 3,
    "desert": 3, "valley": 3, "realm": 2, "plain": 1,
}

def generate_realistic_locations(n=350):
    locs = []
    types = list(REAL_TYPE_WEIGHTS.keys())
    weights = list(REAL_TYPE_WEIGHTS.values())
    clusters = [(0.3, 0.4), (0.7, 0.3), (0.5, 0.65), (0.2, 0.65), (0.75, 0.6)]
    for i in range(n):
        cx, cy = random.choice(clusters)
        x = max(0.03, min(0.97, cx + random.gauss(0, 0.15)))
        y = max(0.03, min(0.97, cy + random.gauss(0, 0.12)))
        tier = random.choices([1,2,3,4,5], [5,15,40,25,15])[0]
        typ = random.choices(types, weights)[0]
        mentions = random.randint(1, 80)
        locs.append({"x": x, "y": y, "tier": tier, "type": typ, "mentions": mentions})
    return locs

def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)

def render(locs, w, h, colors, params):
    max_m = max(l["mentions"] for l in locs)
    centers, radii, cols = [], [], []
    for loc in locs:
        centers.append((loc["x"], 1 - loc["y"]))
        mr = loc["mentions"] / max(1, max_m)
        r = params["rb"] + mr * params["rm"] + (1.0/loc["tier"]) * params["rt"]
        radii.append(r)
        cols.append(colors.get(loc["type"], BG))

    u = np.linspace(0, 1, w); v = np.linspace(0, 1, h)
    uu, vv = np.meshgrid(u, v)
    total_field = np.zeros((h, w))
    max_field = np.zeros((h, w))
    dom_idx = np.zeros((h, w), dtype=np.int32)

    for i in range(len(locs)):
        dx = uu - centers[i][0]; dy = vv - centers[i][1]
        field = radii[i]**2 / (dx*dx + dy*dy + 0.001)
        total_field += field
        mask = field > max_field
        max_field = np.where(mask, field, max_field)
        dom_idx = np.where(mask, i, dom_idx)

    edge = smoothstep(params["slo"], params["shi"], total_field)
    noise = np.mod(np.sin(uu*50*12.9898 + vv*50*78.233)*43758.5453, 1.0)
    edge *= 1 - noise * 0.06

    img = np.zeros((h, w, 3))
    for c in range(3):
        cc = np.array([cols[idx][c] for idx in dom_idx.flat]).reshape(h, w)
        img[:,:,c] = BG[c]*(1 - edge*params["ms"]) + cc*edge*params["ms"]

    return np.clip(img*255, 0, 255).astype(np.uint8)

locs = generate_realistic_locations(350)
W, H = 800, 450
base = "/Users/leonfeng/Baiduyun/AISoul/AI-Reader-V2/scripts"

configs = {
    "v6_distinct": {"rb": 0.01, "rm": 0.03, "rt": 0.02, "slo": 1.0, "shi": 5.0, "ms": 0.85},
    "v7_bold":     {"rb": 0.015, "rm": 0.04, "rt": 0.025, "slo": 0.8, "shi": 3.0, "ms": 0.75},
    "v8_subtle":   {"rb": 0.012, "rm": 0.035, "rt": 0.02, "slo": 1.2, "shi": 6.0, "ms": 0.70},
    "v9_wide":     {"rb": 0.02, "rm": 0.05, "rt": 0.03, "slo": 0.5, "shi": 2.0, "ms": 0.60},
}

for name, p in configs.items():
    t0 = time.time()
    img = render(locs, W, H, COLORS_V6, p)
    path = f"{base}/sdf_test_{name}.png"
    Image.fromarray(img).save(path)
    print(f"[{name}] {(time.time()-t0)*1000:.0f}ms → {path}")
