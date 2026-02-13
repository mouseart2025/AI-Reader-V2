# Story 7.8: 地图 API V2 与层布局缓存

Status: ready-for-dev

## Story

As a 前端,
I want 通过 API 按层获取地图布局数据,
So that 前端可以按需加载各层的地图。

## Acceptance Criteria

1. 地图 API 支持 `layer_id` 查询参数，按层返回布局数据
2. 未指定 `layer_id` 时默认返回 overworld 层 + world_structure 概要
3. 响应包含 region_boundaries 和 portals 信息
4. 每层布局独立缓存到 `layer_layouts` 表
5. 现有前端不指定 layer_id 时兼容正常工作

## Tasks / Subtasks

- [ ] Task 1: 修改地图 API (AC: #1, #2, #5)
  - [ ] 在 `backend/src/api/routes/map.py` 的 `get_map()` 中添加 `layer_id: str | None = Query(None)` 参数
  - [ ] 修改 `visualization_service.get_map_data()` 签名，接受 `layer_id` 参数
  - [ ] 未指定 layer_id: 返回 overworld 层数据 + `world_structure` 概要字段
  - [ ] 指定 layer_id: 返回该层的布局数据
- [ ] Task 2: 添加响应新字段 (AC: #3)
  - [ ] 在地图 API 响应中新增:
    - `region_boundaries`: `[{ region_name, color, bounds: {x1,y1,x2,y2} }]`
    - `portals`: `[{ name, source_location, target_layer, target_layer_name }]`
    - `world_structure`: `{ layers: [{layer_id, name, layer_type, location_count}] }`（仅概要）
  - [ ] 保留现有响应字段（locations, layout, layout_mode, terrain_url, trajectories, analyzed_range）
- [ ] Task 3: 层布局缓存 (AC: #4)
  - [ ] 在 `visualization_service.py` 中实现按 `(novel_id, layer_id, chapter_hash)` 缓存层布局
  - [ ] 使用 Story 7.1 创建的 `layer_layouts` 表
  - [ ] 缓存失效: 新 ChapterFact 写入时失效该 novel_id 的所有层布局

## Dev Notes

### 现有地图 API 响应格式

```python
{
    "locations": [...],
    "trajectories": {...},
    "spatial_constraints": [...],
    "layout": [...],
    "layout_mode": "constraint" | "hierarchy",
    "terrain_url": str | None,
    "analyzed_range": [first, last],
}
```

新增字段附加在现有字段之后，不破坏现有前端。

### 缓存模式

参考现有 `map_layouts` 表的缓存模式:
- `visualization_service.py` 中 `_compute_or_load_layout()` 方法
- 使用 chapter_hash 做缓存 key

### References

- [Source: backend/src/api/routes/map.py — 现有地图路由]
- [Source: backend/src/services/visualization_service.py — 现有布局缓存]
- [Source: _bmad-output/world-map-v2-architecture.md#8.2-API响应结构]

## Dev Agent Record

### Agent Model Used

### Completion Notes List

### File List
