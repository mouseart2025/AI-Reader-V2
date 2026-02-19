---
title: '重新设计「编辑世界结构」面板'
slug: 'world-structure-editor-redesign'
created: '2026-02-16'
status: 'in-progress'
stepsCompleted: [1]
tech_stack: ['React 19', 'TypeScript', 'shadcn/ui', 'Radix UI', 'Tailwind CSS 4']
files_to_modify:
  - frontend/src/components/visualization/WorldStructureEditor.tsx
  - frontend/src/api/types.ts
  - backend/src/api/routes/world_structure.py
code_patterns: ['EncyclopediaPage.tsx 层级树 DFS + expandedNodes', 'shadcn Popover+Command Combobox 模式']
test_patterns: []
---

# Tech-Spec: 重新设计「编辑世界结构」面板

**Created:** 2026-02-16

## Overview

### Problem Statement

WorldStructureEditor（440px 右侧抽屉面板）打开后导致浏览器崩溃。根因：RegionEditor 一次性渲染 1400+ 地点行，每行含一个 Radix `<Select>` 内嵌 519 个 `<SelectItem>`，总计 ~72 万个 DOM 元素。

同时，现有编辑器功能不完整——只支持编辑区域归属和传送门，不支持编辑父级（层级关系）、tier、layer 等核心属性。

### Solution

采用「树形浏览 + 搜索 + 详情编辑面板」混合模式重写编辑器：
- 用 `location_parents` 构建可折叠层级树做导航（默认折叠到 L2，DOM < 100 节点）
- 搜索框实时过滤树节点
- 点击节点后在底部详情面板中编辑（父级/区域/层/tier），使用 Combobox（Popover+Command）替代 Select

### Scope

**In Scope:**
- P0: 树形浏览器（基于 location_parents）
- P0: 搜索过滤（输入即过滤，展开匹配路径）
- P0: 详情编辑面板（父级、区域、层、tier 四个字段）
- P0: 后端 override API 补齐 location_parent 类型
- P0: 前端 TypeScript 类型补齐
- P1: 覆盖记录 Tab（列出所有用户 overrides）
- 保留现有传送门编辑功能

**Out of Scope:**
- 拖拽调整父子关系（440px 面板太窄）
- 地图上右键 → 编辑（后续迭代）
- 虚拟化库引入（树折叠机制天然限制可见量）
- 后端 API 改动（复用现有 override 机制）

## Context for Development

### Codebase Patterns

**树形层级视图**：`EncyclopediaPage.tsx` (line 109-177) 已有完整的层级树实现模式：
- `childrenMap`: `Map<string, string[]>` 构建父子索引
- `expandedNodes`: `Set<string>` 控制展开/折叠
- DFS 遍历生成展平的 `TreeEntry[]`
- 缩进渲染：`paddingLeft: depth * 20 + 16`

**Combobox 模式**：shadcn/ui 的 `<Popover>` + `<Command>` 组合（cmdk 库已在项目依赖中）。不需要引入新依赖。

**Override 保存模式**：现有 `saveWorldStructureOverrides()` 接受 batch，每条包含 `override_type` + `override_key` + `override_json`。编辑面板每次修改一个字段就往 pending batch 里追加一条。

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `frontend/src/components/visualization/WorldStructureEditor.tsx` | 当前编辑器，需完全重写内部组件 |
| `frontend/src/pages/EncyclopediaPage.tsx` (line 109-177) | 层级树 DFS 模式参考 |
| `frontend/src/api/types.ts` (line 379-385) | `WorldStructureData` 类型，需补齐字段 |
| `frontend/src/api/client.ts` (line 204-233) | WorldStructure API 客户端 |
| `backend/src/api/routes/world_structure.py` (line 96-120) | override 保存接口，`valid_types` 需补齐 |
| `backend/src/models/world_structure.py` (line 93-104) | WorldStructure 数据模型 |

### Technical Decisions

1. **不引入虚拟化库**：树形结构默认折叠到 L2（~20 个省级节点），展开单个省显示 ~50 子节点。峰值可见节点 < 200，不需要 react-virtuoso/tanstack-virtual。
2. **Combobox 而非 Select**：地点列表 1400+ 条目，必须支持搜索过滤。用 shadcn 的 Popover+Command 组合。
3. **不做拖拽**：440px 面板中拖拽体验差且容易误操作。改父级通过 Combobox 选择完成。
4. **编辑面板固定在底部**：用 CSS `flex` 布局，树浏览区 `flex-1 overflow-auto`，编辑面板固定高度在底部。

## Implementation Plan

### Tasks

#### Task 1: 后端 — 补齐 `location_parent` override 类型

**文件**: `backend/src/api/routes/world_structure.py`

**改动**:
1. 在 `save_overrides()` 的 `valid_types` 集合中添加 `"location_parent"`
2. 在 override 应用逻辑中处理 `location_parent` 类型（`world_structure_store.load_with_overrides` 或 override 应用函数中）

**验证**: `curl -X PUT` 发送 `location_parent` override 不返回 400。

#### Task 2: 前端 — 补齐 TypeScript 类型

