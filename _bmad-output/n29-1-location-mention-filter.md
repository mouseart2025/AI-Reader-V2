# Story N29.1: 地点重要度过滤滑块

Status: draft

## Story

As a 用户,
I want 通过滑块按提及次数过滤地图上的低频地点,
So that 像红楼梦（760地点）这类密集场景的地图变得可读，只显示真正重要的地点。

## Background

红楼梦分析后产生 760 个地点，全部平铺在地图上导致极度拥挤。大量 building 级地点（偏房、耳房、夹道等）仅出现 1-2 次，对整体理解价值有限。GraphPage 已有成熟的 `minEdgeWeight` + `suggested_min_edge_weight` 模式可以复用。

实测数据：红楼梦 760 地点中，mention_count ≥ 3 的约 200 个，≥ 5 的约 120 个。一个合理的默认值可将可见地点从 760 降至 100-200，大幅改善可读性。

## Acceptance Criteria

1. **AC-1**: 后端 `get_map_data()` 返回新字段 `max_mention_count`（最大提及次数）和 `suggested_min_mentions`（建议最低提及次数）
2. **AC-2**: `suggested_min_mentions` 的计算逻辑：地点总数 > 300 时建议 3，> 150 时建议 2，否则 1
3. **AC-3**: 前端地图页面工具栏显示 "最少提及" 滑块（range input），范围 1 ~ max_mention_count
4. **AC-4**: 滑块默认值为后端返回的 `suggested_min_mentions`
5. **AC-5**: 滑块值变化时，前端实时过滤 `locations` 和 `layout` 数组（mention_count < 阈值的地点隐藏），无需重新请求后端
6. **AC-6**: 过滤后的地点数量显示在滑块旁（如 "显示 186 / 760 地点"）
7. **AC-7**: 滑块使用 debounce（150ms），避免拖动时频繁触发 D3 重渲染

## Tasks / Subtasks

- [ ] Task 1: 后端 — 计算 suggested_min_mentions
  - [ ] 1.1 `visualization_service.py` `get_map_data()` 在构建 `locations` 列表后计算 `max_mention_count = max(loc["mention_count"] for loc in locations)`
  - [ ] 1.2 根据 `len(locations)` 计算 `suggested_min_mentions`：>300→3, >150→2, else→1
  - [ ] 1.3 在返回的 result dict 中新增两个字段

- [ ] Task 2: 前端 — 滑块 UI
  - [ ] 2.1 `MapPage.tsx` 新增状态：`minMentions`（默认 1）、`maxMentionCount`（默认 1）
  - [ ] 2.2 `mapData` 加载完成后，从响应中读取 `suggested_min_mentions` 和 `max_mention_count`，设置滑块初始值
  - [ ] 2.3 在地图左下（冲突开关按钮上方或工具栏区域）添加 range input + 标签
  - [ ] 2.4 实现 debounced 状态（类似 GraphPage 的 `debouncedMinEdgeWeight` 模式）

- [ ] Task 3: 前端 — 地点过滤
  - [ ] 3.1 用 `useMemo` 计算 `filteredLocations = locations.filter(l => l.mention_count >= debouncedMinMentions)`
  - [ ] 3.2 同步过滤 `layout` 数组（只保留 filteredLocations 中存在的地点）
  - [ ] 3.3 将 `filteredLocations` 和 `filteredLayout` 传给 `NovelMap` 组件（替代原来的 `locations` 和 `layout`）
  - [ ] 3.4 同步更新 `visibleLocationNames` Set 以确保 fog-of-war 逻辑一致
  - [ ] 3.5 在滑块旁显示 "显示 {filteredCount} / {totalCount} 地点"

- [ ] Task 4: 前端类型 — MapData 扩展
  - [ ] 4.1 `api/types.ts` 的 `MapData` interface 新增 `max_mention_count?: number` 和 `suggested_min_mentions?: number`

## Dev Notes

### GraphPage 参考模式

```typescript
// GraphPage.tsx — 可直接复用的模式
const [minEdgeWeight, setMinEdgeWeight] = useState(1)
const [debouncedMinEdgeWeight, setDebouncedMinEdgeWeight] = useState(minEdgeWeight)

useEffect(() => {
  const t = setTimeout(() => setDebouncedMinEdgeWeight(minEdgeWeight), 150)
  return () => clearTimeout(t)
}, [minEdgeWeight])

// 后端返回时设置默认值
const suggested = (data.suggested_min_edge_weight as number) ?? 1
setMinEdgeWeight(suggested)
```

### 后端计算位置

在 `visualization_service.py` 的 `get_map_data()` 中，`locations` 列表构建完成后（约 line 380），插入计算逻辑：

```python
max_mention = max((l["mention_count"] for l in locations), default=1)
n_locs = len(locations)
suggested_min = 3 if n_locs > 300 else (2 if n_locs > 150 else 1)

result["max_mention_count"] = max_mention
result["suggested_min_mentions"] = suggested_min
```

### 滑块 UI 位置

放在地图左下角，冲突开关按钮上方。使用与 GraphPage 相同的 `accent-primary` 样式。筛选控件组垂直排列：滑块 → 冲突开关 → 图例。
