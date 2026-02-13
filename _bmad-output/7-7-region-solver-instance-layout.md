# Story 7.7: 区域内约束求解与副本独立布局

Status: ready-for-dev

## Story

As a 系统,
I want 在每个区域边界框内独立运行约束求解器，副本层用独立小画布布局,
So that 布局质量提升且求解效率更高。

## Acceptance Criteria

1. 每个区域独立运行 ConstraintSolver，地点坐标限制在区域边界框内
2. 单区域地点数 10-50，求解速度优于全局 279 点求解
3. Instance 类型层使用独立 [0, 300] 小画布布局
4. 天界/冥界层使用独立画布布局
5. 传送门位置标注在源层的对应地点附近

## Tasks / Subtasks

- [ ] Task 1: 实现区域内求解 (AC: #1, #2)
  - [ ] 新增 `_solve_region(region_name, region_bounds, locations, constraints) -> list[MapLayoutItem]`
  - [ ] 从全局 locations 和 constraints 中过滤出属于该区域的子集
  - [ ] 修改 ConstraintSolver 支持自定义边界范围 (bounds 参数)
  - [ ] 将区域内地点的坐标限制在 `(x1, y1, x2, y2)` 范围内
- [ ] Task 2: 实现副本层/独立层布局 (AC: #3, #4)
  - [ ] 新增 `_solve_layer(layer_id, layer_type, locations, constraints) -> list[MapLayoutItem]`
  - [ ] Instance 层: 小画布 [0, 300]，简单层级布局
  - [ ] Celestial/Underworld 层: 中等画布 [0, 600]
  - [ ] 每层独立的坐标系，不与 overworld 共享
- [ ] Task 3: 传送门位置标注 (AC: #5)
  - [ ] 在 overworld 层布局结果中，为每个 Portal 的 source_location 添加标记
  - [ ] Portal 位置 = source_location 的坐标
  - [ ] 如果 source_location 不在布局中，放置在最近的已布局地点旁边
- [ ] Task 4: 组装多层布局结果 (AC: #1-#5)
  - [ ] 新增 `compute_layered_layout(world_structure, all_locations, all_constraints) -> dict[str, list[MapLayoutItem]]`
  - [ ] 返回 `{ layer_id: [MapLayoutItem, ...] }` 字典
  - [ ] overworld 层: 按区域分别求解后合并
  - [ ] 其他层: 各自独立求解

## Dev Notes

### 现有 ConstraintSolver 接口

```python
# map_layout_service.py
class ConstraintSolver:
    def __init__(self, locations, constraints, user_overrides, first_chapter):
        # CANVAS_MIN, CANVAS_MAX 控制坐标范围
    def solve(self) -> list[dict]:
        # 返回 [{"name": ..., "x": ..., "y": ...}, ...]
```

需要扩展: 添加 `bounds=(x_min, y_min, x_max, y_max)` 参数，替代全局 CANVAS_MIN/MAX。

### 性能预期

| 指标 | 全局求解 | 区域求解 |
|------|---------|---------|
| 地点数 | 100-279 | 10-50 |
| 参数维度 | 200-558 | 20-100 |
| 求解时间 | 10-30s | 1-3s/区域 |

### References

- [Source: _bmad-output/world-map-v2-architecture.md#6.3-区域内约束求解]
- [Source: backend/src/services/map_layout_service.py — ConstraintSolver 类]

## Dev Agent Record

### Agent Model Used

### Completion Notes List

### File List
