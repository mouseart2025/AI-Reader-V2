# Epic N31: 地图可视化升级 — 研究报告对照代码库实况的诚实评估

Status: analysis

---

## A. 现状诚实评估

### A.1 已经做得好的部分

读完代码后，必须承认系统在若干维度已经达到了相当成熟的水平：

**1. 约束求解器 (ConstraintSolver) — 工业级**
- `map_layout_service.py` 2751 行，`ConstraintSolver` 类从 L1318 起，包含完整的 force-directed seeding + scipy `differential_evolution` 全局优化
- 支持天体/冥界地点分离、方向提示、用户手动 override 锁定、canvas 区域边界约束
- 有真正的能量函数（距离约束 + 包含约束 + 方向约束 + 排斥力），这正是研究报告建议的 CSP 路径 — **系统已经实现了**
- 层级布局 (`_hierarchy_layout`) 作为 DE 的 seed population，非常聪明的工程设计

**2. 标签碰撞检测 — 已达到 Mapbox 级别**
- `computeLabelLayout()` (NovelMap.tsx L103-175) 实现了完整的 8 锚点候选 + Grid 空间索引碰撞检测
- 按 `TIER_WEIGHT` 优先级排序，优先放置高层级地点
- N30.1 story 设计的 multi-anchor 系统已完整落地在代码中
- N30.2 的模拟退火导出标签优化也已规划

**3. 地形生成 — 完整的 Whittaker pipeline**
- `generate_terrain()` (L2280-) 实现了: OpenSimplex 双八度噪声 → 海拔场 + 湿度场 → Whittaker 5x5 矩阵双线性插值 → Gaussian 模糊 → PNG 输出
- 地形受地点类型影响（山脉升高海拔、水系降低海拔/升高湿度）
- Lloyd 松弛 (L2256) 对 Voronoi 种子点做 2 轮质心迭代
- 这已经是研究报告第三部分建议的 PCG 方案的**直接实现** — 系统已做到了

**4. 河流水系 — 梯度下降已实现**
- `generate_rivers()` (L2650) + `_trace_river()`: 从山脉附近出发，沿海拔梯度下降，OpenSimplex 横向扰动产生蜿蜒效果
- 3-8 条河流，宽度随长度递增 (1.5-5.0px)
- 前端 SVG 贝塞尔曲线渲染，深/浅色主题颜色适配

**5. 领地可视化 — Convex hull + 手绘扭曲**
- `hullTerritoryGenerator.ts`: 层级化凸包，父节点包裹所有后代坐标 + tier-dependent padding
- `edgeDistortion.ts`: 基于 hash-noise 的多边形边缘扰动，产生手绘感
- 4 层 nesting 深度，每层独立的 stroke/fill opacity + dash pattern
- 曲线文字 (`<textPath>` 二次贝塞尔) 沿领地弧线排列

**6. 地形装饰符号 — 完整语义纹理层**
- `terrainHints.ts` (365 行): 5 类地形 (mountain/water/forest/desert/cave)，每类 2-3 个 SVG 符号变体
- Tier-dependent 密度/尺寸/扩散半径，golden-angle-like 向日葵分布避免成圈
- 碰撞过滤 (距地点中心 18px 排斥)，MAX_HINTS=900 全局上限

**7. 轨迹动画 — 双路径 + 脉冲播放**
- 背景全轨迹 (dashed, 低透明度) + 前景可见段 (solid)
- 播放模式带脉冲外圈动画 + 自动平移跟随
- 路径点停留时长圆圈缩放 + 章节标注

### A.2 架构硬限制 — 必须面对的天花板

**1. SVG 2373 行单体组件 — 维护性极限**
- `NovelMap.tsx` 2373 行，包含 20+ 个 `useEffect` hook，每个管理一个 SVG 图层
- D3 命令式操作（`selectAll().remove()` + 重新 append）与 React 声明式模型冲突
- 任何新图层/交互都必须插入这个巨型文件 — 技术债加速堆积

