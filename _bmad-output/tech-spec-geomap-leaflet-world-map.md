---
title: 'GeoMap — Leaflet 真实世界地图渲染'
slug: 'geomap-leaflet-world-map'
created: '2026-02-16'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['React 19', 'TypeScript 5.9', 'react-leaflet v5', 'leaflet v1.9', 'FastAPI', 'Python 3.9+']
files_to_modify:
  - frontend/src/components/visualization/GeoMap.tsx
  - frontend/src/pages/MapPage.tsx
  - frontend/src/api/types.ts
  - backend/src/services/visualization_service.py
code_patterns:
  - 'MapPage 轨迹状态管理: selectedPerson / visibleTrajectory / playIndex / currentLocation'
  - 'NovelMap props 接口: locations + layout + trajectoryPoints + onLocationClick'
  - 'EntityCard 交互: openEntityCard(name, "location")'
  - 'API 响应构建: visualization_service.py L671-682 的 result dict'
test_patterns: []
---

# Tech-Spec: GeoMap — Leaflet 真实世界地图渲染

**Created:** 2026-02-16

## Overview

### Problem Statement

地理写实题材小说（《八十天环游地球》《水浒传》等）的地图在空白画布上通过墨卡托投影渲染，缺乏海岸线、国境线等地理参照物，用户无法建立地理方位感。当前 `NovelMap.tsx`（1206 行，D3+SVG）是为幻想/虚构空间布局设计的，不适合真实地理场景。

### Solution

新建 `GeoMap.tsx` 组件，使用 react-leaflet 在 CartoDB Positron 瓦片底图上直接用 raw lat/lng 渲染地名标记和角色轨迹路线。`MapPage.tsx` 根据 `layout_mode` 分支渲染。后端在 geographic 模式下额外返回 `geo_coords` 字段（raw 经纬度）。

### Scope

**In Scope:**
- 后端返回 `geo_coords: dict[str, {lat, lng}]`（raw 经纬度）
- 新建 `GeoMap.tsx` 组件（瓦片底图 + CircleMarker + 轨迹 Polyline + 弹窗 + fitBounds）
- `MapPage.tsx` 按 layout_mode 分支：geographic → GeoMap，其他 → NovelMap
- 复用现有轨迹动画逻辑（selectedPerson / visibleTrajectory / playback）
- 复用 EntityCard 点击交互（openEntityCard）

**Out of Scope:**
- 不修改 NovelMap.tsx
- 不改幻想题材渲染逻辑
- 暂不做离线瓦片缓存
- 暂不做自定义瓦片源配置 UI
- 暂不做地名拖拽重定位（geographic 模式下坐标来自 GeoNames，不可拖）

## Context for Development

### Codebase Patterns

1. **MapPage 轨迹状态管理**（`MapPage.tsx:65-173`）：
   - `selectedPerson` / `playing` / `playIndex` 管理动画状态
   - `visibleTrajectory` = `selectedTrajectory.slice(0, playIndex + 1)` 实现逐步揭露
   - `currentLocation` = 最后一个 trajectory point 的 location
   - `startPlay()` / `stopPlay()` 控制 800ms interval
   - **GeoMap 可以直接复用这些状态**，只需通过 props 传入

2. **NovelMap 调用方式**（`MapPage.tsx:327-346`）：
   - Props: `locations`, `layout`, `layoutMode`, `trajectoryPoints`, `currentLocation`, `onLocationClick`, `onLocationDragEnd`
   - `onLocationClick` → `openEntityCard(name, "location")`
   - `onLocationDragEnd` → `saveLocationOverride()` — GeoMap 不需要这个

3. **API 响应结构**（`visualization_service.py:671-682`）：
   - `result` dict 包含 `locations`, `trajectories`, `layout`, `layout_mode`, `terrain_url` 等
   - `geo_coords` 需要作为新字段添加到这个 dict
   - `resolved` 变量已存在于 geographic 分支（L553），直接转换格式即可

4. **MapData 类型**（`types.ts:313-329`）：
   - `layout_mode` 已包含 `"geographic"`
   - 需新增 `geo_coords?: Record<string, { lat: number; lng: number }>`

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `frontend/src/pages/MapPage.tsx` | 地图页面主组件，轨迹状态管理，分支渲染入口 |
| `frontend/src/components/visualization/NovelMap.tsx` | 现有 D3+SVG 地图（参考 props 接口，不修改） |
| `frontend/src/api/types.ts:313-329` | MapData 接口定义 |
| `frontend/src/api/client.ts:191` | fetchMapData API 调用 |
| `backend/src/services/visualization_service.py:543-682` | geographic 布局分支 + API 响应构建 |
| `backend/src/services/geo_resolver.py` | GeoResolver，`resolved` 变量包含 raw lat/lng |

### Technical Decisions

1. **瓦片底图选择 CartoDB Positron** — 浅色、标注少、适合叠加自定义标记。URL: `https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png`
2. **CircleMarker 而非 Marker** — 不需要图标资源，通过 radius 和颜色区分地点重要性
3. **不传 layout[] 给 GeoMap** — GeoMap 使用 geo_coords 的 raw lat/lng，不需要 canvas 投影坐标
4. **轨迹用 Polyline + 渐变色** — 按时间顺序从浅到深，配合 animated dash offset
5. **Leaflet CSS 导入** — 在 GeoMap.tsx 内 `import "leaflet/dist/leaflet.css"`

## Implementation Plan

### Tasks

- [ ] Task 1: 安装前端依赖
  - Action: `cd frontend && npm install react-leaflet leaflet @types/leaflet`
  - Notes: react-leaflet v5 要求 React 18+（我们是 React 19，兼容）

