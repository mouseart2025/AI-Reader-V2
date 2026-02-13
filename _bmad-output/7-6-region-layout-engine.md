# Story 7.6: 区域级布局引擎

Status: ready-for-dev

## Story

As a 系统,
I want 基于 WorldStructure 的区域划分在画布上分配区域边界框,
So that 地点布局有宏观结构而不是平铺在一个平面上。

## Acceptance Criteria

1. 区域按 cardinal_direction 映射到画布对应方位（east→右, west→左, south→下, north→上）
2. 多个区域同方位时在该象限内等分细分
3. 无区域划分时回退到当前全局约束求解布局
4. 区域边界框信息包含在 API 响应中
5. 遵循上北下南左西右东惯例

## Tasks / Subtasks

- [ ] Task 1: 实现区域布局算法 (AC: #1, #2, #5)
  - [ ] 在 `map_layout_service.py` 新增 `_layout_regions(regions, canvas_size=1000) -> dict[str, tuple]`
  - [ ] 方位 → 画布象限映射:
    ```python
    DIRECTION_ZONES = {
        "east":  (600, 200, 950, 800),   # (x1, y1, x2, y2)
        "west":  (50, 200, 400, 800),
        "south": (200, 50, 800, 350),
        "north": (200, 650, 800, 950),
        "center": (300, 300, 700, 700),
    }
    ```
  - [ ] 处理同方位冲突: 同方位区域在象限内水平或垂直等分
  - [ ] 无 cardinal_direction 的区域分配到 "center"
- [ ] Task 2: 集成到布局流程 (AC: #3, #4)
  - [ ] 在 `visualization_service.py` 中加载 WorldStructure
  - [ ] 如果有区域划分: 先调用 `_layout_regions()`，再对每个区域内的地点调用区域内求解
  - [ ] 如果无区域划分（WorldStructure 为空或仅 overworld 无 regions）: 走现有全局求解路径
  - [ ] 在地图 API 响应中添加 `region_boundaries` 字段
- [ ] Task 3: 区域边界数据结构 (AC: #4)
  - [ ] 定义 RegionBoundary 数据: region_name, color, bounds(x1,y1,x2,y2)
  - [ ] 每个区域分配一个颜色（按方位或 region_type 映射）

## Dev Notes

### 坐标系

- 画布 [0, 1000] × [0, 1000]
- +x = 东（右），+y = 北（上）
- CANVAS_MIN = 50, CANVAS_MAX = 950
- 区域边界框预留 margin，不覆盖天界/冥界非地理区域

### 现有布局流程

- `visualization_service.py` 调用 `ConstraintSolver` 获取布局
- `map_layout_service.py` 中 `ConstraintSolver` 处理全部地点
- 新方案: 先确定区域边界框 → 区域内求解时用边界框作为坐标约束

### References

- [Source: _bmad-output/world-map-v2-architecture.md#6.0-地图方位原则]
- [Source: _bmad-output/world-map-v2-architecture.md#6.2-区域布局算法]
- [Source: backend/src/services/map_layout_service.py — 现有求解器]
- [Source: backend/src/services/visualization_service.py — 布局调用]

## Dev Agent Record

### Agent Model Used

### Completion Notes List

### File List