**文件**: `frontend/src/api/types.ts`

**改动**: 在 `WorldStructureData` 接口中添加：
```typescript
export interface WorldStructureData {
  novel_id: string
  layers: WorldStructureLayer[]
  portals: WorldStructurePortal[]
  location_region_map: Record<string, string>
  location_layer_map: Record<string, string>
  // 新增：
  location_parents: Record<string, string>   // child → parent
  location_tiers: Record<string, string>     // name → tier
  location_icons: Record<string, string>     // name → icon
  novel_genre_hint: string | null
  spatial_scale: string | null
}
```

#### Task 3: 前端 — 重写 WorldStructureEditor 内部组件

**文件**: `frontend/src/components/visualization/WorldStructureEditor.tsx`

**整体结构**:
```
WorldStructureEditor (抽屉壳: 440px, 不变)
├── Header (标题 + 保存/关闭按钮, 基本不变)
├── SearchBox (新增: 搜索框)
├── Tabs: [地点层级] [传送门] [覆盖记录]
├── TabContent
│   ├── LocationTreeTab (新增: 树形浏览器)
│   ├── PortalTab (现有, 小改)
│   └── OverrideHistoryTab (新增)
└── DetailPanel (新增: 底部编辑面板, 选中节点时显示)
```

**3a. LocationTreeTab — 树形浏览器**

数据构建（复用百科页面模式）：
```typescript
// 从 ws.location_parents 构建树
const { roots, childrenMap, entryMap } = useMemo(() => {
  const parents = ws.location_parents ?? {}
  const tiers = ws.location_tiers ?? {}
  const allNames = new Set([
    ...Object.keys(parents),
    ...Object.values(parents),
    ...Object.keys(tiers),
  ])
  const childrenMap = new Map<string, string[]>()
  for (const [child, parent] of Object.entries(parents)) {
    const children = childrenMap.get(parent) ?? []
    children.push(child)
    childrenMap.set(parent, children)
  }
  // Sort children alphabetically
  for (const [, children] of childrenMap) children.sort()
  // Roots = names that are parents but not children
  const childSet = new Set(Object.keys(parents))
  const roots = [...allNames].filter(n => !childSet.has(n)).sort()
  // Entry map
  const entryMap = new Map<string, { tier: string; childCount: number }>()
  for (const name of allNames) {
    entryMap.set(name, {
      tier: tiers[name] ?? "",
      childCount: childrenMap.get(name)?.length ?? 0,
    })
  }
  return { roots, childrenMap, entryMap }
}, [ws])
```

渲染（DFS 展平 + expandedNodes）：
- 默认展开 roots 和第 1 层子节点（`defaultExpanded` = roots + roots 的直接子节点）
- 搜索时：匹配节点 + 所有祖先自动展开
- 每个节点行：`▶/▼` + 名字 + tier 标签 + 子节点数 + 覆盖标记(琥珀点)
- 单击选中 → `setSelectedLocation(name)` → 底部 DetailPanel 显示

**3b. DetailPanel — 详情编辑面板**

选中一个地点后在底部显示，4 个 Combobox 字段：

```tsx
function DetailPanel({ locationName, ws, pendingChanges, onFieldChange }) {
  const currentParent = pendingChanges.get(`parent:${locationName}`)
    ?? ws.location_parents[locationName] ?? ""
  const currentRegion = pendingChanges.get(`region:${locationName}`)
    ?? ws.location_region_map[locationName] ?? ""
  const currentLayer = pendingChanges.get(`layer:${locationName}`)
    ?? ws.location_layer_map[locationName] ?? "overworld"
  const currentTier = pendingChanges.get(`tier:${locationName}`)
    ?? ws.location_tiers[locationName] ?? ""

  return (
    <div className="border-t p-3 space-y-2">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium">{locationName}</h4>
        {hasOverride && <Button size="xs" variant="ghost">重置为 AI 生成</Button>}
      </div>
      <FieldCombobox label="父级" value={currentParent}
        options={allLocationNames} onChange={v => onFieldChange("parent", v)} />
      <FieldCombobox label="区域" value={currentRegion}
        options={allRegionNames} onChange={v => onFieldChange("region", v)} />
      <FieldSelect label="层" value={currentLayer}
        options={layerOptions} onChange={v => onFieldChange("layer", v)} />
      <FieldSelect label="类型" value={currentTier}
        options={TIER_OPTIONS} onChange={v => onFieldChange("tier", v)} />
    </div>
  )
}
```

TIER_OPTIONS 常量:
```typescript
const TIER_OPTIONS = [
  { value: "continent", label: "大洲" },
  { value: "region", label: "区域" },
  { value: "province", label: "省/路" },
  { value: "prefecture", label: "州/府" },
  { value: "city", label: "城市" },
  { value: "town", label: "城镇" },
  { value: "village", label: "村庄" },
  { value: "landmark", label: "地标" },
  { value: "building", label: "建筑" },
  { value: "room", label: "房间" },
]
```

**3c. FieldCombobox 组件**