- [ ] Task 2: 后端返回 geo_coords
  - File: `backend/src/services/visualization_service.py`
  - Action: 在 geographic 分支中，将 `resolved` dict 转换为 `{name: {"lat": lat, "lng": lng}}` 格式，添加到 `result` dict
  - Notes: `resolved` 变量类型为 `dict[str, tuple[float, float]]`，在 L553 已有。在 L671 的 result 构建处，当 `layout_mode == "geographic"` 时添加 `"geo_coords": {name: {"lat": c[0], "lng": c[1]} for name, c in resolved.items()}`

- [ ] Task 3: 前端 MapData 类型扩展
  - File: `frontend/src/api/types.ts`
  - Action: 在 `MapData` 接口中新增 `geo_coords?: Record<string, { lat: number; lng: number }>`
  - Notes: 放在 `geography_context` 之前

- [ ] Task 4: 新建 GeoMap 组件
  - File: `frontend/src/components/visualization/GeoMap.tsx` (NEW)
  - Action: 创建 react-leaflet 地图组件
  - 具体实现:
    - Props: `locations: MapLocation[]`, `geoCoords: Record<string, {lat, lng}>`, `trajectoryPoints?: TrajectoryPoint[]`, `currentLocation?: string | null`, `onLocationClick?: (name: string) => void`
    - MapContainer: `center={[30, 105]}` (中国中心) 或通过 fitBounds 自动
    - TileLayer: CartoDB Positron
    - CircleMarker: 遍历 locations，从 geoCoords 取坐标，radius 按 mention_count 缩放（min 5, max 20），颜色用现有 location type 配色
    - Tooltip: 显示地名
    - Popup: 显示地名 + type + parent + mention_count，包含「查看详情」按钮触发 onLocationClick
    - Polyline: 从 trajectoryPoints 取 location → 查 geoCoords 得坐标 → 绘制折线
    - FitBoundsOnMount: 自定义 hook，初始化时 fitBounds 到所有有坐标的 marker
    - 未匹配坐标的 location 不渲染（它们没有 geo_coords 条目）

- [ ] Task 5: MapPage 分支渲染
  - File: `frontend/src/pages/MapPage.tsx`
  - Action: 在 L327-347 的 NovelMap 渲染区域，增加条件分支：
    ```tsx
    {layoutMode === "geographic" && mapData?.geo_coords ? (
      <GeoMap
        locations={locations}
        geoCoords={mapData.geo_coords}
        trajectoryPoints={visibleTrajectory}
        currentLocation={currentLocation}
        onLocationClick={handleLocationClick}
      />
    ) : (
      <NovelMap ... />
    )}
    ```
  - Notes:
    - geographic 模式下隐藏图例（图例是 NovelMap 的 fantasy icon 系统）
    - geographic 模式下隐藏 "空间约束不足" 提示
    - 保留右侧轨迹面板（状态管理已在 MapPage 中，GeoMap 只消费 props）
    - 保留 toast、GeographyPanel 等 overlay

- [ ] Task 6: 验证 + 样式微调
  - Action: 启动后端+前端，选择写实题材小说测试
  - 验证项: 瓦片加载、标记定位、轨迹绘制、点击交互、fitBounds

### Acceptance Criteria

- [ ] AC 1: Given 一本已分析的写实题材小说（geo_type 为 realistic 或 mixed），When 打开地图页，Then 显示 Leaflet 瓦片底图（CartoDB Positron），地名标记显示在真实地理位置上
- [ ] AC 2: Given 地图页显示 geographic 模式，When 点击某个地名标记，Then 弹出 Popup 显示地名信息，点击「查看详情」打开 EntityCard 抽屉
- [ ] AC 3: Given 地图页右侧轨迹面板选中一个角色，When 点击播放，Then Leaflet 地图上显示该角色的旅行路线折线，逐步扩展
- [ ] AC 4: Given 一本幻想题材小说（geo_type 为 fantasy），When 打开地图页，Then 仍然使用 NovelMap（D3+SVG）渲染，不显示 Leaflet
- [ ] AC 5: Given geographic 模式下的地图，When 缩放/拖拽地图，Then Leaflet 原生交互正常工作
- [ ] AC 6: Given 后端返回 geo_coords，When 某些地名未被 GeoResolver 匹配，Then 这些地名不在 Leaflet 上渲染（不显示错误位置的标记）

## Additional Context

### Dependencies

- `react-leaflet` v5 — React wrapper for Leaflet
- `leaflet` v1.9 — 核心地图库
- `@types/leaflet` — TypeScript 类型定义
- CartoDB Positron 瓦片服务 — 需要网络连接

### Testing Strategy

手动验证流程：
1. 选择《八十天环游地球》或类似写实小说
2. 确认后端日志显示 `GeoResolver[world]: geo_type=realistic`
3. 打开地图页，确认 Leaflet 底图渲染
4. 确认伦敦在欧洲西北、孟买在南亚、香港在东亚等地理位置正确
5. 选择角色轨迹，确认路线折线沿真实地理路径绘制
6. 切换到幻想小说，确认仍然使用 NovelMap

### Notes

- Leaflet CSS 必须导入，否则瓦片显示异常（常见坑）
- MapContainer 的 `style` 必须设置 `height: "100%"`，否则地图不显示
- react-leaflet v5 使用 hooks API（`useMap()` 等），不再使用 class components
- 未来可扩展：自定义瓦片源（暗色主题、卫星图）、离线瓦片包、GeoJSON 国境叠加
