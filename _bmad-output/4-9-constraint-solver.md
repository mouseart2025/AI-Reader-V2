# Story 4.9: 约束求解布局引擎

Status: ready-for-dev
Depends on: Story 4.8

## Story

As a 系统,
I want 基于提取的空间约束，通过约束满足算法计算所有地点的 (x, y) 坐标,
So that 世界地图上的地点位置稳定、符合小说描述的空间关系。

## Acceptance Criteria

1. **Given** 小说已有空间约束数据（来自 Story 4.8）
   **When** 调用布局引擎
   **Then** 为每个地点计算 (x, y) 坐标，坐标满足以下约束：
   - 方位约束：若 A 在 B 以北，则 A.y > B.y
   - 距离约束：相关地点的欧氏距离与描述成正比
   - 包含约束：子地点坐标在父地点的凸包范围内
   - 相邻约束：相邻地点距离较近
   - 分隔约束：被山脉/河流分隔的地点在分隔线两侧

2. **Given** 约束不足（少于 3 条空间关系）
   **When** 调用布局引擎
   **Then** 退化为层级树布局（现有方案），并在 API 响应中标记 `layout_mode: "hierarchy"`

3. **Given** 约束存在冲突（如 A 在 B 北边，同时 B 在 A 北边）
   **When** 求解
   **Then** 以 confidence 高的约束优先，丢弃冲突约束，日志记录冲突

4. **Given** 布局已计算
   **When** 调用 `GET /api/novels/{id}/map`
   **Then** 响应新增：
   - `layout`: `[{ name, x, y, radius }]` — 每个地点的坐标和影响半径
   - `layout_mode`: `"constraint"` | `"hierarchy"` — 布局模式
   - `terrain_tiles_url`: 地形瓦片 URL 模板（如果启用地形生成）

5. **Given** 用户手动拖拽调整了某地点位置
   **When** 保存调整
   **Then** 用户覆盖坐标存入数据库，后续布局计算以用户坐标为锚定点

## Tasks / Subtasks

