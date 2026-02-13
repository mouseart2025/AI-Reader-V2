# Story 7.10: 区域边界、传送门 UI 与增强 Fog of War

Status: ready-for-dev

## Story

As a 用户,
I want 在主世界地图上看到区域边界划分和传送门入口标记,
So that 我能直观理解小说世界的宏观结构和空间层级。

## Acceptance Criteria

1. overworld 层区域以半透明填充色 + 虚线边界显示
2. 区域名称以大字体低透明度标注在区域中心
3. 传送门显示为特殊图标（⊙），点击弹出 Popup 可切换到目标层
4. Fog of War 增强为三态: hidden(不显示) / revealed(灰色) / active(完整显示)
5. 传送门点击"进入地图"联动 Tab 切换

## Tasks / Subtasks

- [ ] Task 1: 添加区域边界 GeoJSON 层 (AC: #1, #2)
  - [ ] 在 `NovelMap.tsx` 中新增 3 个 GeoJSON 层（在 trajectory 之前插入）:
    - `region-fills`: type=fill, 半透明填充 (opacity 0.08)
    - `region-borders`: type=line, 虚线边界 (dasharray [4,4], opacity 0.3)
    - `region-labels`: type=symbol, 大字体区域名 (text-size 18, opacity 0.4)
  - [ ] 新增 `SRC_REGIONS = "regions-src"` GeoJSON source
  - [ ] 从 `region_boundaries` prop 构建 GeoJSON Polygon features
  - [ ] 将画布坐标 bounds (x1,y1,x2,y2) 转为 LngLat 多边形
- [ ] Task 2: 添加传送门图标层 (AC: #3, #5)
  - [ ] 新增 `SRC_PORTALS = "portals-src"` + `LYR_PORTALS = "portals-layer"` 层
  - [ ] 传送门渲染为圆圈 + 内部旋涡图案（或用 ⊙ Unicode 符号作为 text-field）
  - [ ] 传送门颜色: 与目标层类型相关（celestial=金色, underworld=紫色, instance=棕色）
  - [ ] 点击传送门弹出 Popup:
    ```html
    <div>
      <div>⊙ {portal.name}</div>
      <div>通往: {target_layer_name}</div>
      <button>进入地图</button>
      <button>查看卡片</button>
    </div>
    ```
  - [ ] "进入地图"按钮点击 → 调用 `onPortalClick(target_layer_id)` 回调 → MapPage 切换 Tab
- [ ] Task 3: NovelMap Props 扩展 (AC: #1-#5)
  - [ ] 新增 props:
    - `regionBoundaries?: RegionBoundary[]`
    - `portals?: PortalInfo[]`
    - `layerType?: LayerType`
    - `onPortalClick?: (targetLayerId: string) => void`
  - [ ] 在 MapPage 中传递这些 props
- [ ] Task 4: 增强 Fog of War (AC: #4)
  - [ ] 修改 visibleLocationNames 逻辑为三态:
    - `activeLocations`: 当前章节范围内出现的地点 → opacity=1
    - `revealedLocations`: 之前章节出现但不在当前范围 → opacity=0.35, 灰色轮廓
    - 其他: 不渲染（完全隐藏）
  - [ ] 后端 API 需要返回 `all_known_locations`（所有曾出现过的地点）
  - [ ] 前端根据 chapter_start 判断哪些是 revealed vs active
- [ ] Task 5: MapPage 联动 (AC: #5)
  - [ ] 在 MapPage 中接收 `onPortalClick` 回调
  - [ ] 回调触发: `setActiveLayerId(targetLayerId)` → 重新加载该层数据

## Dev Notes

### 现有 NovelMap 层级顺序

```
background → terrain-layer → trajectory-line → trajectory-points → locations-circles → locations-labels
```

新增层应插入在 trajectory 之前:
```
background → terrain-layer → region-fills → region-borders → region-labels → trajectory-line → ...
```

传送门层在 locations-circles 之后:
```
... → locations-circles → portals-layer → locations-labels
```

### 坐标转换

区域边界 bounds `(x1, y1, x2, y2)` 需要转为 LngLat 多边形:
```typescript
const polygon = [
  toLngLat(x1, y1), // 左下
  toLngLat(x2, y1), // 右下
  toLngLat(x2, y2), // 右上
  toLngLat(x1, y2), // 左上
  toLngLat(x1, y1), // 闭合
]
```

### References

- [Source: _bmad-output/world-map-v2-architecture.md#7.2-区域边界显示]
- [Source: _bmad-output/world-map-v2-architecture.md#7.3-传送门交互]
- [Source: _bmad-output/world-map-v2-architecture.md#7.5-渐进式解锁]
- [Source: frontend/src/components/visualization/NovelMap.tsx — 现有图层结构]

## Dev Agent Record

### Agent Model Used

### Completion Notes List

### File List
