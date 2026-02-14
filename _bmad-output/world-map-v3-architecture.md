# AI-Reader-V2 世界地图 V3 架构设计：分层级真实地图体验

> 作者: Winston (Architect Agent)
> 日期: 2026-02-14
> 状态: 实现完成 — Phase 1-5 核心功能已全部落地
> 前置文档: world-map-v2-architecture.md

---

## 1. 问题陈述与目标

### 1.1 V2 遗留问题

V2 实现了多层地图（overworld + celestial + underworld + instance），但用户反馈暴露了根本性的体验问题：

1. **水平聚集**：约束求解器的叙事轴权重过高 (1.5)，当叙事轴为水平时 y 分量为零，所有地点挤在一条水平线上
2. **尺度不足**：1000×1000 画布承载 100+ 地点时过于拥挤，缺乏"大世界"的感觉
3. **层级缺失**：大洲、国家、城镇、洞府、建筑混在同一缩放级别，看不到宏观结构
4. **区域标签冗余**：大区域名字既显示为标签又显示为点，信息重复
5. **不像"地图"**：缺乏指北针、图标系统、缩放导航等地图基本元素

### 1.2 V3 目标体验

用户的核心期望是："**趋近于人类认知的世界地图**"。具体而言：

| 维度 | 目标 |
|------|------|
| 层级感 | 缩小看大洲/界/域，放大看城镇/建筑，像 Google Maps 的 LOD |
| 空间感 | AI 判断故事的空间尺度，给出合理的物理空间规划 |
| 图标化 | 市镇 🏘️、洞府 ⛰️、寺庙 🏛️、水域 🌊 等用合适图标 |
| 导航性 | 指北针、全貌按钮、缩放控件 |
| 多空间 | 主世界、副本、异界作为独立地图平面（V2 已有，需改进） |
| 区域感 | 大区域名称内嵌在区域多边形内，不再用点重复标记 |

---

## 2. 技术研究

### 2.1 MapLibre GL JS 能力评估

**结论：MapLibre GL JS 完全满足 V3 需求，无需更换技术栈。**

| 需求 | MapLibre 支持 | 实现方式 |
|------|-------------|---------|
| 缩放层级 | 0-24 级 zoom | 每个 layer 设置 `minzoom` / `maxzoom` |
| 自定义图标 | `map.addImage()` + SDF sprite | 运行时加载 SVG → sprite |
| 区域多边形 | GeoJSON fill + line 层 | V2 已实现矩形边界，需升级为 Voronoi/凸包 |
| 区域内嵌标签 | symbol 层 + `text-field` | 已实现，需按 zoom 调整 `text-size` |
| 指北针 | `NavigationControl({ showCompass: true })` | 一行代码 |
| 缩放到全貌 | `map.fitBounds()` | V2 已有 |
| 性能 | GeoJSON source + WebGL 渲染 | 100-500 个 feature 无压力 |

**关键 MapLibre 特性用于层级显示：**

```typescript
// 不同 zoom 显示不同层级的地点
map.addLayer({
  id: "locations-continent",
  type: "symbol",
  source: "locations",
  filter: ["==", ["get", "tier"], "continent"],
  minzoom: 6,
  maxzoom: 24,
  layout: {
    "icon-image": ["get", "icon"],
    "icon-size": ["interpolate", ["linear"], ["zoom"], 6, 0.8, 12, 1.5],
    "text-field": ["get", "name"],
    "text-size": ["interpolate", ["linear"], ["zoom"], 6, 14, 12, 20],
  },
})

map.addLayer({
  id: "locations-city",
  type: "symbol",
  source: "locations",
  filter: ["==", ["get", "tier"], "city"],
  minzoom: 9,  // 放大到一定程度才显示
  maxzoom: 24,
  layout: { ... },
})

map.addLayer({
  id: "locations-building",
  type: "symbol",
  source: "locations",
  filter: ["==", ["get", "tier"], "building"],
  minzoom: 11,  // 更大缩放才显示
  maxzoom: 24,
  layout: { ... },
})
```

**SVG 图标加载（运行时，无需预构建 sprite）：**

推荐使用 `maplibre-gl-svg` 插件或直接 `map.addImage()` + SVG 数据 URI：

