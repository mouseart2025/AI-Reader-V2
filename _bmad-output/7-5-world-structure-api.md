# Story 7.5: WorldStructure API 端点

Status: ready-for-dev

## Story

As a 前端,
I want 通过 API 获取小说的世界结构数据,
So that 地图页面可以渲染多层级地图。

## Acceptance Criteria

1. `GET /api/novels/{novel_id}/world-structure` 返回完整 WorldStructure JSON
2. 小说未分析或无世界结构时返回默认结构（仅 overworld 层）
3. API 响应包含 layers, portals, regions 列表以及地点映射

## Tasks / Subtasks

- [ ] Task 1: 创建 API 路由 (AC: #1, #2)
  - [ ] 新建 `backend/src/api/routes/world_structure.py`
  - [ ] `GET /api/novels/{novel_id}/world-structure`:
    - 验证 novel 存在（404 if not found）
    - 从 world_structure_store.load() 获取
    - 如果为 None，返回默认结构
    - 序列化返回 JSON
- [ ] Task 2: 注册路由 (AC: #1)
  - [ ] 在 `backend/src/api/main.py` 中 import 并 include router
  - [ ] 路由前缀: `/api/novels/{novel_id}/world-structure`
- [ ] Task 3: 验证响应格式 (AC: #3)
  - [ ] 确保响应包含: layers(list), portals(list), location_region_map(dict), location_layer_map(dict)
  - [ ] 每个 layer 包含: layer_id, name, layer_type, regions(list)
  - [ ] 每个 portal 包含: name, source_layer, source_location, target_layer, first_chapter

## Dev Notes

### 现有 API 模式

- 参考 `backend/src/api/routes/map.py` 的路由模式
- 使用 `from src.db import novel_store` 验证小说存在
- 路由使用 `APIRouter(prefix=..., tags=[...])`
- 在 `main.py` 中使用 `app.include_router(router)` 注册

### References

- [Source: backend/src/api/routes/map.py — 现有地图 API 模式]
- [Source: backend/src/api/main.py — 路由注册]

## Dev Agent Record

### Agent Model Used

### Completion Notes List

### File List