基于 shadcn 的 Popover + Command 组合：
```tsx
function FieldCombobox({ label, value, options, onChange }) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const filtered = options.filter(o => o.includes(search))

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted-foreground w-10">{label}</span>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button variant="outline" size="sm" className="h-7 flex-1 justify-between text-xs">
            {value || "无"} <ChevronDown className="h-3 w-3" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-[300px] p-0" align="start">
          <Command>
            <CommandInput placeholder="搜索..." value={search}
              onValueChange={setSearch} className="h-8 text-xs" />
            <CommandList className="max-h-48">
              <CommandEmpty>无匹配</CommandEmpty>
              {filtered.slice(0, 50).map(opt => (
                <CommandItem key={opt} value={opt}
                  onSelect={() => { onChange(opt); setOpen(false) }}>
                  {opt}
                </CommandItem>
              ))}
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  )
}
```

**3d. OverrideHistoryTab — 覆盖记录**

```tsx
function OverrideHistoryTab({ overrides, onDelete }) {
  return (
    <div className="space-y-1">
      {overrides.length === 0 && <p className="text-muted-foreground text-xs text-center py-8">暂无覆盖记录</p>}
      {overrides.map(ov => (
        <div key={ov.id} className="flex items-center gap-2 rounded px-2 py-1.5 border">
          <div className="flex-1 min-w-0">
            <div className="text-xs font-medium truncate">{ov.override_key}</div>
            <div className="text-[10px] text-muted-foreground">{ov.override_type} · {ov.created_at}</div>
          </div>
          <Button variant="ghost" size="icon-xs" onClick={() => onDelete(ov.id)}>
            <ResetIcon className="size-3.5" />
          </Button>
        </div>
      ))}
    </div>
  )
}
```

**3e. 保存逻辑**

pending changes 存为 `Map<string, { type: OverrideType, key: string, json: Record<string, unknown> }>`。
点击保存时，批量调用 `saveWorldStructureOverrides(novelId, batch)`。

Override 映射：
- 修改父级 → `{ override_type: "location_parent", override_key: locName, override_json: { parent: newParent } }`
- 修改区域 → `{ override_type: "location_region", override_key: locName, override_json: { region: newRegion } }`
- 修改层 → `{ override_type: "location_layer", override_key: locName, override_json: { layer: newLayer } }`
- 修改 tier → 注意：当前后端没有 `location_tier` override 类型。**需要后端补齐**（Task 1 中一并处理）。

#### Task 4: 后端 — 补齐 `location_tier` override 类型

**文件**: `backend/src/api/routes/world_structure.py`

在 `valid_types` 中添加 `"location_tier"`。在 override 应用逻辑中处理（设置 `ws.location_tiers[key] = json["tier"]`）。

### Acceptance Criteria

**AC1: 编辑器不崩溃**
- Given 水浒传 (1471 地点, 519 区域)
- When 用户点击「编辑世界」打开编辑器
- Then 面板在 1 秒内加载完成，无卡顿

**AC2: 树形浏览可用**
- Given 编辑器已打开
- When 查看「地点层级」Tab
- Then 显示层级树，默认展开到省级(L2)，可点击展开/折叠

**AC3: 搜索过滤有效**
- Given 编辑器已打开
- When 在搜索框输入"济州"
- Then 树中只显示包含"济州"的节点及其祖先路径

**AC4: 详情编辑可用**
- Given 树中点击了"石碣村"
- When 底部编辑面板显示
- Then 可修改父级(Combobox)、区域、层、类型四个字段
- And 修改后顶栏显示"保存"按钮

**AC5: 保存生效**
- Given 修改了"石碣村"的父级从"梁山泊"改为"济州"
- When 点击「保存」
- Then 后端 override 保存成功
- And 刷新地图后层级关系更新

**AC6: 覆盖记录可查**
- Given 已保存若干覆盖
- When 切换到「覆盖记录」Tab
- Then 按时间倒序列出所有覆盖，可单条删除

**AC7: 传送门编辑不变**
- Given 切换到「传送门」Tab
- When 查看/添加/删除传送门
- Then 功能与改版前一致

## Additional Context

### Dependencies

- shadcn/ui 的 `Command` 组件（cmdk，已在项目依赖中）
- shadcn/ui 的 `Popover` 组件（已在项目中）
- 不引入新依赖

### Testing Strategy

手动验证：
1. 打开水浒传地图 → 编辑世界 → 确认不崩溃
2. 搜索"梁山" → 确认过滤正确
3. 修改某地点父级 → 保存 → 刷新地图 → 确认层级变化
4. 覆盖记录 Tab → 删除覆盖 → 确认恢复 AI 生成值

### Notes

- 后端 `load_with_overrides()` 需确认是否已处理 `location_parent` 和 `location_tier` 类型的 override 应用。若未处理，Task 1/4 需在 store 层也补齐逻辑。
- 搜索框过滤策略：匹配节点名 + 其所有祖先路径展开。这需要在 DFS 前先计算哪些节点匹配，再反向标记祖先。
- 树节点默认展开逻辑：`expandedNodes` 初始化为 roots + roots 的直接子节点 names。