```typescript
// 方案 A：maplibre-gl-svg 插件（推荐）
import { SvgManager } from "maplibre-gl-svg"
const svgManager = new SvgManager(map)
await svgManager.add("icon-city", "/icons/city.svg")
await svgManager.add("icon-cave", "/icons/cave.svg")

// 方案 B：手动 addImage
const img = await map.loadImage("/icons/city.png")
map.addImage("icon-city", img.data)
```

### 2.2 幻想地图生成算法研究

| 方案 | 来源 | 核心思路 | 适用性 |
|------|------|---------|--------|
| **Voronoi + Lloyd 松弛** | Red Blob Games | Poisson Disc 采样 → Voronoi 网格 → 区域多边形 | ★★★★ 适合生成自然区域边界 |
| **Azgaar 数据模型** | Azgaar FMG | Cell-based Voronoi → 叠加 state/province/culture 层 | ★★★ 层级思想可借鉴，但过于复杂 |
| **分层约束求解** | V2 + PlotMap | 区域级布局 → 区域内布局 → 副本独立布局 | ★★★★★ V2 已有基础，增强即可 |
| **力导向图 + 层级约束** | D3 force | 节点间力模拟，支持碰撞检测 | ★★★ 适合小规模，难控制区域约束 |
| **Treemap** | 通用 | 矩形递归划分 | ★★ 太"方正"，不像地图 |

**推荐方案：V2 的分层约束求解 + Voronoi 区域边界**

1. 保留 V2 的 `ConstraintSolver` 核心，修复水平聚集 bug
2. 用 Voronoi 生成区域边界多边形（替代矩形）
3. 增加层级感知：不同 tier 的地点用不同的画布空间

### 2.3 LLM 空间推理能力评估

研究表明 LLM 在简单空间关系（方向、包含）上表现良好，但在复杂多跳推理和精确几何上有局限。

**对本项目的影响：**

| 任务 | LLM 可靠性 | 策略 |
|------|-----------|------|
| 地点层级分类（洲→国→城→建筑） | ★★★★ | 高度可靠，用 enum 约束输出 |
| 空间尺度估计（大陆级/城市级/建筑级） | ★★★ | 给出粗粒度枚举，不要求精确数字 |
| 相对方位（A 在 B 东面） | ★★★★ | V2 已验证可行 |
| 副本/子空间识别 | ★★★★ | V2 已验证可行 |
| 物理距离估计 | ★★ | 不可靠，用距离等级替代 |
| 区域边界形状 | ★ | 不可行，由算法生成 |

### 2.4 图标系统研究

**可选图标来源：**

| 来源 | 授权 | 格式 | 风格 | 评估 |
|------|------|------|------|------|
| Public Domain Vectors | CC0 | SVG | 多种 | 免费但风格不统一 |
| Lucide Icons（已在项目中） | ISC | SVG | 线条 | ★★★★ 风格统一，但缺少奇幻专属图标 |
| 自制 SVG 集 | 自有 | SVG | 定制 | ★★★★★ 完全控制，但需要设计投入 |
| SDF 单色图标 | 运行时上色 | PNG/SVG | 可着色 | ★★★★ MapLibre SDF 支持运行时染色 |

**推荐方案：基于 Lucide + 少量自制 SVG 的混合方案**

利用 Lucide 已有的图标（Mountain, Castle, Building, Trees, Waves, Tent, etc.），缺少的奇幻特有图标（洞府、法阵、传送门）用简单 SVG 自制。所有图标转为 SDF 单色格式，支持按地点类型运行时上色。

---

## 3. 数据模型设计

### 3.1 地点层级模型（Tier 系统）

**核心增强：每个地点新增 `tier` 字段，控制缩放显示行为。**

```python
class LocationTier(str, Enum):
    """地点的空间层级，决定在地图上何时显示。"""
    WORLD = "world"           # 整个世界（如"三界"）— 仅作为容器
    CONTINENT = "continent"   # 大洲/大陆/界/域 — zoom 6+ 显示
    KINGDOM = "kingdom"       # 国/大区域 — zoom 7+ 显示
    REGION = "region"         # 郡/州/区/山脉/海域 — zoom 8+ 显示
    CITY = "city"             # 城/镇/村/寺庙/门派总部 — zoom 9+ 显示
    SITE = "site"             # 具体地点（客栈、桥、洞口）— zoom 10+ 显示
    BUILDING = "building"     # 建筑内部/房间 — zoom 11+ 显示
```

**层级 → 缩放映射：**