**2. SVG 性能边界 — 760 节点已经是极限**
- 红楼梦 760 个地点 × (hit-area circle + icon group + label text + overview dot) ≈ 3000+ SVG 元素
- 加上 territories, rivers, terrain hints (最多 900 个 `<use>`) ≈ 总计 5000-6000 DOM 节点
- 每次 zoom/pan 时 D3 transform 整个 viewport group — 浏览器 compositing 勉强应付
- **N29 的两阶段过滤 (mention filter + tier collapse) 本质上是绕过 SVG 性能限制的 workaround**

**3. 后端 terrain PNG — 静态瓦片无法交互**
- `generate_terrain()` 输出单张 1024px PNG，前端作为 `<image>` 以 40% opacity 叠加
- 无法点击特定 biome 区域、无法高亮、无法动态修改
- 地形与地点布局耦合在后端 — 前端无法独立调整地形样式

**4. 领地边界 — 凸包几何的固有缺陷**
- 凸包无法表达凹形领地（如环形城市围绕湖泊）
- 深层 nesting 时子领地的凸包常溢出父领地边界 — 没有互斥约束
- 与研究报告推荐的 MapSets (非交叉生成树 + 膨胀) 相比，凸包在"领土不重叠"保证上完全空白

**5. 缺乏真正的语义缩放 (Semantic Zoom)**
- 当前的 tier visibility (`TIER_MIN_SCALE`) 是 CSS 可见性开关，不是语义级数据过滤
- 标签碰撞检测在每个 zoom 级别都对所有可见节点运行 — 没有分层 LOD
- 没有 zoom-dependent 信息密度控制（放大后应显示更多细节文字）

### A.3 用户看到的 vs 代码实际产出

| 用户感知 | 代码实况 |
|----------|----------|
| "散布在空白画布上的点" | 有 terrain PNG 底图 (opacity 40%)、有 terrain hints、有 territories — 但 N30.6 发现 **参数过于保守**，视觉效果像没有一样 |
| "河流在哪？" | `generate_rivers()` 完整实现且前端渲染 — 但 N30.6 发现 river 仅在 constraint mode 生效，width 最大 3px，opacity 0.6，几乎不可见 |
| "领地色块看不到" | `FILL_OP` 最高 0.15 (light) / 0.22 (dark) — 在 N30.6 之前最高仅 0.05，肉眼不可辨 |
| "标签全叠在一起" | 8 锚点碰撞检测已实现 — 但只能消除 ~60-70% 碰撞，极密集区 (大观园 20+ 建筑) 仍然溢出 |
| "想要 Inkarnate 那种效果" | 当前是**算法驱动的制图引擎**，不是**像素级美术引擎**。差距不是代码 bug，是设计范式不同 |

**核心洞察**: 系统的**基础设施已经到位** — CSP 求解器、Whittaker biome、河流、领地、标签碰撞都有了。视觉效果差的主要原因不是功能缺失，而是 **(1) 渲染参数保守 (2) 图层整合欠打磨 (3) SVG 表现力天花板**。

---

## B. ROI 分析：影响 vs 投入矩阵

### B.1 SpRL (Spatial Role Labeling) — 空间角色标注

| 维度 | 评估 |
|------|------|
| 视觉冲击 | **1/5** — 这是后端数据质量改进，用户在地图上看不到直接变化 |
| 工程投入 | **15-25 人天** — 需重写 extraction prompt schema、增加 Trajector/Landmark/SI/Path/Direction/Distance 6 种角色、修改 ChapterFact 数据模型、重新训练/调整 few-shot examples、更新所有下游消费者 (aggregator, validator, visualization_service) |
| 风险等级 | **高** — ChapterFact 是系统核心数据模型，任何 schema 变更影响全链路；LLM 在 zero-shot 下对 SpRL 标注质量未知（学术 benchmark 用的是 fine-tuned 模型）；已有的 `spatial_relationships` 字段已包含 direction 和 relation_type |
| 依赖项 | 需要先有 benchmark 验证 LLM 对 SpRL schema 的遵从度；需要 migration 脚本处理已有数据 |
| 当前系统已有 | `ChapterFact` 已有 `spatial_relationships[].relation_type` (contains/near/direction)、`locations[].parent`、`locations[].role` (setting/referenced/boundary)。ConstraintSolver 已消费方向约束。**SpRL 的核心价值已经被当前 schema 覆盖了约 70%** |

