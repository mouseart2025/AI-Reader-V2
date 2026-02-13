# Story 7.9: 前端类型更新与 Tab 切换 UI

Status: ready-for-dev

## Story

As a 用户,
I want 在地图页面通过 Tab 栏切换不同地图层（主世界/天界/冥界/副本）,
So that 我可以浏览小说世界的不同空间层。

## Acceptance Criteria

1. 地图上方显示 Tab 栏，列出所有已解锁的地图层
2. 默认显示 overworld（主世界）层
3. 点击 Tab 切换到对应层的地图数据
4. 背景色根据层类型变化（celestial=深蓝金色, underworld=暗紫, instance=洞穴色调）
5. 无活动的层 Tab 灰色禁用

## Tasks / Subtasks

- [ ] Task 1: 更新前端类型定义 (AC: #1-#5)
  - [ ] 在 `frontend/src/api/types.ts` 中新增:
    ```typescript
    type LayerType = "overworld" | "celestial" | "underworld" | "underwater" | "instance" | "pocket"

    interface MapLayerInfo {
      layer_id: string
      name: string
      layer_type: LayerType
      location_count: number
      is_unlocked: boolean
    }

    interface PortalInfo {
      name: string
      source_layer: string
      source_location: string
      target_layer: string
      target_layer_name: string
      first_chapter: number
    }

    interface RegionBoundary {
      region_name: string
      color: string
      bounds: { x1: number; y1: number; x2: number; y2: number }
    }

    // 扩展现有 MapData
    interface MapData {
      // ... 现有字段 ...
      region_boundaries?: RegionBoundary[]
      portals?: PortalInfo[]
      world_structure?: { layers: MapLayerInfo[] }
    }
    ```
- [ ] Task 2: 更新 API client (AC: #3)
  - [ ] 修改 `frontend/src/api/client.ts` 的 `fetchMapData` 函数，支持 `layer_id` 可选参数
  - [ ] 新增 `fetchWorldStructure(novelId)` 函数
- [ ] Task 3: 创建 MapLayerTabs 组件 (AC: #1, #2, #5)
  - [ ] 新建 `frontend/src/components/visualization/MapLayerTabs.tsx`
  - [ ] Props: `layers: MapLayerInfo[], activeLayerId: string, onLayerChange: (id) => void`
  - [ ] 渲染水平 Tab 栏，每个 Tab 显示层名称 + 地点数
  - [ ] 未解锁层（is_unlocked=false）灰色禁用
  - [ ] 当前层高亮
- [ ] Task 4: 修改 MapPage 集成 Tab 切换 (AC: #1-#5)
  - [ ] 在 `frontend/src/pages/MapPage.tsx` 中:
    - 新增 `activeLayerId` state（默认 "overworld"）
    - 页面加载时获取 world_structure，提取 layers 列表
    - Tab 切换时用新 layer_id 重新 fetchMapData
    - 传递 layer_type 给 NovelMap 以改变背景色
- [ ] Task 5: NovelMap 背景色适配 (AC: #4)
  - [ ] 修改 `frontend/src/components/visualization/NovelMap.tsx`:
    - 接受 `layerType` prop
    - 根据 layerType 设置不同背景色:
      - overworld: `#f0ead6`（羊皮纸色，现有）
      - celestial: `#0f172a`（深蓝）
      - underworld: `#1a0a2e`（暗紫）
      - instance: `#1c1917`（暗棕）
    - hierarchy 模式背景保持 `#1a1a2e`

## Dev Notes

### 现有前端结构

- MapPage: `frontend/src/pages/MapPage.tsx`（382 行）
- NovelMap: `frontend/src/components/visualization/NovelMap.tsx`（466 行）
- API client: `frontend/src/api/client.ts`
- 类型: `frontend/src/api/types.ts`
- 状态管理: zustand stores

### MapPage 现有数据流

```
useEffect(fetchMapData) → setMapData → 传递 locations/layout/etc 给 NovelMap
```

新增:
```
useEffect(fetchWorldStructure) → setLayers → 渲染 MapLayerTabs
Tab 切换 → fetchMapData(layer_id) → 更新 NovelMap
```

### References

- [Source: _bmad-output/world-map-v2-architecture.md#7.1-多层地图UI]
- [Source: frontend/src/pages/MapPage.tsx — 现有地图页面]
- [Source: frontend/src/components/visualization/NovelMap.tsx — 现有地图组件]

## Dev Agent Record

### Agent Model Used

### Completion Notes List

### File List