```
zoom 6-7:  显示 WORLD + CONTINENT 标签 + 区域多边形
zoom 7-8:  + KINGDOM 标签
zoom 8-9:  + REGION 标签，CONTINENT 标签淡化
zoom 9-10: + CITY 点位 + 图标
zoom 10-11: + SITE 点位
zoom 11+:  + BUILDING 点位
```

### 3.2 地点图标类型

```python
class LocationIcon(str, Enum):
    """地点的图标类型，决定地图上的视觉表现。"""
    # 聚落
    CAPITAL = "capital"        # 首都/都城 — 大圆 + 星标
    CITY = "city"              # 大城市 — 实心圆
    TOWN = "town"              # 城镇 — 空心圆
    VILLAGE = "village"        # 村庄 — 小点
    CAMP = "camp"              # 营地/临时聚落 — 帐篷

    # 自然
    MOUNTAIN = "mountain"      # 山/峰/岭/崖 — 三角山形
    FOREST = "forest"          # 林/森/丛 — 树木
    WATER = "water"            # 海/河/湖/泉/潭 — 波浪
    DESERT = "desert"          # 沙漠/荒原 — 沙丘
    ISLAND = "island"          # 岛屿 — 岛形

    # 建筑
    TEMPLE = "temple"          # 寺庙/道观/教堂 — 殿堂
    PALACE = "palace"          # 宫殿/府邸 — 城堡
    CAVE = "cave"              # 洞穴/洞府 — 洞口
    TOWER = "tower"            # 塔/阁 — 塔形
    GATE = "gate"              # 关隘/门 — 城门

    # 特殊
    PORTAL = "portal"          # 传送门/入口 — 旋涡
    RUINS = "ruins"            # 废墟/遗迹 — 碎石
    SACRED = "sacred"          # 神圣/法阵/祭坛 — 光环
    GENERIC = "generic"        # 通用 — 默认圆点
```

### 3.3 增强的 WorldStructure 模型

```python
class SpatialScale(str, Enum):
    """空间尺度 — AI 推断的故事世界物理大小。"""
    COSMIC = "cosmic"            # 多世界/宇宙级（仙侠/玄幻）
    CONTINENTAL = "continental"  # 大陆级（西游记、魔戒）
    NATIONAL = "national"        # 单国/多国级（红楼梦、水浒）
    URBAN = "urban"              # 城市级（都市小说）
    LOCAL = "local"              # 局部（单一建筑/区域）

class EnhancedLocation(BaseModel):
    """增强的地点信息，包含层级和图标。"""
    name: str
    tier: LocationTier                    # 新增：空间层级
    icon: LocationIcon = LocationIcon.GENERIC  # 新增：图标类型
    parent: str | None = None
    region: str | None = None            # 所属区域
    layer_id: str = "overworld"
    type: str = ""                       # 原有地点类型
    description: str = ""

class WorldStructureV3(WorldStructure):
    """V3 增强的世界结构。"""
    spatial_scale: SpatialScale = SpatialScale.CONTINENTAL
    location_tiers: dict[str, str] = {}   # name → tier
    location_icons: dict[str, str] = {}   # name → icon
    # V2 字段保持不变
```

### 3.4 数据库 Schema 增强

```sql
-- 在 world_structures 表中增加 V3 字段（向后兼容）
-- structure_json 中的 WorldStructure 自然包含新字段

-- 地点层级和图标缓存（可选，避免每次从 WorldStructure 解析）
CREATE TABLE IF NOT EXISTS location_metadata (
    novel_id    TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    tier        TEXT NOT NULL DEFAULT 'city',
    icon        TEXT NOT NULL DEFAULT 'generic',
    PRIMARY KEY (novel_id, name)
);
```

---

## 4. AI 空间规划设计

### 4.1 层级分类 Prompt

在现有 `WorldStructureAgent.process_chapter()` 中增加操作类型 `SET_TIER` 和 `SET_ICON`：

