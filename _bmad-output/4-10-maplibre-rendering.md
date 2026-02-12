# Story 4.10: MapLibre GL JS 世界地图渲染

Status: ready-for-dev
Depends on: Story 4.9

## Story

As a 用户,
I want 在浏览器中看到一个交互式世界地图，地点位置稳定合理，可以查看地点详情、人物轨迹动画和随阅读进度揭示的区域,
So that 我能直观地理解小说世界的空间结构和角色动线。

## Acceptance Criteria

1. **Given** 地图数据已加载（含 layout 坐标）
   **When** 世界地图页面渲染
   **Then** 使用 MapLibre GL JS 渲染地图：
   - 地形底图为后端生成的 terrain.png（通过 `L.CRS.Simple` 风格的自定义坐标系）
   - 地点以 Marker 标注在计算坐标上，图标按类型区分（城市/山/水/宗门等）
   - 地点名称标签在缩放时渐进显示（高提及数的优先）
   - 支持平移、缩放交互

2. **Given** 地图已渲染
   **When** 用户点击某个地点 Marker
   **Then** 弹出 Popup 显示：地点名称、类型、描述、提及章节数
   **And** 提供"查看卡片"按钮打开 EntityCardDrawer

3. **Given** 地图已渲染
   **When** 用户在右侧面板选择一个人物
   **Then** 在地图上绘制该人物的移动轨迹：
   - 用折线（Polyline）连接途经地点，颜色渐变表示时间先后
   - 每段折线标注章节号
   - 支持"播放"按钮逐段动画显示轨迹

4. **Given** layout_mode 为 "hierarchy"（约束不足退化）
   **When** 地图页渲染
   **Then** 显示提示"空间约束不足，使用层级布局"
   **And** 以同心圆布局展示地点（根节点在中心），而非地形底图

5. **Given** 地图已渲染
   **When** 用户长按（>0.5s）并拖拽某个地点 Marker
   **Then** Marker 移动到新位置，松手后保存新坐标到 user_overrides
   **And** 提示"位置已保存，下次刷新地图将以此为锚定"

6. **Given** 章节范围滑轨变化
   **When** 范围缩小
   **Then** 未在该范围内出现的地点半透明化（战争迷雾效果）
   **And** 首次出现在范围内的地点有"揭示"动画

## Tasks / Subtasks