**结论**: **不建议在 N31 引入**。投入产出比最差的一项。当前的 spatial_relationships 已足够。真正缺少的是提取质量，而非 schema 丰富度 — 提升提取质量应通过 prompt engineering 和 few-shot 调优，不是改数据模型。

### B.2 AntV G6 Combos + 混合布局

| 维度 | 评估 |
|------|------|
| 视觉冲击 | **3/5** — Combo 嵌套的 expand/collapse 交互确实比当前的 tier visibility 更直观，但对"地图美观度"贡献有限 |
| 工程投入 | **30-50 人天** — 完整替换 D3 SVG 渲染层 → AntV G6，需要: 移植 20+ useEffect 到 G6 plugin 体系、重写所有自定义渲染 (icon SVGs, terrain hints, territory hulls, rivers, trajectories, conflict markers)、学习 G6 API 并适配 React、处理 G6 的布局引擎与现有 ConstraintSolver 的关系 |
| 风险等级 | **极高** — 这是**重写 NovelMap 的整个前端渲染层**。G6 的 Combo 节点需要预定义层级结构，当前系统的层级是动态的 (WorldStructure 可编辑)；G6 的内置布局算法 (dagre, force) 与当前后端 ConstraintSolver 不兼容 — 需要用 G6 的 custom layout 桥接后端坐标，增加大量胶水代码 |
| 依赖项 | 需要先确认 G6 v5 对自定义 SVG icon、terrain PNG overlay、curved textPath label 的支持程度 |
| 当前系统已有 | N29.2 的 tier collapse/expand 已实现层级折叠；NovelMap 的 `collapsedChildCount` + double-click expand 已是 Combo 的轻量版本 |

**结论**: **N31 不建议全面迁移**。但可以考虑在**关系图 (GraphPage)** 上试点 G6 — 那里是纯 force-directed 图，没有地形/河流/领地等自定义层，迁移成本低得多。地图页保持 D3 SVG。

### B.3 WebGL 渲染 (AntV G6 Canvas/WebGL 或 Sigma.js)

| 维度 | 评估 |
|------|------|
| 视觉冲击 | **4/5** — WebGL shader 能实现: 实时 Perlin noise 地形着色（替代静态 PNG）、Sobel 边缘检测手绘风、toon shading、粒子效果 (fog of war 等)。这是视觉天花板的质变 |
| 工程投入 | **40-60 人天** — 自定义 fragment shader 开发 (GLSL)、WebGL context 管理、与 React 生命周期整合、所有 SVG 元素转 WebGL primitives、性能调优 |
| 风险等级 | **极高** — GLSL shader 开发是极专业技能；WebGL 的调试工具远不如 SVG DOM inspector；文字渲染在 WebGL 中是出了名的难题 (SDF text rendering)；与现有 d3-zoom、d3-drag 的交互模型完全不同 |
| 依赖项 | 需要先有可运行的 WebGL canvas + 基础 zoom/pan；需要解决 WebGL 上的文字渲染 (可能引入 pixi.js 或 three.js) |
| 替代方案 | **Canvas 2D** 是更合理的中间态 — 保持 2D API 的简单性同时获得批量渲染性能提升 |

**结论**: **N31 绝对不碰**。这是 N33-N35 的长期目标。当前阶段的 ROI 最差 — 投入巨大、风险极高、而用户痛点 (参数保守导致视觉弱) 用参数调优就能解决大部分。