```
## 你的额外任务

对于本章出现的每个地点，判断其空间层级(tier)和图标类型(icon)。

### 空间层级规则
- world: 整个世界的总称（如"三界"、"天下"）
- continent: 大洲、大陆、界、域级别（如"东胜神洲"、"南赡部洲"）
- kingdom: 国家、王国级别（如"傲来国"、"乌鸡国"）
- region: 区域、山脉、海域级别（如"花果山"、"东洋大海"）
- city: 城镇、寺庙、门派总部级别（如"长安城"、"白虎岭"）
- site: 具体地点（如"水帘洞口"、"通天河渡口"）
- building: 建筑内部（如"铁板桥"、"金銮殿"）

### 图标类型规则
根据地点描述和名称关键词：
- 城/镇/都 → city/town; 村/寨/庄 → village; 营/帐 → camp
- 山/峰/岭/崖 → mountain; 林/森 → forest; 海/河/湖/泉 → water
- 寺/庙/观/庵 → temple; 宫/殿/府 → palace; 洞/穴 → cave
- 塔/阁/楼 → tower; 关/隘/门 → gate
- 传送/入口 → portal; 废墟/遗迹 → ruins
- 其他 → generic

### 输出格式
在 operations 中使用：
{"op": "SET_TIER", "data": {"location": "花果山", "tier": "region"}}
{"op": "SET_ICON", "data": {"location": "花果山", "icon": "mountain"}}
```

### 4.2 空间尺度推断

在分析的**前 3 章**完成后，增加一次专门的空间尺度推断调用：

```
你是小说世界观分析专家。根据以下前几章的世界观信息，判断这部小说的空间尺度。

## 已知世界结构
{world_structure_summary}

## 前几章出现的地点
{locations_summary}

## 空间尺度选项
- cosmic: 多世界/宇宙/多界（有仙界/魔界/人界之分）
- continental: 大陆级（有多个大洲或大陆，跨越千里的旅程）
- national: 国家级（故事在一两个国家内展开）
- urban: 城市级（主要发生在一个城市内）
- local: 局部（一个建筑群/学校/小区域）

请输出：
{"spatial_scale": "continental", "reasoning": "..."}
```

空间尺度影响地图的初始缩放级别和画布大小：

| 尺度 | 画布大小 | 初始 zoom | 区域间距 |
|------|---------|-----------|---------|
| cosmic | 5000 | 5 | 极大 |
| continental | 3000 | 6 | 大 |
| national | 2000 | 7 | 中 |
| urban | 1000 | 9 | 小 |
| local | 500 | 11 | 极小 |

### 4.3 多空间平面规划

V2 已有 MapLayer 概念。V3 增强：让 AI 在空间尺度推断时同时识别独立空间平面：

```
## 额外任务：识别独立空间平面

有些地点不在地理主世界中，而是独立的空间平面。例如：
- 天界/仙界：与地面不在同一物理空间
- 冥界/地府：地下独立空间
- 海底宫殿：水下独立空间
- 洞府/副本：从某个入口进入的独立空间
- 梦境/幻境：临时存在的虚拟空间

对于每个独立空间，指出：
1. 空间名称和类型
2. 入口位置（在主世界的哪个地点）
3. 该空间的内部尺度（building/local/urban）
```

---

## 5. 布局算法设计

### 5.1 多尺度画布

替代 V2 的固定 1000×1000 画布，使用基于空间尺度的动态画布：

```python
CANVAS_SIZES = {
    "cosmic": 5000,
    "continental": 3000,
    "national": 2000,
    "urban": 1000,
    "local": 500,
}
```

### 5.2 分层布局策略（改进 V2）

```
Step 0: 确定空间尺度 → 画布大小
Step 1: 区域级布局
  — 使用 Voronoi + 方位约束布局大区域
  — 每个区域分配一个 Voronoi 多边形（替代 V2 的矩形）
Step 2: 区域内分层布局
  — 对每个区域内的地点，按 tier 分组
  — KINGDOM/REGION tier 先布局（作为锚点）
  — CITY/SITE/BUILDING tier 围绕锚点分布
Step 3: 副本层独立布局（V2 已有，保持不变）
Step 4: 传送门标注（V2 已有，保持不变）
```

### 5.3 Voronoi 区域边界生成

替代 V2 的矩形边界，用 Voronoi 生成更自然的区域多边形：

