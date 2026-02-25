# Story N29.2: 地图层级折叠/展开

Status: draft

## Story

As a 用户,
I want 地图默认只显示高层级地点（城市级以上），点击父节点可展开其子地点,
So that 我能按层级逐步探索地点，而不是被数百个建筑级地点淹没。

## Background

当前 NovelMap 使用 zoom-tier 渐变控制地点可见性（`TIER_MIN_SCALE`），缩放到 3x 才能看到 building 级。但这只是透明度渐变，所有地点始终占据布局空间。红楼梦 760 地点中约 500+ 是 site/building 级，即使看不见也参与碰撞检测和布局计算。

目标：实现类似文件浏览器的折叠/展开机制。默认折叠 site/building 子节点，父节点显示子节点数量 badge。点击父节点展开时，子节点围绕父节点用 sunflower seed 分布放置。

### 现有 tier 系统

```
continent (0.3x) > kingdom (0.5x) > region (0.8x) > city (1.2x) > site (2.0x) > building (3.0x)
```

折叠阈值：默认折叠 site + building（只有点击展开或 zoom > 阈值时才显示）。

## Acceptance Criteria

1. **AC-1**: 默认状态下，tier 为 `site` 和 `building` 的地点不渲染在地图上
2. **AC-2**: 有子节点被折叠的父节点显示子节点数量 badge（如 "+12"）
3. **AC-3**: 点击父节点的 badge 或双击父节点，展开其直接子节点（动画 300ms）
4. **AC-4**: 展开的子节点以 sunflower seed 分布围绕父节点放置（复用现有 `_place_children` 的金角分布逻辑）
5. **AC-5**: 再次点击 badge 或双击父节点，折叠子节点（动画 300ms）
6. **AC-6**: 展开/折叠状态维护在前端 state 中（`expandedNodes: Set<string>`），刷新页面时重置为默认折叠
7. **AC-7**: 展开状态下的子节点仍然受 mention_count 滑块（N29.1）过滤
8. **AC-8**: "全部展开" / "全部折叠" 按钮在工具栏中提供

## Tasks / Subtasks

- [ ] Task 1: 前端状态管理
  - [ ] 1.1 `MapPage.tsx` 新增 `expandedNodes: Set<string>` 状态
  - [ ] 1.2 新增 `collapseTier` 状态（默认 `"site"`，即 site+building 被折叠）
  - [ ] 1.3 `NovelMapProps` 新增 `expandedNodes` 和 `onToggleExpand` 回调

- [ ] Task 2: 前端过滤逻辑
  - [ ] 2.1 `MapPage.tsx` 用 `useMemo` 计算 `displayLocations`：
    - 对每个 location，如果 tier 在折叠范围内（site/building），只在 `expandedNodes` 包含其 parent 时显示
    - 同时受 mention_count 滑块过滤
  - [ ] 2.2 同步过滤 `layout` 数组
  - [ ] 2.3 计算每个可见父节点的折叠子节点数（`collapsedChildCount: Map<string, number>`），传给 NovelMap

- [ ] Task 3: 子节点数量 badge 渲染
  - [ ] 3.1 `NovelMap.tsx` 在有折叠子节点的父节点旁渲染 badge circle（小圆 + 数字 "+N"）
  - [ ] 3.2 Badge 样式：半径 8px，bg `#3b82f6`（蓝色），白色文字，右上角偏移
  - [ ] 3.3 Badge counter-scale 随 zoom 保持固定屏幕尺寸

- [ ] Task 4: 展开/折叠交互
  - [ ] 4.1 点击 badge 或双击父节点 `<g>` 时调用 `onToggleExpand(parentName)`
  - [ ] 4.2 展开动画：新子节点从父节点位置 (parentX, parentY) 开始，过渡到 sunflower seed 目标位置（300ms ease-out）
  - [ ] 4.3 折叠动画：子节点从当前位置过渡回父节点位置并消失（300ms ease-in）

- [ ] Task 5: 子节点布局计算
  - [ ] 5.1 展开时，子节点围绕父节点用 sunflower seed 分布放置（金角 137.5°，半径 = baseRadius × (0.3 + 0.7 × √(i/n))）
  - [ ] 5.2 `baseRadius` 根据子节点数量动态调整（≤5: 30px, ≤15: 50px, >15: 70px）
  - [ ] 5.3 子节点的布局数据计算在前端 `useMemo` 中完成（不请求后端）

- [ ] Task 6: 工具栏按钮
  - [ ] 6.1 在地图左下工具栏区域添加 "全部展开" / "全部折叠" 按钮
  - [ ] 6.2 "全部展开"：将所有有子节点的父节点加入 `expandedNodes`
  - [ ] 6.3 "全部折叠"：清空 `expandedNodes`

## Dev Notes

### Sunflower Seed 分布参考

后端 `map_layout_service.py` 已有实现（`_place_children` 方法）：

```python
GOLDEN_ANGLE = 137.508  # degrees
for i, child in enumerate(children):
    angle = i * GOLDEN_ANGLE * math.pi / 180
    r = base_jitter * (0.3 + 0.7 * math.sqrt(i / max(len(children), 1)))
    cx = parent_x + r * math.cos(angle)
    cy = parent_y + r * math.sin(angle)
```

前端复用同样算法，在 TypeScript 中实现。

### 与 N29.1 的集成

过滤管线顺序：mention_count 过滤 → tier 折叠过滤 → 传给 NovelMap。展开的子节点仍受 mention_count 约束，避免展开后出现大量低频地点。

### 性能考虑

- 折叠后 NovelMap 接收的 locations/layout 数组大幅缩小（760 → ~80-150），D3 渲染显著加快
- 展开时仅对单个父节点的子节点做动画，不触发全量重渲染
- `collapsedChildCount` 用 `useMemo` 缓存，仅在 locations/expandedNodes 变化时重算