### B.4 XAI (可解释 AI — SHAP + 证据溯源)

| 维度 | 评估 |
|------|------|
| 视觉冲击 | **2/5** — 这是交互层改进，不是视觉改进。SHAP 值对普通小说读者几乎无意义 |
| 工程投入 | **10-15 人天** — 证据溯源: 已有 `RelationStage.evidences`，需在点击 edge 时 popup 显示原文。SHAP: 需引入 shap 库 + 对 LLM 输出做 perturbation — 对本系统的 LLM 调用模式不太适用 |
| 风险等级 | **低 (溯源部分)** / **高 (SHAP 部分)** |
| 依赖项 | 证据溯源: 需要 `chapter_facts.fact_json` 中 evidences 字段的质量保证 |
| 当前系统已有 | EntityCardDrawer 已显示关系列表 + evidence 文字；关系图 edge hover 已有 tooltip |

**结论**: 证据溯源 popup 可以作为 **N31 的小 story** (2-3 天)。SHAP 没有必要 — 小说分析不需要机器学习可解释性。

---

## C. N31 推荐 Epic: "从散点图到地图 — 视觉品质冲刺"

**设计原则**: 最大化视觉冲击 / 最小化架构变更。不引入新渲染引擎，不改数据模型，聚焦于**让已有系统看起来好 10 倍**。

### Story N31.1: 领地边界升级 — MapSets-lite (连通凸包 + 互斥裁剪)

**痛点**: 当前凸包领地可能重叠，深层子领地溢出父领地。
**方案**: 在现有 `hullTerritoryGenerator.ts` 基础上，加两个后处理步骤:
1. **Sutherland-Hodgman 裁剪**: 子领地多边形被父领地多边形裁剪，保证不溢出
2. **同级互斥**: 同一父级下的兄弟领地，用 Voronoi-seam 方式切分重叠区域（每个重叠像素分配给最近中心的兄弟）
3. **凹包支持** (可选): 对子节点数 > 5 的领地，用 concave hull (alpha shape) 替代 convex hull

**视觉影响**: 4/5 — 领地边界从"随意的虚线泡泡"变成"互不侵犯的政治版图"
**工程投入**: 5-7 天 (纯前端 TypeScript 算法)
**文件影响**: `hullTerritoryGenerator.ts` + `NovelMap.tsx` 领地渲染 useEffect

### Story N31.2: 地形纹理笔触升级 — 手绘风格 SVG filter

**痛点**: terrain hints 是规则的几何图形 (三角形、圆、波浪线)，缺乏手绘质感。
**方案**:
1. 为 terrain hint `<use>` 元素添加 SVG `<filter>` — `feTurbulence` (纹理) + `feDisplacementMap` (扭曲) 组合产生手绘笔触效果
2. 增加 terrain hint 符号变体: 松树轮廓、帆船 (水域)、云团 (高山)、沙丘曲线 (沙漠)
3. 为 territory 边界 `<path>` 同样施加轻度手绘 filter
4. 添加全图 parchment 纹理 SVG filter (noise + grain)

**视觉影响**: 4/5 — 从"CAD 制图"到"手绘奇幻地图"的质变
**工程投入**: 3-5 天 (纯 SVG filter 定义 + terrainHints.ts 符号扩展)
**文件影响**: `NovelMap.tsx` (SVG `<defs>` filter 定义), `terrainHints.ts` (新符号)

### Story N31.3: 海岸线与大陆轮廓

**痛点**: 地图没有"边界感" — 节点散布到 canvas 边缘突然截断。
**方案**:
1. 后端: 用所有地点的 alpha shape (或 convex hull + padding) 计算"大陆轮廓"多边形
2. 轮廓外填充"海洋色" (偏蓝灰)，轮廓线用 distortPolygonEdges 产生手绘海岸线
3. 在海岸线外侧绘制 2-3 圈渐淡的平行线模拟"海浪等深线" (传统手绘地图风格)
4. 海洋区域撒水域 terrain hints (波浪符号)
5. 可选: 指南针玫瑰图 (compass rose) SVG 装饰