```python
from scipy.spatial import Voronoi
import numpy as np

def generate_region_boundaries(
    region_centers: dict[str, tuple[float, float]],
    canvas_size: int,
) -> dict[str, list[tuple[float, float]]]:
    """
    从区域中心点生成 Voronoi 多边形边界。

    Args:
        region_centers: 区域名 → (cx, cy) 中心坐标
        canvas_size: 画布大小

    Returns:
        区域名 → 多边形顶点列表
    """
    names = list(region_centers.keys())
    points = np.array([region_centers[n] for n in names])

    # 添加远距离镜像点以确保边缘区域有闭合边界
    mirrored = []
    for p in points:
        mirrored.extend([
            [-p[0], p[1]],
            [2 * canvas_size - p[0], p[1]],
            [p[0], -p[1]],
            [p[0], 2 * canvas_size - p[1]],
        ])
    all_points = np.vstack([points, np.array(mirrored)])

    vor = Voronoi(all_points)

    result = {}
    for i, name in enumerate(names):
        region_idx = vor.point_region[i]
        region = vor.regions[region_idx]
        if -1 in region:
            continue
        vertices = [tuple(vor.vertices[v]) for v in region]
        # 裁剪到画布范围
        vertices = _clip_polygon_to_canvas(vertices, canvas_size)
        result[name] = vertices

    return result
```

### 5.4 修复水平聚集问题

在 V3 中同时修复 V2 的布局 bug：

```python
# 修复 1: 降低叙事轴权重
NARRATIVE_AXIS_WEIGHT = 0.4  # V2: 1.5 → V3: 0.4

# 修复 2: 水平轴退化处理
def _e_narrative_axis(self, coords):
    ax, ay = self._narrative_axis
    # 当轴接近水平时(|ay|<0.1)，添加垂直分散项
    if abs(ay) < 0.1:
        # 给 y 坐标添加基于 tier/chapter 的正弦偏移
        for i, name in enumerate(self.loc_names):
            ch = self.first_chapter.get(name, 0)
            if ch <= 0: continue
            # 期望 y 位置有正弦变化，避免所有点在同一水平线
            expected_y = 0.5 + 0.3 * math.sin(ch * 0.2)
            actual_y = (coords[i, 1] - self._canvas_min_y) / (self._canvas_max_y - self._canvas_min_y)
            penalty += (actual_y - expected_y) ** 2
    ...

# 修复 3: 增大反重叠间距
MIN_SPACING = 50  # V2: 25 → V3: 50
```

---

## 6. 前端渲染设计

### 6.1 多层级地图层 (LOD - Level of Detail)

```typescript
// 地图层定义：不同 tier 在不同 zoom 显示
const TIER_ZOOM_RANGES: Record<string, [number, number]> = {
  continent: [6, 24],
  kingdom: [7, 24],
  region: [8, 24],
  city: [9, 24],
  site: [10, 24],
  building: [11, 24],
}

// 不同 tier 的文本大小
const TIER_TEXT_SIZES: Record<string, number[]> = {
  continent: [18, 24],  // [base, max]
  kingdom: [14, 18],
  region: [12, 16],
  city: [10, 14],
  site: [9, 12],
  building: [8, 10],
}
```

**实现方式：每个 tier 创建一组独立的 MapLibre layer（circle + symbol + label）：**

```typescript
for (const [tier, [minZoom, maxZoom]] of Object.entries(TIER_ZOOM_RANGES)) {
  // 图标层
  map.addLayer({
    id: `loc-icon-${tier}`,
    type: "symbol",
    source: "locations",
    filter: ["==", ["get", "tier"], tier],
    minzoom: minZoom,
    maxzoom: maxZoom,
    layout: {
      "icon-image": ["get", "icon"],
      "icon-size": ["interpolate", ["linear"], ["zoom"],
        minZoom, 0.6,
        minZoom + 3, 1.0,
      ],
      "icon-allow-overlap": tier === "continent" || tier === "kingdom",
    },
    paint: {
      "icon-opacity": ["get", "opacity"],
    },
  })

  // 标签层
  map.addLayer({
    id: `loc-label-${tier}`,
    type: "symbol",
    source: "locations",
    filter: ["==", ["get", "tier"], tier],
    minzoom: minZoom,
    maxzoom: maxZoom,
    layout: {
      "text-field": ["get", "name"],
      "text-size": ["interpolate", ["linear"], ["zoom"],
        minZoom, TIER_TEXT_SIZES[tier][0],
        minZoom + 4, TIER_TEXT_SIZES[tier][1],
      ],
      "text-offset": [0, 1.5],
      "text-anchor": "top",
      "text-allow-overlap": tier === "continent",
      "text-optional": tier !== "continent",
    },
    paint: {
      "text-color": ["get", "textColor"],
      "text-halo-color": ["get", "haloColor"],
      "text-halo-width": 1.5,
      "text-opacity": ["get", "opacity"],
    },
  })
}
```

### 6.2 区域多边形渲染

V2 用矩形，V3 升级为 Voronoi 多边形：