- [ ] Task 1: 安装 MapLibre GL JS 并替换现有地图 (AC: #1)
  - [ ] 1.1 `npm install maplibre-gl`，确认与 React 19 兼容
  - [ ] 1.2 创建 `frontend/src/components/visualization/NovelMap.tsx` 组件
  - [ ] 1.3 使用 `maplibregl.Map` 配合自定义坐标系（无经纬度，纯像素坐标）
  - [ ] 1.4 加载后端 terrain.png 作为底图 ImageSource
  - [ ] 1.5 退化模式（无 terrain）时使用纯色背景 + 同心圆布局

- [ ] Task 2: 渲染地点 Markers 和标签 (AC: #1, #2)
  - [ ] 2.1 为每个 location 创建 Marker（icon 按 type 区分颜色/形状）
  - [ ] 2.2 标签渐进显示逻辑：zoom < 2 只显示 mention_count > 10 的，zoom > 4 显示全部
  - [ ] 2.3 点击 Marker 弹出 Popup（名称、类型、描述、章节数、"查看卡片"按钮）
  - [ ] 2.4 Popup 内"查看卡片"按钮调用 `openEntityCard(name, 'location')`

- [ ] Task 3: 人物轨迹渲染和动画 (AC: #3)
  - [ ] 3.1 右侧面板列出人物列表（按轨迹点数排序），复用现有面板设计
  - [ ] 3.2 选中人物后，在 Map 上添加 GeoJSON LineString 图层绘制轨迹
  - [ ] 3.3 折线颜色使用章节号映射的渐变色（起始=浅色，结束=深色）
  - [ ] 3.4 每段折线节点标注章节号（小标签）
  - [ ] 3.5 "播放"按钮：使用 `requestAnimationFrame` 逐段显示折线 + 移动 marker 动画
  - [ ] 3.6 播放控制栏：播放/暂停、进度滑块、速度调节

- [ ] Task 4: 手动调整地点位置 (AC: #5)
  - [ ] 4.1 Marker 添加 `draggable: true`（默认禁用，需长按 0.5s 启用）
  - [ ] 4.2 拖拽结束后调用 `PUT /api/novels/{id}/map/layout/{location_name}` 保存
  - [ ] 4.3 显示 toast 提示"位置已保存"

- [ ] Task 5: 战争迷雾 / 渐进揭示 (AC: #6)
  - [ ] 5.1 根据 chapterRangeStore 的范围，计算哪些地点在范围内出现过
  - [ ] 5.2 未出现的地点 Marker 设为 opacity=0.2，标签隐藏
  - [ ] 5.3 范围变化时，新揭示的地点播放渐入动画（opacity 0.2 → 1.0）

- [ ] Task 6: 重写 MapPage (AC: all)
  - [ ] 6.1 替换 `frontend/src/pages/MapPage.tsx` 中的 react-force-graph-2d 为 NovelMap 组件
  - [ ] 6.2 保持 VisualizationLayout 包裹（章节范围滑轨）
  - [ ] 6.3 保持右侧人物轨迹面板
  - [ ] 6.4 更新 `frontend/src/api/types.ts` 新增 MapLayout 类型
  - [ ] 6.5 更新 `frontend/src/api/client.ts` 的 fetchMapData 类型定义

## Dev Notes

### MapLibre GL JS 自定义坐标系

MapLibre GL JS 原生使用经纬度（WGS84），但我们的虚构地图没有经纬度。两种方案：

**方案 A（推荐）：伪经纬度映射**
将 [0, 1000] 的画布坐标映射到一个小经纬度范围（如 [0, 0] ~ [0.009, 0.009]），利用 MapLibre 原生功能。在这个小范围内墨卡托投影近似等距。

```typescript
// 画布坐标 → 伪经纬度
const scale = 0.009 / 1000  // 约 1km 范围
const toLngLat = (x: number, y: number): [number, number] => [x * scale, y * scale]
```

**方案 B：使用 ImageOverlay**
将 terrain.png 作为 RasterSource 加载，Marker 用 `addTo(map)` 按像素位置定位。

推荐方案 A，因为它保留了 MapLibre 的所有原生交互（缩放、旋转、倾斜）和图层功能。

### 类型颜色方案（沿用现有）

```typescript
const TYPE_ICONS: Record<string, { color: string; icon: string }> = {
  // 大区域
  "界": { color: "#3b82f6", icon: "region" },
  "域": { color: "#3b82f6", icon: "region" },
  "国": { color: "#3b82f6", icon: "region" },
  // 聚落
  "城市": { color: "#10b981", icon: "city" },
  "城": { color: "#10b981", icon: "city" },
  "镇": { color: "#22c55e", icon: "town" },
  "村": { color: "#22c55e", icon: "town" },
  // 自然地貌
  "山": { color: "#84cc16", icon: "mountain" },
  "洞": { color: "#84cc16", icon: "cave" },
  "谷": { color: "#84cc16", icon: "valley" },
  "林": { color: "#84cc16", icon: "forest" },
  // 门派
  "宗": { color: "#8b5cf6", icon: "sect" },
  "门": { color: "#8b5cf6", icon: "sect" },
  "派": { color: "#8b5cf6", icon: "sect" },
  // 水域
  "河": { color: "#06b6d4", icon: "water" },
  "湖": { color: "#06b6d4", icon: "water" },
  "海": { color: "#06b6d4", icon: "water" },
}
```

### 前端依赖

```json
{
  "maplibre-gl": "^4.x"
}
```

可移除 `react-force-graph-2d`，但注意 GraphPage 和 FactionsPage 仍在使用它，不可删除。MapPage 是唯一切换到 MapLibre 的页面。

### 关键文件变更

| 文件 | 操作 | 说明 |
|------|------|------|
| `frontend/src/pages/MapPage.tsx` | 重写 | 移除 force-graph，使用 NovelMap |
| `frontend/src/components/visualization/NovelMap.tsx` | 新建 | MapLibre 地图组件 |
| `frontend/src/api/types.ts` | 修改 | 新增 MapLayout 类型 |
| `frontend/src/api/client.ts` | 修改 | 更新 fetchMapData 返回类型 |
| `frontend/package.json` | 修改 | 添加 maplibre-gl 依赖 |
| `frontend/src/app.css` 或全局样式 | 修改 | 引入 maplibre-gl/dist/maplibre-gl.css |

### 性能考虑

- 地点数量典型 50-200 个，MapLibre Marker 方式完全可行
- 超过 500 个地点时考虑换用 Symbol Layer（GPU 渲染）
- terrain.png 建议 2048x2048 分辨率，作为单张 Image Source

### References

- [Source: maplibre.org/maplibre-gl-js/docs] MapLibre GL JS 文档
- [Source: frontend/src/pages/MapPage.tsx] 现有地图页面（将被重写）
- [Source: frontend/src/components/visualization/VisualizationLayout.tsx] 章节范围滑轨布局
- [Source: frontend/src/stores/entityCardStore.ts] 实体卡片状态管理
