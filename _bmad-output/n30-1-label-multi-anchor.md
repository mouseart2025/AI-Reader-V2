# Story N30.1: 标签多锚点智能偏移

Status: draft

## Story

As a 用户,
I want 地图标签在默认位置（节点下方）碰撞时自动尝试其他位置（右侧、上方、左下等），而不是直接被隐藏,
So that 密集区域（如大观园片区 20+ 建筑节点）的地名标签显示率大幅提升，地图可读性显著改善。

## Background

当前 `computeLabelCollisions()` 采用贪心策略：按优先级排序，碰撞即隐藏。对于大观园这类密集区域，大量中低频地点被隐藏，即使周边有空间也无法利用。

报告（维度4）建议引入"动态锚点偏移"——碰撞时尝试候选位置，类似 Mapbox 的 `text-variable-anchor`。此功能在现有 Grid 空间索引基础上扩展，工作量可控。

## Acceptance Criteria

1. **AC-1**: `computeLabelCollisions()` 升级为 `computeLabelLayout()`，碰撞时依次尝试 8 个候选锚点：底部中心(默认) → 右侧 → 右上 → 顶部 → 左上 → 左侧 → 左下 → 右下
2. **AC-2**: 每个候选锚点的偏移量基于 iconSize 和 fontSize 计算，确保不遮挡节点图标
3. **AC-3**: 函数返回 `Map<string, { anchor: string, offsetX: number, offsetY: number }>` 而非 `Set<string>`，包含选中的锚点信息
4. **AC-4**: 渲染时根据返回的锚点信息设置标签位置和 `text-anchor` 属性（底部/顶部用 "middle"，左侧用 "end"，右侧用 "start"）
5. **AC-5**: 仅当全部 8 个候选位置均碰撞时才隐藏标签
6. **AC-6**: Grid 空间索引复用现有实现，每次尝试新位置时检查对应 cell
7. **AC-7**: 性能不退化——100 个可见节点场景下碰撞检测耗时 < 5ms（当前约 1-2ms）

## Tasks / Subtasks

- [ ] Task 1: 定义锚点候选系统
  - [ ] 1.1 定义 `ANCHOR_CANDIDATES` 数组：8 个锚点，每个包含 `{ name, textAnchor, getOffset(iconSize, fontSize) → {dx, dy} }`
  - [ ] 1.2 默认锚点：底部中心 `(0, iconSize/2 + fontSize*0.9)`
  - [ ] 1.3 右侧锚点：`(iconSize/2 + 4, 0)`，text-anchor="start"
  - [ ] 1.4 顶部锚点：`(0, -iconSize/2 - 4)`，text-anchor="middle"

- [ ] Task 2: 升级碰撞检测函数
  - [ ] 2.1 重命名 `computeLabelCollisions` → `computeLabelLayout`
  - [ ] 2.2 修改返回类型为 `Map<string, LabelPlacement>`（含 anchor/offsetX/offsetY/textAnchor）
  - [ ] 2.3 在贪心循环中，碰撞时遍历 ANCHOR_CANDIDATES 尝试替代位置
  - [ ] 2.4 每个候选位置构建临时 LabelRect，检查 Grid 索引中是否碰撞
  - [ ] 2.5 找到无碰撞的候选位置后，将其注册到 Grid 索引并记录到结果 Map

- [ ] Task 3: 更新渲染管线
  - [ ] 3.1 Zoom effect 中调用 `computeLabelLayout()` 替代 `computeLabelCollisions()`
  - [ ] 3.2 根据返回的 LabelPlacement 设置每个 `.loc-label` 的 `x`/`y`/`text-anchor` 属性
  - [ ] 3.3 原有的 `.style("display", ...)` 隐藏逻辑改为：有 placement → 显示并定位，无 placement → 隐藏

## Dev Notes

### 锚点偏移量计算

```typescript
const ANCHOR_CANDIDATES = [
  { name: "bottom",      textAnchor: "middle", dx: 0,             dy: iconH/2 + fh*0.9 },
  { name: "right",       textAnchor: "start",  dx: iconH/2 + 4,   dy: fh*0.3 },
  { name: "top-right",   textAnchor: "start",  dx: iconH/2 + 2,   dy: -iconH/2 },
  { name: "top",         textAnchor: "middle", dx: 0,             dy: -iconH/2 - 4 },
  { name: "top-left",    textAnchor: "end",    dx: -iconH/2 - 2,  dy: -iconH/2 },
  { name: "left",        textAnchor: "end",    dx: -iconH/2 - 4,  dy: fh*0.3 },
  { name: "bottom-left", textAnchor: "end",    dx: -iconH/2 - 2,  dy: iconH/2 + fh*0.9 },
  { name: "bottom-right",textAnchor: "start",  dx: iconH/2 + 2,   dy: iconH/2 + fh*0.9 },
]
```

### 性能考量

8 个候选位置 × N 个标签，最坏 O(8N) 次碰撞检查。但由于 Grid 索引每次检查是 O(1)，总体仍为 O(N)。实际上大多数标签在前 1-3 个候选就能找到位置。