**视觉影响**: 5/5 — **这是单项投入产出比最高的改进**。有海岸线的地图和没有海岸线的地图在视觉感知上是两个完全不同的东西
**工程投入**: 5-7 天 (后端大陆轮廓计算 + 前端 SVG 渲染)
**文件影响**: `map_layout_service.py` (新函数), `visualization_service.py` (API 返回), `NovelMap.tsx` (新 SVG 图层)

### Story N31.4: 语义缩放 — 逐级信息揭示

**痛点**: zoom in/out 只是几何缩放，没有信息密度控制。
**方案**:
1. 定义 3 个语义 zoom 级别:
   - **宏观 (scale < 0.8)**: 只显示 continent/kingdom 节点 + 一级领地 + 地形底图。隐藏所有 site/building 标签和小节点
   - **中观 (0.8-2.0)**: 显示到 city 级别，领地降低 opacity，地形细节浮现
   - **微观 (> 2.0)**: 显示所有节点，包括 building 级标签
2. 过渡动画: opacity fade 而非 hard show/hide
3. 标签碰撞检测 per zoom level 只处理当前可见层级的标签
4. 地形 PNG opacity 随 zoom 反比变化 (放大后减淡，让节点更清晰)

**视觉影响**: 3/5 — 改善密集地图的可读性，但不改变整体美观度
**工程投入**: 4-5 天
**文件影响**: `NovelMap.tsx` (zoom 事件处理 + 图层 opacity 控制)

### Story N31.5: 地图导出高清海报模式

**痛点**: 用户想保存/分享地图，当前只能截屏。
**方案**:
1. "导出为 PNG" 按钮: 使用 `html2canvas` 或直接 SVG → Canvas → blob download
2. 导出前运行模拟退火标签优化 (N30.2 的方案)，确保静态版本所有标签最优排布
3. 输出分辨率: 4K (3840x2160)，terrain PNG 同步放大
4. 可选: SVG 直接导出 (矢量格式，可在 Illustrator 编辑)
5. 水印/标题: 小说名 + "AI Reader 制图" + 章节范围

**视觉影响**: 3/5 — 不改善在线体验，但让成果可分享
**工程投入**: 4-6 天 (含模拟退火集成)
**文件影响**: `MapPage.tsx` (导出按钮), 新文件 `lib/mapExporter.ts`

### Story N31.6: NovelMap 组件拆分 (技术债)

**痛点**: 2373 行单体组件，任何改动都有回归风险。
**方案**: 将 NovelMap.tsx 拆分为:
- `NovelMapCore.tsx` — SVG 初始化、zoom/pan、canvas 设置 (~300 行)
- `TerrainLayer.tsx` — terrain PNG + terrain hints rendering (~150 行)
- `TerritoryLayer.tsx` — territory hull 渲染 + 文字弧线 (~200 行)
- `RiverLayer.tsx` — 河流渲染 (~50 行)
- `LocationLayer.tsx` — 地点图标 + 标签 + 碰撞检测 (~500 行)
- `TrajectoryLayer.tsx` — 轨迹动画 (~200 行)
- `ConflictLayer.tsx` — 冲突标记 (~100 行)
- `useMapZoom.ts` — zoom/pan hook

**视觉影响**: 0/5 — 纯重构，用户无感
**工程投入**: 5-7 天 (大量测试保证无回归)
**文件影响**: 拆分 `NovelMap.tsx` 为 7-8 个文件

### Sprint 优先级排序

