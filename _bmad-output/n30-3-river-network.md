# Story N30.3: 河流水系网络生成

Status: draft

## Story

As a 用户,
I want 地图上出现蜿蜒的河流水系，从山脉高地流向城镇低地,
So that 虚拟地图具备自然地理深度感，从"数据图表"升级为"史诗级奇幻地图"。

## Background

当前地形系统有两个层次：
1. 后端 `generate_terrain()` 生成静态 Voronoi + OpenSimplex 噪声 PNG 底图
2. 前端 `terrainHints.ts` 生成 SVG 装饰符号（山脉三角、波浪线、树丛等）

缺失的关键元素是**河流网络**——真实世界地图中最具辨识度的地理特征。研究报告（维度3）建议从高海拔水源地贪婪下降生成河流路径。

实现策略：在**后端**生成河流路径坐标（利用已有的 Voronoi 网格和噪声海拔场），通过 API 返回给前端，前端用 SVG `<path>` 渲染手绘风格的蓝色曲线。

## Acceptance Criteria

1. **AC-1**: 后端 `map_layout_service.py` 新增 `_generate_rivers()` 函数，基于地形海拔场生成河流路径
2. **AC-2**: 河流从高海拔区域（海拔 > 0.7）的水系类型地点附近出发，沿最大梯度下降方向流向低海拔区域
3. **AC-3**: 生成 3-8 条主要河流（基于水系地点数量），每条河流为一系列 (x, y) 坐标点序列
4. **AC-4**: 河流路径带有 OpenSimplex 噪声扰动，呈现自然蜿蜒效果（非直线）
5. **AC-5**: `get_map_data()` 返回新字段 `rivers: [{points: [[x,y],...], width: number}]`
6. **AC-6**: 前端 NovelMap 新增 `#rivers` SVG 图层（z-order：地形底图之上、领地之下），渲染贝塞尔曲线路径
7. **AC-7**: 河流样式：浅蓝色 (#6b9bc3)、stroke-width 按缩放反比缩放、stroke-linecap="round"、半透明 (opacity 0.6)
8. **AC-8**: 河流宽度随下游累积增粗（上游 1px → 下游 3px）
9. **AC-9**: 无水系地点的小说（如都市小说）不生成河流

## Tasks / Subtasks

- [ ] Task 1: 后端 — 海拔场构建
  - [ ] 1.1 在 `generate_terrain()` 或独立函数中，构建 canvas 坐标系的海拔场 `elevation_at(x, y)`，复用现有 OpenSimplex 噪声
  - [ ] 1.2 将水系类型地点（type 含"河/湖/海/泉/溪"）标记为低海拔吸引点
  - [ ] 1.3 将山系类型地点（type 含"山/峰/岭/崖"）标记为高海拔排斥点

- [ ] Task 2: 后端 — 河流路径生成
  - [ ] 2.1 新增 `_generate_rivers(locations, layout, canvas_width, canvas_height)` 函数
  - [ ] 2.2 识别水源地：高海拔区域（取海拔前 20% 的位置）或山系地点附近随机采样 3-8 个点
  - [ ] 2.3 从每个水源地开始，沿海拔梯度下降方向步进（步长 20px），记录路径点
  - [ ] 2.4 路径点叠加 OpenSimplex 横向扰动（amplitude=15px）实现蜿蜒
  - [ ] 2.5 路径到达画布边缘或低海拔阈值时终止
  - [ ] 2.6 为每条河流计算宽度权重（基于路径长度）

- [ ] Task 3: 后端 — API 集成
  - [ ] 3.1 在 `get_map_data()` 中调用 `_generate_rivers()`
  - [ ] 3.2 返回 `rivers` 字段：`[{points: [[x1,y1],[x2,y2],...], width: 1-3}]`
  - [ ] 3.3 河流数据缓存在 layout 中，避免每次请求重新计算

- [ ] Task 4: 前端 — 河流渲染
  - [ ] 4.1 `api/types.ts` 扩展 `MapData` 接口新增 `rivers?: {points: number[][], width: number}[]`
  - [ ] 4.2 NovelMap 渲染 useEffect 中新增 `#rivers` group（在 `#terrain` 之后、`#territories` 之前）
  - [ ] 4.3 每条河流渲染为 SVG `<path>`，使用 D3 `d3Shape.curveBasis` 贝塞尔曲线
  - [ ] 4.4 stroke-width 沿路径从起点到终点渐增
  - [ ] 4.5 Zoom effect 中对河流层做 counter-scale stroke-width 调整

## Dev Notes

### 海拔场计算

复用 `generate_terrain()` 中的 OpenSimplex 噪声参数，但在 canvas 坐标系而非图片坐标系中计算：

```python
def _elevation_at(x: float, y: float, noise_gen, canvas_w: int, canvas_h: int) -> float:
    # Normalize to 0-1 range, apply noise
    nx, ny = x / canvas_w, y / canvas_h
    return noise_gen.noise2(nx * 3, ny * 3) * 0.5 + 0.5
```

### 梯度下降步进

```python
def _trace_river(start_x, start_y, elevation_fn, noise_gen, step=20):
    path = [(start_x, start_y)]
    x, y = start_x, start_y
    for _ in range(200):  # max steps
        # Sample 8 neighbors
        best_x, best_y, best_e = x, y, elevation_fn(x, y)
        for dx, dy in [(-1,0),(1,0),(0,-1),(0,1),(-1,-1),(1,1),(-1,1),(1,-1)]:
            nx, ny = x + dx*step, y + dy*step
            e = elevation_fn(nx, ny)
            if e < best_e:
                best_x, best_y, best_e = nx, ny, e
        if best_x == x and best_y == y:
            break  # local minimum
        # Add lateral wiggle
        wiggle = noise_gen.noise2(best_x * 0.01, best_y * 0.01) * 15
        path.append((best_x + wiggle, best_y))
        x, y = best_x, best_y
    return path
```

### 渲染 z-order

```
parchment bg → terrain PNG → rivers → territories → regions → locations → labels
```
