# Story N30.4: 二维生物群落矩阵 + Lloyd 松弛地形优化

Status: draft

## Story

As a 用户,
I want 地形底图展现更丰富的自然景观——草原、冻原、雨林、沼泽等不同气候带，而非现在粗糙的单一着色,
So that 虚拟地图拥有专业级地理制图的视觉品质，每块区域都能体现气候特征和地貌差异。

## Background

当前 `generate_terrain()` 的着色逻辑非常简单：
1. 每个地点匹配一个 `_BIOME_COLORS` 颜色（6个关键词组 + 默认灰绿）
2. Voronoi 区域直接填充该颜色
3. OpenSimplex 噪声仅用于边界扰动和色彩变化

研究报告（维度3）建议引入**惠特克生物群落矩阵**——以海拔（elevation）和湿度（moisture）两个维度确定 biome 类型。现有系统已有海拔场（OpenSimplex 噪声），缺少湿度场和二维查询矩阵。

另一个改进是 **Lloyd 松弛**——对 Voronoi 种子点做 2-3 轮质心迭代，使单元格形状更规则、视觉更和谐。当前系统直接用地点坐标作为种子点，点分布不均匀时会产生极细长或极大的单元格。

## Acceptance Criteria

1. **AC-1**: 新增湿度场 `_moisture_at(x, y)`，使用独立种子的 OpenSimplex 噪声，与海拔场正交
2. **AC-2**: 水系类型地点周围湿度值提升（径向衰减叠加），山系类型地点周围湿度值降低
3. **AC-3**: 新增 `_WHITTAKER_MATRIX` 二维查询表，至少包含 8 种 biome：雪山、岩地、针叶林、温带森林、热带雨林、草原/稀树草原、沙漠、沼泽/湿地
4. **AC-4**: 每个 Voronoi 单元格的颜色由其质心的 (elevation, moisture) 决定，通过矩阵查询获取 biome 颜色
5. **AC-5**: 对 Voronoi 种子点执行 2 轮 Lloyd 松弛（质心迭代），种子点数量从 locations 扩展到 `max(len(locations), 30)` 个（补充随机种子点填充空白区域）
6. **AC-6**: Lloyd 松弛中，已有地点的种子点移动范围受限（最大偏移 30px），防止地点偏离其标注位置过远
7. **AC-7**: 生成的地形 PNG 在视觉上呈现平滑的 biome 过渡带，而非硬边界
8. **AC-8**: 地形生成耗时不超过当前的 2 倍（当前约 0.5-1 秒）
9. **AC-9**: 保留与现有 `terrainHints.ts` 的兼容性——前端地形纹理符号继续叠加在新底图之上

## Tasks / Subtasks

- [ ] Task 1: 湿度场构建
  - [ ] 1.1 新增 `_moisture_at(x, y, noise_gen_moisture, canvas_w, canvas_h)` 函数
  - [ ] 1.2 使用独立种子的 OpenSimplex 实例（`seed = hash(novel_id + "moisture") % 2**31`）
  - [ ] 1.3 水系类型地点周围叠加正向湿度（高斯径向衰减，半径 = canvas_size * 0.15）
  - [ ] 1.4 山系类型地点周围叠加负向湿度（雨影效应模拟）

- [ ] Task 2: Whittaker 生物群落矩阵
  - [ ] 2.1 定义 `_WHITTAKER_MATRIX`：elevation (0-1) × moisture (0-1) → biome color
  - [ ] 2.2 矩阵分区：
    - elevation > 0.85 → 雪山 (250, 248, 245)
    - elevation > 0.7, moisture < 0.3 → 岩地/高地 (165, 145, 125)
    - elevation > 0.7, moisture > 0.3 → 针叶林 (95, 120, 90)
    - elevation 0.4-0.7, moisture < 0.25 → 沙漠/荒地 (190, 170, 140)
    - elevation 0.4-0.7, moisture 0.25-0.6 → 草原 (145, 165, 120)
    - elevation 0.4-0.7, moisture > 0.6 → 温带森林 (110, 140, 100)
    - elevation < 0.4, moisture < 0.3 → 稀树草原 (170, 165, 130)
    - elevation < 0.4, moisture 0.3-0.7 → 温带森林/平原 (130, 155, 115)
    - elevation < 0.4, moisture > 0.7 → 沼泽/湿地 (100, 120, 95)
  - [ ] 2.3 颜色查询使用双线性插值，确保 biome 之间平滑过渡
  - [ ] 2.4 保留 parchment tint（15% cream blend），使新颜色与羊皮纸整体风格一致

- [ ] Task 3: Lloyd 松弛优化
  - [ ] 3.1 在 Voronoi 计算前，补充随机种子点至 `max(n_locations, 30)` 个
  - [ ] 3.2 实现 `_lloyd_relax(points, img_w, img_h, iterations=2)` 函数
  - [ ] 3.3 每轮迭代：计算每个 Voronoi 单元格的质心，将种子移向质心
  - [ ] 3.4 地点种子的移动距离 clamped to 30px（`np.clip(delta, -30, 30)`）
  - [ ] 3.5 补充种子不受 clamp 限制，允许自由移动

- [ ] Task 4: 集成到 generate_terrain()
  - [ ] 4.1 替换现有 `_biome_for_type()` 着色逻辑为 Whittaker 矩阵查询
  - [ ] 4.2 仍保留地点类型作为湿度修正因子（水系 → 湿度+0.3，山系 → 高海拔偏置+0.2）
  - [ ] 4.3 保留现有的 multi-octave 色彩变化、paper grain、boundary darkening 效果
  - [ ] 4.4 确保生成的 PNG 路径和格式不变，前端无需修改

## Dev Notes

### Whittaker 矩阵查询（双线性插值）

```python
def _biome_color_at(elevation: float, moisture: float) -> tuple[int, int, int]:
    """查询 Whittaker 矩阵获取 biome 颜色，使用双线性插值实现平滑过渡。"""
    # Clamp to [0, 1]
    e = max(0.0, min(1.0, elevation))
    m = max(0.0, min(1.0, moisture))

    # 找到最近的 4 个 biome 区域，按距离加权混合颜色
    # 简化实现：定义 5x5 网格点的颜色，双线性插值
    grid_e = int(e * 4)  # 0-4
    grid_m = int(m * 4)  # 0-4
    # ... interpolate between 4 nearest grid points
```

### Lloyd 松弛

```python
def _lloyd_relax(points: np.ndarray, w: int, h: int, iters: int = 2,
                 fixed_mask: np.ndarray | None = None,
                 max_shift: float = 30.0) -> np.ndarray:
    from scipy.spatial import Voronoi
    pts = points.copy()
    for _ in range(iters):
        vor = Voronoi(pts)
        for i, region_idx in enumerate(vor.point_region):
            region = vor.regions[region_idx]
            if -1 in region or len(region) == 0:
                continue
            verts = vor.vertices[region]
            centroid = verts.mean(axis=0)
            delta = centroid - pts[i]
            if fixed_mask is not None and fixed_mask[i]:
                delta = np.clip(delta, -max_shift, max_shift)
            pts[i] += delta
    return np.clip(pts, [0, 0], [w-1, h-1])
```

### 性能保证

当前地形生成约 0.5-1秒。新增计算：
- 湿度场：与海拔场共享稀疏采样+插值策略，增加约 20%
- Lloyd 松弛：2 轮 scipy.spatial.Voronoi，~50 个点，毫秒级
- Whittaker 查询：替换原有 `_biome_for_type()`，复杂度相同
- 总增长估计 < 50%，符合 AC-8 要求