| 优先级 | Story | 视觉冲击 | 投入 | 理由 |
|--------|-------|----------|------|------|
| P0 | N31.3 海岸线与大陆轮廓 | 5/5 | 5-7d | **解决"散点在空白画布上"的根本感知问题** |
| P0 | N31.2 手绘风格 SVG filter | 4/5 | 3-5d | 投入最小、视觉回报最大的 quick win |
| P1 | N31.1 领地互斥裁剪 | 4/5 | 5-7d | 从"泡泡"升级为"版图" |
| P1 | N31.4 语义缩放 | 3/5 | 4-5d | 密集地图的必要改进 |
| P2 | N31.5 地图导出 | 3/5 | 4-6d | 用户分享需求 |
| P2 | N31.6 组件拆分 | 0/5 | 5-7d | 为后续迭代降低维护成本 |

**建议 Sprint 容量**: 取 P0 + P1 共 4 个 story = 17-24 人天。

---

## D. 长期架构演进路线图

### Phase 1 — N31 (当前): SVG 极限优化
**目标**: 在不更换渲染引擎的前提下，将 SVG 地图的视觉品质推到极限。
**产出**: 海岸线、手绘风格、领地互斥、语义缩放。
**技术约束**: 纯 SVG + D3，不引入新依赖。

### Phase 2 — N32-N33: Canvas 2D 混合渲染
**目标**: 将性能敏感层 (terrain, terrain hints) 迁移到 `<canvas>` offscreen rendering，SVG 保留交互层 (locations, labels, trajectories)。
**关键决策**:
- 是否引入 pixi.js (Canvas 2D + WebGL 自动切换)
- terrain 从后端 PNG 迁移到前端实时 Canvas Perlin noise 渲染
- 预计 **15-20 人天**

**里程碑**:
- Canvas terrain + SVG overlay 的双层架构
- 前端实时地形着色 (消除后端 terrain.png 延迟)
- 支持 2000+ 节点不卡顿

### Phase 3 — N34-N35: WebGL 渲染引擎
**目标**: 全面 WebGL + 自定义 shader。
**前提条件**:
1. Phase 2 的 Canvas 2D 混合架构已稳定
2. 确认 pixi.js / regl / three.js 的选型
3. 有 GLSL shader 开发能力

**关键能力**:
- Fragment shader 实时 Perlin noise 地形 (GPU accelerated)
- Sobel edge detection 手绘边界
- SDF text rendering (解决 WebGL 文字问题)
- 粒子系统 (fog of war 散开动画、天气效果)
- 预计 **30-40 人天**

### Phase 4 — N36+: AntV G6 / 图可视化专用引擎 (可选)
**条件**: 只在关系图 (GraphPage) 需要复杂交互 (Combo 嵌套、拓扑编辑) 时考虑。
**地图页 (MapPage) 不建议迁移到 G6** — 地图的自定义渲染需求 (terrain, rivers, coastline, hand-drawn filter) 与 G6 的图可视化范式不匹配。

### SpRL 时间点
**不早于 Phase 2**。在当前 ChapterFact schema 稳定运行、累积足够多的用户反馈后，才考虑数据模型扩展。优先通过 prompt engineering 提升提取质量。

---

## E. 关键技术决策 — 需要用户输入

### 决策 1: N31.3 海岸线的适用范围

当前系统有三种布局模式: constraint (虚拟世界)、geographic (真实地图)、hierarchy (层级树)。

- **constraint 模式**: 海岸线最有价值 — 虚拟大陆需要边界感
- **geographic 模式**: 已有真实地图瓦片 (CartoDB Positron)，不需要海岸线
- **hierarchy 模式**: 不适用

**需确认**: 海岸线是否只在 constraint + layered 模式下渲染？

### 决策 2: 手绘风格的强度控制

SVG `feTurbulence` + `feDisplacementMap` filter 可以产生从"轻微纸质感"到"极度手绘笔触"的连续范围。

- **保守方案**: 仅对 territory 边界和 terrain hints 施加轻度 filter，保留当前的"现代简洁"风格
- **激进方案**: 全图 parchment 纹理 + 所有线条手绘化 + 标签使用 serif 字体 + 全面奇幻地图风格