```typescript
// 区域边界：Voronoi 多边形
map.addLayer({
  id: "region-fills",
  type: "fill",
  source: "regions",
  minzoom: 6,
  maxzoom: 11,  // 放大后淡出
  paint: {
    "fill-color": ["get", "color"],
    "fill-opacity": ["interpolate", ["linear"], ["zoom"],
      6, 0.12,
      10, 0.04,
    ],
  },
})

map.addLayer({
  id: "region-borders",
  type: "line",
  source: "regions",
  minzoom: 6,
  maxzoom: 12,
  paint: {
    "line-color": ["get", "color"],
    "line-opacity": ["interpolate", ["linear"], ["zoom"],
      6, 0.4,
      11, 0.15,
    ],
    "line-width": 2,
    "line-dasharray": [6, 4],
  },
})

// 区域名称内嵌标签（大字体、低透明度）
map.addLayer({
  id: "region-labels",
  type: "symbol",
  source: "region-labels",
  minzoom: 6,
  maxzoom: 10,  // 放大后隐藏
  layout: {
    "text-field": ["get", "name"],
    "text-size": ["interpolate", ["linear"], ["zoom"], 6, 16, 9, 28],
    "text-allow-overlap": true,
    "text-ignore-placement": true,  // 不遮挡其他标签
  },
  paint: {
    "text-color": ["get", "color"],
    "text-opacity": ["interpolate", ["linear"], ["zoom"],
      6, 0.5,
      9, 0.2,
    ],
  },
})
```

**关键设计：区域标签只在 zoom 6-10 显示，放大后自然消失，不与地点标签冲突。**

### 6.3 指北针与导航控件

```typescript
// 启用指北针
map.addControl(
  new maplibregl.NavigationControl({
    showCompass: true,    // V2 是 false，V3 改为 true
    showZoom: true,
    visualizePitch: true,
  }),
  "top-right",
)

// 添加"全貌"按钮
class FitAllControl {
  onAdd(map: maplibregl.Map) {
    const btn = document.createElement("button")
    btn.className = "maplibregl-ctrl-icon"
    btn.title = "查看全貌 (Home)"
    btn.innerHTML = `<svg>...</svg>`  // 地球/全貌图标
    btn.onclick = () => map.fitBounds(worldBounds, { padding: 40 })

    const container = document.createElement("div")
    container.className = "maplibregl-ctrl maplibregl-ctrl-group"
    container.appendChild(btn)
    return container
  }
}

map.addControl(new FitAllControl(), "top-right")
```

### 6.4 图标系统实现

**SVG 图标集（约 20 个）：**

```
/public/map-icons/
  ├── capital.svg       # 首都 — 星形标记
  ├── city.svg          # 城市 — 实心圆
  ├── town.svg          # 城镇 — 空心圆
  ├── village.svg       # 村庄 — 小点
  ├── camp.svg          # 营地 — 帐篷
  ├── mountain.svg      # 山峰 — 三角形
  ├── forest.svg        # 森林 — 树木
  ├── water.svg         # 水域 — 波浪
  ├── desert.svg        # 沙漠 — 沙丘
  ├── island.svg        # 岛屿 — 环形
  ├── temple.svg        # 寺庙 — 殿堂
  ├── palace.svg        # 宫殿 — 城堡
  ├── cave.svg          # 洞穴 — 拱门
  ├── tower.svg         # 高塔 — 尖塔
  ├── gate.svg          # 关隘 — 城门
  ├── portal.svg        # 传送门 — 旋涡
  ├── ruins.svg         # 废墟 — 碎石
  ├── sacred.svg        # 圣地 — 光环
  └── generic.svg       # 通用 — 默认点
```

所有图标设计为 **24×24 单色 SVG**，支持 SDF 运行时上色。颜色由地点类型决定（人文=蓝、自然=绿、特殊=金），与项目整体配色一致。

**图标加载策略：**

```typescript
const ICON_NAMES = [
  "capital", "city", "town", "village", "camp",
  "mountain", "forest", "water", "desert", "island",
  "temple", "palace", "cave", "tower", "gate",
  "portal", "ruins", "sacred", "generic",
]

async function loadMapIcons(map: maplibregl.Map) {
  for (const name of ICON_NAMES) {
    const img = await map.loadImage(`/map-icons/${name}.svg`)
    map.addImage(`icon-${name}`, img.data, { sdf: true })
  }
}
```

