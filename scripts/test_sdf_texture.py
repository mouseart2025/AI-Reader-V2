"""
SDF 地图底图纹理参数调优脚本
模拟 WebGL metaball shader，输出 PNG 供视觉检查。
"""
import numpy as np
from PIL import Image
import random
import time
import math

# ─── 模拟数据：类似凡人修仙传的地点分布 ───
random.seed(42)
TYPES = ["kingdom", "city", "mountain", "forest", "water", "ocean", "org", "desert", "town", "valley", "plain", "realm"]
TIER_WEIGHTS = {1: 0.05, 2: 0.15, 3: 0.40, 4: 0.25, 5: 0.15}

def generate_mock_locations(n=350):
    """生成模拟地点（归一化坐标 0-1）"""
    locs = []
    # 几个大陆级聚类中心
    clusters = [(0.3, 0.4), (0.7, 0.3), (0.5, 0.7), (0.2, 0.7), (0.8, 0.6)]
    for i in range(n):
        # 选一个聚类中心
        cx, cy = random.choice(clusters)
        # 加随机偏移
        x = cx + random.gauss(0, 0.15)
        y = cy + random.gauss(0, 0.12)
        x = max(0.02, min(0.98, x))
        y = max(0.02, min(0.98, y))

        tier = random.choices(list(TIER_WEIGHTS.keys()), list(TIER_WEIGHTS.values()))[0]
        typ = random.choice(TYPES)
        mentions = random.randint(1, 80)
        locs.append({"x": x, "y": y, "tier": tier, "type": typ, "mentions": mentions})
    return locs

# ─── 颜色配置 ───
BG = np.array([240, 228, 208], dtype=np.float64) / 255.0  # #f0e4d0

TERRAIN_COLORS = {
    "realm":    np.array([200, 180, 140]) / 255.0,
    "kingdom":  np.array([195, 175, 130]) / 255.0,
    "city":     np.array([190, 168, 120]) / 255.0,
    "town":     np.array([190, 168, 120]) / 255.0,
    "org":      np.array([165, 145, 200]) / 255.0,
    "mountain": np.array([160, 135, 100]) / 255.0,
    "hill":     np.array([175, 155, 115]) / 255.0,
    "forest":   np.array([120, 170, 95 ]) / 255.0,
    "water":    np.array([100, 155, 200]) / 255.0,
    "ocean":    np.array([75,  130, 195]) / 255.0,
    "desert":   np.array([200, 170, 100]) / 255.0,
    "valley":   np.array([150, 185, 120]) / 255.0,
    "plain":    np.array([200, 185, 150]) / 255.0,
}

# ─── 可调参数 ───
PARAMS = {
    "radius_base": 0.02,
    "radius_mention": 0.05,
    "radius_tier": 0.03,
    "smoothstep_lo": 0.4,
    "smoothstep_hi": 2.0,
    "mix_strength": 0.7,
    "noise_strength": 0.08,
    "use_dominant": True,  # True=最强贡献者着色, False=加权平均
}

def smoothstep(edge0, edge1, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0, 1)
    return t * t * (3 - 2 * t)

