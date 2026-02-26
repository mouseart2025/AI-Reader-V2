# Story N30.6: 地图视觉参数调优 — 让 N30 改动真正可见

Status: ready

## Story

As a 使用红楼梦地图的用户,
I want 看到明显的地形色彩分区、领地色块、河流水系和装饰符号,
So that 地图不再是"节点散布在空白画布上"，而是具有地理隐喻的沉浸式视觉体验。

## Background

QA 根因分析发现 Epic N30 的后端改动（Whittaker 生物群落、河流水系）在前端几乎不可见。核心问题不是功能缺失，而是**渲染参数过于保守 + 关键渲染路径被禁用**。

### 根因清单

| # | 严重度 | 根因 | 影响 |
|---|--------|------|------|
| 1 | 致命 | 地形 PNG 在前端被注释禁用（NovelMap.tsx:599） | N30.4 全部工作不可见 |
| 2 | 严重 | 领地填充 opacity 最大仅 0.05 | 区域色块肉眼不可辨 |
| 3 | 中等 | 河流最大 3px + 0.6 opacity + 仅 constraint 模式 | 河流几乎看不到 |
| 4 | 中等 | terrain hints opacity 仅 13%-22% | 装饰符号融入背景 |
| 5 | 轻微 | Whittaker 矩阵 RGB 窄带 + 15% cream blend | biome 无色差 |

## Acceptance Criteria

1. **AC-1**: 地形底图 PNG 在地图上可见，能看出不同区域的 biome 颜色差异（绿色林地 vs 棕色干地 vs 浅色高地）
2. **AC-2**: 领地填充区域在默认缩放下肉眼可辨，不同领地通过色块深浅区分
3. **AC-3**: 河流在地图上一眼可见（不需要仔细找），宽度和颜色与背景有足够对比
4. **AC-4**: 地形装饰符号（山脉三角、树丛、波浪）在正常浏览时自然可见
5. **AC-5**: 所有视觉增强不遮挡地点图标和标签的可读性（这是保守参数的原始原因）
6. **AC-6**: `npm run build` 通过，无新增 TypeScript/ESLint 错误

## Tasks

### Task 1: 启用地形 PNG 渲染 [致命修复]

**文件**: `frontend/src/components/visualization/NovelMap.tsx`

- [ ] 1.1 在 SVG init useEffect 中，`#terrain` group 创建后，当 `terrainUrl` 非空时添加 `<image>` 元素
- [ ] 1.2 参数：`opacity: 0.35`，`width/height` 为 canvas 尺寸，`preserveAspectRatio: "none"`
- [ ] 1.3 确保 z-order 正确：terrain image 在 `#terrain` group 内、terrainHints `<use>` 元素之前
- [ ] 1.4 移除或更新行 599-601 的 disabled 注释

### Task 2: 提升领地填充透明度 [严重修复]

**文件**: `frontend/src/components/visualization/NovelMap.tsx`

- [ ] 2.1 修改 `FILL_OP` 常量：`[0.05, 0.04, 0.03, 0.02]` → `[0.15, 0.10, 0.07, 0.04]`
- [ ] 2.2 如果描边在密集模式下太弱，同步提升 `STROKE_OP` 的密集模式值

### Task 3: 增强河流视觉 [中等修复]

**文件**: `frontend/src/components/visualization/NovelMap.tsx` + `backend/src/services/visualization_service.py`

- [ ] 3.1 前端：河流 opacity 从 `0.6` → `0.8`
- [ ] 3.2 前端：河流 stroke-width 乘以 2（`river.width * 2`）
- [ ] 3.3 后端：`generate_rivers` 调用条件从 `layout_mode == "constraint"` 放宽为 `layout_mode not in ("geographic",)`
- [ ] 3.4 后端：河流最大宽度从 `3.0` 提升到 `5.0`

### Task 4: 提升装饰符号可见度 [中等修复]

**文件**: `frontend/src/lib/terrainHints.ts`

- [ ] 4.1 `baseOpacity` 从 `darkBg ? 0.18 : 0.22` → `darkBg ? 0.28 : 0.35`

### Task 5: 增强 Whittaker 生物群落色彩饱和度 [轻微修复]

**文件**: `backend/src/services/map_layout_service.py`

- [ ] 5.1 `_WHITTAKER_GRID` 矩阵颜色加大饱和度差异（沙漠更黄、森林更绿、沼泽更蓝绿）
- [ ] 5.2 cream blend 比例从 15% 降至 8%：`(rgb * 85 + cream * 15)` → `(rgb * 92 + cream * 8)`
- [ ] 5.3 验证：修改后重启后端，刷新红楼梦地图，确认 terrain.png 色彩区分度提升

## Dev Notes

### 关键风险：视觉平衡

地形 PNG 被禁用的原始原因是"Voronoi 边界暗化与领地/区域层冲突"。启用时需要注意：
- terrain PNG opacity 不宜超过 0.40，否则会遮挡上层 SVG 元素
- 如果 Voronoi 边界暗化仍然干扰，可以在 `generate_terrain()` 中降低 `edge_factor` 系数（当前 0.25）
- 建议先启用 PNG 看效果，再微调参数

### 修改影响范围

- 纯参数调优，不改变任何接口、数据结构或算法逻辑
- 后端改动需要清除 terrain 缓存（删除 `~/.ai-reader-v2/maps/*/terrain.png`）才能看到新 Whittaker 颜色
- 前端改动即时生效（dev server HMR）