- [ ] Task 1: 实现约束求解器核心 (AC: #1, #3)
  - [ ] 1.1 新建 `backend/src/services/map_layout_service.py`
  - [ ] 1.2 实现 `ConstraintSolver` 类：
    - 输入：locations 列表 + spatial_constraints 列表
    - 输出：`dict[str, tuple[float, float]]` 地点名→坐标映射
  - [ ] 1.3 使用 scipy.optimize 的 `differential_evolution` 或 `minimize`（模拟 CMA-ES）：
    - 定义能量函数 = 方位违反惩罚 + 距离误差 + 包含违反 + 分隔违反
    - 每个地点 2 个变量 (x, y)，总变量数 = 2N
    - 约束坐标范围 [0, 1000]（归一化画布）
  - [ ] 1.4 能量函数各项设计：
    - `E_direction`: 若 relation=north_of(A,B)，惩罚 max(0, B.y - A.y + margin)
    - `E_distance`: 若 relation=near(A,B)，惩罚 (dist(A,B) - target_dist)^2
    - `E_contains`: 若 relation=contains(A,B)，惩罚 dist(A,B) > A.radius
    - `E_separated`: 若 relation=separated_by(A,B,mountain)，要求 dist(A,B) > separation_dist
    - `E_overlap`: 所有节点对的最小间距惩罚，防止重叠
  - [ ] 1.5 处理约束冲突：求解前检测并移除冲突约束（按 confidence 优先级）

- [ ] Task 2: 实现退化和缓存策略 (AC: #2)
  - [ ] 2.1 约束不足时退化为圆形层级布局（root 在中心，子节点围绕父节点）
  - [ ] 2.2 布局结果缓存到数据库（新表 `map_layouts`），按 novel_id + chapter_range hash 缓存
  - [ ] 2.3 ChapterFact 更新时失效对应 novel_id 的布局缓存

- [ ] Task 3: 简易地形生成 (AC: #4)
  - [ ] 3.1 基于计算坐标生成 Voronoi 区域划分（每个地点一个区域）
  - [ ] 3.2 根据地点 type 为 Voronoi 区域分配生物群落颜色：
    - 山/峰/岭 → 棕色山地
    - 河/湖/海 → 蓝色水域
    - 林/森 → 绿色森林
    - 城/镇/村 → 浅黄色聚落
    - 默认 → 浅绿平原
  - [ ] 3.3 叠加 Perlin 噪声生成自然边界（使用 `noise` PyPI 包）
  - [ ] 3.4 输出为 PNG 图片，供前端作为底图使用
  - [ ] 3.5 地形图存储在 `~/.ai-reader-v2/maps/{novel_id}/terrain.png`

- [ ] Task 4: 更新 API 和数据库 (AC: #4, #5)
  - [ ] 4.1 新建 `map_layouts` 表：`(novel_id, chapter_hash, layout_json, layout_mode, terrain_path, created_at)`
  - [ ] 4.2 新建 `map_user_overrides` 表：`(novel_id, location_name, x, y, updated_at)`
  - [ ] 4.3 更新 `get_map_data` 返回值，加入 layout/layout_mode/terrain 字段
  - [ ] 4.4 新增 `PUT /api/novels/{id}/map/layout/{location_name}` — 保存用户手动调整坐标
  - [ ] 4.5 布局计算时优先使用 user_overrides 中的坐标作为固定锚点

## Dev Notes

### 算法选择

PlotMap 使用 CMA-ES（协方差矩阵自适应进化策略），但其完整实现较复杂。我们的简化方案：

1. **首选**: `scipy.optimize.differential_evolution` — 全局优化，无需梯度，适合非凸问题
2. **备选**: `scipy.optimize.minimize(method='L-BFGS-B')` — 如果约束函数可微分，速度更快
3. **最简**: 先用力导向模拟（但加上方向约束力），收敛后固定坐标 — 最容易实现

推荐先用 **differential_evolution**，如果性能不够再简化。对于 50-200 个地点（典型小说规模），求解时间应在秒级。

### 能量函数伪代码

```python
def energy(coords_flat, locations, constraints):
    coords = coords_flat.reshape(-1, 2)  # (N, 2)
    E = 0.0

    for c in constraints:
        i, j = loc_index[c.source], loc_index[c.target]
        if c.relation_type == "direction":
            # north_of: A.y should > B.y
            if c.value == "north_of":
                E += max(0, coords[j, 1] - coords[i, 1] + 50) ** 2
        elif c.relation_type == "distance":
            dist = np.linalg.norm(coords[i] - coords[j])
            target = parse_distance(c.value)  # "三天路程" → 150
            E += (dist - target) ** 2
        elif c.relation_type == "contains":
            dist = np.linalg.norm(coords[i] - coords[j])
            E += max(0, dist - parent_radius) ** 2

    # Anti-overlap: minimum spacing
    for i in range(N):
        for j in range(i+1, N):
            dist = np.linalg.norm(coords[i] - coords[j])
            E += max(0, 30 - dist) ** 2  # min 30px apart

    return E
```

### 旅行时间→距离转换（来自文档 2 的建议）

```python
SPEED = {
    "步行": 30,    # km/day
    "骑马": 60,    # km/day
    "飞行": 200,   # km/day (仙侠小说)
    "传送": 0,     # instant
}
TERRAIN_FACTOR = {
    "平原": 1.0,
    "森林": 0.6,
    "山地": 0.3,
    "沼泽": 0.3,
}
# "在森林中步行了三天" → 3 * 30 * 0.6 = 54 km → 映射为画布上 54 单位
```

### 依赖库

- `scipy` — 已在项目依赖中（如未安装则需 `uv add scipy`）
- `noise` — Perlin 噪声生成（`uv add noise`）
- `Pillow` — PNG 图片生成（已在依赖中）
- `numpy` — 数值计算（已在依赖中）

### 数据库 Schema

```sql
CREATE TABLE IF NOT EXISTS map_layouts (
    novel_id TEXT NOT NULL,
    chapter_hash TEXT NOT NULL,
    layout_json TEXT NOT NULL,
    layout_mode TEXT NOT NULL DEFAULT 'hierarchy',
    terrain_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (novel_id, chapter_hash),
    FOREIGN KEY (novel_id) REFERENCES novels(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS map_user_overrides (
    novel_id TEXT NOT NULL,
    location_name TEXT NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (novel_id, location_name),
    FOREIGN KEY (novel_id) REFERENCES novels(id) ON DELETE CASCADE
);
```

### Project Structure Notes

- 新文件 `backend/src/services/map_layout_service.py` — 约束求解器
- 修改 `backend/src/services/visualization_service.py` — 调用求解器
- 修改 `backend/src/db/sqlite_db.py` — 新增两张表的建表语句
- 修改 `backend/src/api/routes/map.py` — 新增用户覆盖 API

### References

- [Source: PlotMap CMA-ES] github.com/AutodeskAILab/PlotMap — 12 种约束类型和进化优化
- [Source: scipy.optimize.differential_evolution] 全局优化算法
- [Source: 文档2 旅行时间转换] D = T x V x M_terrain 公式
- [Source: backend/src/services/visualization_service.py#L132-199] 现有 get_map_data