def render_sdf(locs, w=512, h=288, params=PARAMS):
    """CPU 模拟 SDF metaball shader"""
    t0 = time.time()

    max_mentions = max(l["mentions"] for l in locs)

    # 预计算中心、半径、颜色
    centers = []
    radii = []
    colors = []
    for loc in locs:
        centers.append((loc["x"], 1 - loc["y"]))  # flip Y like shader
        mr = loc["mentions"] / max(1, max_mentions)
        tier = loc["tier"]
        r = params["radius_base"] + mr * params["radius_mention"] + (1.0 / tier) * params["radius_tier"]
        radii.append(r)
        colors.append(TERRAIN_COLORS.get(loc["type"], BG))

    centers = np.array(centers)
    radii = np.array(radii)

    # 创建 UV 网格
    u = np.linspace(0, 1, w)
    v = np.linspace(0, 1, h)
    uu, vv = np.meshgrid(u, v)

    # 对每个像素计算 metaball 场
    total_field = np.zeros((h, w))

    if params["use_dominant"]:
        max_field = np.zeros((h, w))
        dominant_idx = np.zeros((h, w), dtype=np.int32)

    weighted_color = np.zeros((h, w, 3))
    total_weight = np.zeros((h, w))

    for i in range(len(locs)):
        cx, cy = centers[i]
        r = radii[i]
        dx = uu - cx
        dy = vv - cy
        d2 = dx * dx + dy * dy
        field = r * r / (d2 + 0.001)
        total_field += field

        if params["use_dominant"]:
            mask = field > max_field
            max_field = np.where(mask, field, max_field)
            dominant_idx = np.where(mask, i, dominant_idx)
        else:
            for c in range(3):
                weighted_color[:, :, c] += field * colors[i][c]
            total_weight += field

    # 着色
    edge = smoothstep(params["smoothstep_lo"], params["smoothstep_hi"], total_field)

    # 噪声
    noise_uv = uu * 50.0 * 12.9898 + vv * 50.0 * 78.233
    noise = np.mod(np.sin(noise_uv) * 43758.5453, 1.0)
    edge *= 1.0 - noise * params["noise_strength"]

    # 计算最终颜色
    img = np.zeros((h, w, 3))

    if params["use_dominant"]:
        # Voronoi 着色：用最强贡献者的颜色
        for c in range(3):
            color_channel = np.array([colors[idx][c] for idx in dominant_idx.flat]).reshape(h, w)
            img[:, :, c] = BG[c] * (1 - edge * params["mix_strength"]) + color_channel * edge * params["mix_strength"]
    else:
        # 加权平均着色
        mask = total_weight > 0
        for c in range(3):
            avg = np.where(mask, weighted_color[:, :, c] / np.where(mask, total_weight, 1), BG[c])
            img[:, :, c] = BG[c] * (1 - edge * params["mix_strength"]) + avg * edge * params["mix_strength"]

    img = np.clip(img * 255, 0, 255).astype(np.uint8)
    elapsed = time.time() - t0
    return img, elapsed

def test_params(name, **overrides):
    """测试一组参数"""
    params = {**PARAMS, **overrides}
    locs = generate_mock_locations(350)
    img, elapsed = render_sdf(locs, w=800, h=450, params=params)
    path = f"/Users/leonfeng/Baiduyun/AISoul/AI-Reader-V2/scripts/sdf_test_{name}.png"
    Image.fromarray(img).save(path)
    print(f"[{name}] {elapsed*1000:.0f}ms → {path}")
    return path

if __name__ == "__main__":
    locs = generate_mock_locations(350)

    # 当前参数（Voronoi 着色）
    test_params("v1_current")

    # 更高对比度
    test_params("v2_high_contrast",
        mix_strength=0.9,
        smoothstep_lo=0.6,
        smoothstep_hi=3.0,
        radius_base=0.015,
        radius_mention=0.04,
        radius_tier=0.025,
    )

    # 更小半径 + 更低阈值 = 只在聚集区着色
    test_params("v3_localized",
        mix_strength=0.85,
        smoothstep_lo=1.0,
        smoothstep_hi=5.0,
        radius_base=0.01,
        radius_mention=0.03,
        radius_tier=0.02,
    )

    # 加权平均模式（非 Voronoi）
    test_params("v4_weighted",
        use_dominant=False,
        mix_strength=0.8,
        smoothstep_lo=0.8,
        smoothstep_hi=4.0,
        radius_base=0.015,
        radius_mention=0.04,
        radius_tier=0.02,
    )

    # Voronoi + 大半径 + 高阈值（大区域着色）
    test_params("v5_voronoi_wide",
        mix_strength=0.6,
        smoothstep_lo=0.3,
        smoothstep_hi=1.5,
        radius_base=0.025,
        radius_mention=0.06,
        radius_tier=0.035,
    )

    print("\nDone! Compare the PNG files.")