### 6.5 画布坐标映射增强

V2 使用 2° × 2° 地理范围，V3 根据空间尺度动态调整：

```typescript
function getExtent(canvasSize: number): number {
  // 更大的画布 → 更大的地理范围 → 更多缩放级别可用
  if (canvasSize >= 5000) return 10.0  // cosmic
  if (canvasSize >= 3000) return 6.0   // continental
  if (canvasSize >= 2000) return 4.0   // national
  return 2.0                           // urban/local
}
```

---

## 7. API 设计

### 7.1 增强的地图数据 API

```
GET /api/novels/{id}/map?layer_id=overworld&chapter_start=1&chapter_end=100
```

V3 响应新增字段：

```typescript
interface MapDataV3 extends MapData {
  // 新增
  spatial_scale: SpatialScale     // 空间尺度
  canvas_size: number             // 动态画布大小

  // 增强 location 数据
  locations: MapLocationV3[]      // 包含 tier + icon

  // 增强 region 边界
  region_boundaries: RegionBoundaryV3[]  // Voronoi 多边形
}

interface MapLocationV3 extends MapLocation {
  tier: LocationTier              // 空间层级
  icon: LocationIcon              // 图标类型
}

interface RegionBoundaryV3 {
  region_name: string
  color: string
  // V2: bounds (矩形)
  // V3: polygon (多边形顶点)
  polygon: [number, number][]     // Voronoi 多边形顶点
  center: [number, number]        // 标签位置
}
```

### 7.2 层级元数据 API（可选）

```
GET /api/novels/{id}/map/tiers
```

返回各 tier 的地点数量，供前端显示缩放导航提示：

```json
{
  "spatial_scale": "continental",
  "tiers": {
    "continent": 4,
    "kingdom": 12,
    "region": 35,
    "city": 89,
    "site": 120,
    "building": 15
  }
}
```

---

## 8. 实施路径

### Phase 1: 修复 V2 布局问题 + 基础层级 ✅

**目标**：修复水平聚集 bug + 添加指北针 + 增大画布

- [x] 降低 `NARRATIVE_AXIS_WEIGHT` 从 1.5 到 0.4
- [x] 添加水平轴退化处理（垂直分散项）
- [x] 增大 `MIN_SPACING` 从 25 到 50
- [x] 修复 `_interpolate_on_axis` 的固定 y 问题
- [x] 启用 NavigationControl 的指北针
- [x] 添加"全貌"按钮

**验证**：西游记地图不再水平聚集，有合理的纵向分布。

### Phase 2: Tier 系统 + 缩放层级 ✅

**目标**：实现地点分层显示

- [x] 新增 `LocationTier` 和 `LocationIcon` 枚举
- [x] 增强 `WorldStructureAgent` 的 prompt，输出 tier 和 icon
- [x] 在 `world_structure_agent.py` 中实现 `SET_TIER` / `SET_ICON` 操作
- [x] 修改 `map_layout_service.py`：动态画布大小 + tier 感知布局
- [x] 前端：创建多层 MapLibre layer（per-tier）
- [x] 前端：实现 zoom-based 显示/隐藏

**验证**：缩小看到四大部洲标签，放大看到城镇点位。

### Phase 3: Voronoi 区域边界 + 图标系统 ✅

**目标**：视觉升级

- [x] 实现 Voronoi 区域边界生成（替代矩形）
- [x] 设计并制作 19 个 SVG 地图图标
- [x] 实现 SDF 图标加载和运行时上色
- [x] 区域标签内嵌（大字体、低透明度、zoom 6-10 显示）
- [x] 所有 tier 使用 symbol layer（icon + label 合一）

**验证**：地图看起来像一张真正的幻想地图。

### Phase 4: 空间尺度推断 + 多尺度画布 ✅

**目标**：AI 驱动的空间规划

- [x] 实现空间尺度推断（`_detect_spatial_scale` 启发式 + genre hint）
- [x] 动态画布大小（500-5000 based on scale）
- [x] 调整前端坐标映射的地理范围（`getExtentDeg` 按 canvasSize）
- [x] 不同尺度的默认 zoom 级别（cosmic=5 → local=11）

### Phase 5: 增强与打磨 ✅

**目标**：细节完善

- [x] 缩放级别指示器（显示当前可见 tier）
- [x] 快捷键导航（Home=全貌, +/- 缩放）
- [x] 地图图例（可折叠，仅显示数据中出现的图标）
- [ ] 区域边界手绘风格化（可选，低优先级）
- [ ] 性能优化：大量地点时的渲染策略（暂未需要，当前 100-500 地点无性能问题）