**需确认**: 用户想要的是"更好看的数据可视化"还是"像 Inkarnate/Wonderdraft 那样的手绘奇幻地图"？这两个方向在美术语言上是完全不同的。

### 决策 3: NovelMap 组件拆分时机

N31.6 是零视觉影响的纯重构。有两种策略:

- **A. N31 就做**: 先拆分再实现 N31.1-N31.5，开发体验好但 Sprint 容量被占用
- **B. N31 之后做**: 先在 2373 行巨型文件中实现 N31.1-N31.5，然后 N32 第一个 story 拆分

**需确认**: 对开发速度 vs Sprint 可见产出的权衡偏好？

### 决策 4: Canvas 2D 迁移 (Phase 2) 的时间预期

Phase 2 是一个分水岭 — 一旦 terrain 迁移到 Canvas，SVG 和 Canvas 双层架构就需要长期维护。

- 如果 N31 的 SVG 优化效果已经让用户满意，Phase 2 可以延后到 N35+
- 如果 N31 后仍觉得地形效果不够好 (因为 SVG filter 有性能开销)，则需要尽快进入 Phase 2

**需确认**: N31 完成后再评估？还是现在就想确定 Phase 2 时间线？

### 决策 5: 关系图 (GraphPage) 的 G6 试点

GraphPage 当前用 `react-force-graph-2d` (基于 Canvas 2D + force-directed)。如果有以下需求，可以考虑 G6 试点:

- Combo 嵌套 (按组织/阵营折叠人物)
- 拓扑编辑 (拖拽创建/删除关系)
- 子图探索 (展开某个人物的 N 跳关系)

**需确认**: 关系图是否有这些交互需求？如果只是展示，`react-force-graph-2d` 足够了。

---

## 附录: 研究报告对照现实的校准表

| 报告建议 | 系统现状 | 差距评估 |
|----------|---------|----------|
| CSP 约束满足求解坐标 | `ConstraintSolver` + `differential_evolution` + force-directed seed | **已实现 90%**。缺少的是文本中距离描述的权重化 (当前用 mention 频次代替) |
| Whittaker biome PCG 地形 | `_WHITTAKER_GRID` 5x5 + 双线性插值 + Lloyd 松弛 | **已实现 80%**。缺少: 前端实时渲染 (当前是后端 PNG)、岛屿/大陆轮廓、海洋 |
| 河流水系 gradient descent | `generate_rivers()` + `_trace_river()` | **已实现 100%** |
| GMap / MapSets 互斥领地 | Convex hull + noise distortion | **已实现 40%**。缺少: 互斥保证、凹包、子不溢出父裁剪 |
| 8 锚点标签碰撞检测 | `computeLabelLayout()` + Grid 空间索引 | **已实现 100%** |
| 模拟退火标签全局优化 | N30.2 story 设计完毕，未实现 | **已规划** |
| WebGL 渲染 | 无 | **完全未实现** (Phase 3) |
| 语义缩放 | `TIER_MIN_SCALE` 硬阈值 | **已实现 30%**。缺少: 平滑过渡、zoom-dependent 信息密度 |
| Geo-Storylines 轨迹动画 | 双路径 + 脉冲播放 + auto-pan | **已实现 90%**。缺少: 时间线联动的领地变化 |
| 手绘风格 | `edgeDistortion.ts` 边界扰动 | **已实现 20%**。缺少: SVG filter 纹理、笔触效果、字体风格 |

**总结**: 研究报告假设系统处于"节点散布 + 力导向图"的原始阶段。实际上系统已经实现了报告建议的大部分后端基础设施。真正的差距集中在**前端渲染表现层** — 参数调优、视觉整合、手绘风格化、海岸线/大陆轮廓。这些都可以在当前 SVG 架构内完成，不需要架构级重写。