---

## 9. 关键技术决策

### 决策 1: 是否需要替换 MapLibre？

**否**。MapLibre GL JS 完全满足所有 V3 需求：
- 支持 layer-level `minzoom`/`maxzoom` 控制 LOD
- 支持 `addImage()` + SDF 运行时上色
- 支持 GeoJSON polygon 区域边界
- 内置 NavigationControl 指北针
- WebGL 渲染性能足以处理 500+ 个 feature

### 决策 2: Tier 分类由 AI 做还是规则做？

**混合方案**：
- 规则优先：根据名称关键词（洲/国/城/村/洞/殿/桥/阁）自动分类
- AI 补充：规则无法判断时由 LLM 分类
- 用户覆盖：用户可手动调整 tier

### 决策 3: 区域边界用 Voronoi 还是凸包？

**Voronoi**。理由：
- 覆盖整个画布（凸包只包裹已有点，留空白）
- 边界更自然
- `scipy.spatial.Voronoi` 已在项目依赖中

### 决策 4: 图标用 SDF 单色还是彩色 SVG？

**SDF 单色**。理由：
- 支持按地点类型/状态运行时上色
- 文件更小，渲染更快
- 与 MapLibre 的 `icon-color` 表达式无缝配合
- 所有图标风格统一

### 决策 5: 画布大小是动态还是固定？

**动态**（基于空间尺度）。理由：
- 不同类型小说的地理范围差异巨大
- 固定 1000×1000 对大陆级小说太小
- 画布大小影响 Voronoi 质量和地点间距

---

## 10. 与 V2 的兼容性

### 渐进升级策略

```
V2 (当前): 多层地图 + 矩形区域 + 圆点标记
     ↓  Phase 1 (修 bug)
V2.1: 布局修复 + 指北针 + 全貌按钮
     ↓  Phase 2 (层级)
V2.5: tier 分层显示 + 动态画布
     ↓  Phase 3 (视觉)
V3.0: Voronoi 边界 + 图标系统 + 区域内嵌标签
     ↓  Phase 4 (AI)
V3.5: 空间尺度推断 + 多尺度画布
```

### 向后兼容

- `WorldStructure` 新增字段均有默认值，旧数据不受影响
- API 响应中新字段为可选，前端可渐进适配
- 未经 tier 分类的地点默认为 `city` tier + `generic` icon
- Phase 1 可独立发布，无需等待完整 V3

---

## 11. 开放问题

1. **图标设计投入**：20 个 SVG 图标需要设计工作。是否可以先用 Lucide 现有图标作为占位，后续替换？

2. **Voronoi 边界美观度**：算法生成的 Voronoi 边界可能不够"有机"。是否需要 simplex noise 扰动让边界更自然？（类似 V2 terrain 生成的噪声扰动思路）

3. **Tier 自动分类准确率**：关键词规则覆盖面估计 70-80%，剩余需要 LLM 判断。LLM 分类是否要缓存到 `location_metadata` 表避免重复调用？

4. **重新分析时的行为**：用户重新分析后，tier/icon 信息是否需要重新推断？还是保留用户手动调整？

5. **性能边界**：5000×5000 画布 + 300+ 个 Voronoi seed 的计算量如何？需要用 Web Worker 吗？

---

## 参考资料

- [MapLibre Style Spec - Layers](https://maplibre.org/maplibre-style-spec/layers/) — minzoom/maxzoom/symbol 配置
- [MapLibre GL SVG Plugin](https://github.com/rbrundritt/maplibre-gl-svg) — SVG 图标运行时加载
- [Red Blob Games - Polygonal Map Generation](http://www-cs-students.stanford.edu/~amitp/game-programming/polygon-map-generation/) — Voronoi + Lloyd 松弛算法
- [Azgaar FMG Data Model](https://github.com/Azgaar/Fantasy-Map-Generator/wiki/Data-model) — Cell-based 层级数据模型
- [MapLibre NavigationControl](https://maplibre.org/maplibre-gl-js/docs/API/classes/NavigationControl/) — 指北针配置
- [maplibre-gl-compass](https://github.com/qazsato/maplibre-gl-compass) — 指北针增强插件

---

*本文档为 V3 架构草案 v1，请审阅后反馈意见。*
