---
stepsCompleted: [step-01-validate-prerequisites, step-02-design-epics, step-03-create-stories]
inputDocuments:
  - CLAUDE.md — 前端技术栈与架构
  - 前端代码性能审计结果
date: '2026-02-21'
scope: 前端性能优化 — 构建修复 + 渲染优化 + 虚拟列表 + Bundle 瘦身
---

# AI Reader V2 - Epic 22: 前端性能优化

## Overview

本文档将前端性能优化需求分解为 1 个 Epic、6 个 Story。目标是系统性解决首屏加载、大数据量页面卡顿、地图/图谱渲染性能三大痛点。

**当前性能审计发现：**
- **构建阻塞**：15 个 TypeScript 编译错误（未使用变量、类型断言不安全、联合类型缺失），导致 `npm run build` 失败
- **Bundle 体积**：dist 总计 2.8MB，`index.js` 349K、`MapPage.js` 210K、`vendor-graph` 135K 为前三大 chunk
- **无虚拟列表**：百科页、时间线页对 500+ 条目使用 `.map()` 直接渲染全部 DOM 节点
- **Store 全量订阅**：ReadingPage 从 `useReadingStore()` 解构 16 个字段，任一字段变更触发整页重渲染
- **缺少 React.memo**：实体卡片组件（PersonCard、LocationCard 等）无 memo 包裹，父组件状态变更导致不必要重渲染
- **O(n*m) 别名处理**：ReadingPage 实体-别名匹配使用嵌套 find 循环
- **标签碰撞检测 O(n²)**：NovelMap 标签碰撞使用暴力 AABB，地点多时帧率下降

---

## Requirements Inventory

### Functional Requirements

```
FR-PERF-001: 修复所有 TypeScript 编译错误，恢复 npm run build 可用性
FR-PERF-002: ReadingPage 大章节（1000+ 实体）阅读流畅，无可感知卡顿
FR-PERF-003: 百科页/时间线页 500+ 条目列表滚动流畅（60fps）
FR-PERF-004: 图谱页 400+ 节点交互（筛选、拖拽、缩放）响应流畅
FR-PERF-005: 地图页 200+ 地点渲染与交互无卡顿
FR-PERF-006: 首屏加载时间优化（减少主 bundle 体积）
```

### NonFunctional Requirements

```
NFR-PERF-001: 修复后 npm run build 零 TypeScript 错误、零警告
NFR-PERF-002: 主 index.js chunk 体积 < 200K（当前 349K）
NFR-PERF-003: 长列表页面首次渲染 DOM 节点数 < 100（虚拟化后可视窗口内）
NFR-PERF-004: ReadingPage store 订阅粒度细化至 ≤ 5 个独立 selector
NFR-PERF-005: 不改变任何现有功能行为和 UI 视觉效果
```

### FR Coverage Map

| FR | Epic | Story |
|----|------|-------|
| FR-PERF-001 | Epic 22 | 22.1 |
| FR-PERF-002 | Epic 22 | 22.2 |
| FR-PERF-003 | Epic 22 | 22.3 |
| FR-PERF-004, FR-PERF-005 | Epic 22 | 22.4 |
| FR-PERF-006 | Epic 22 | 22.5 |
| 全部 | Epic 22 | 22.6 |

## Epic List

| # | Epic | 优先级 | FR | Stories |
|---|------|--------|-----|---------|
| 22 | 前端性能优化 | P1 | FR-PERF-001~006 | 6 |

---

## Epic 22: 前端性能优化

**目标：** 修复构建阻塞，系统性消除前端性能瓶颈，确保大数据量小说（1000+ 实体、500+ 地点）下的流畅使用体验。
**成功标准：** `npm run build` 零错误；主 bundle < 200K；百科/时间线 500+ 条目滚动 60fps；ReadingPage 大章节无感知卡顿。

### Story 22.1: 修复 TypeScript 编译错误

As a 开发团队,
I want 修复所有 TypeScript 编译错误,
So that `npm run build` 能成功执行，CI/CD 不被阻塞。

**Acceptance Criteria:**

**Given** 当前 `tsc -b` 报告 15 个错误（10 个未使用变量、3 个不安全类型断言、1 个联合类型缺失、1 个未使用导入）
**When** 修复所有错误
**Then** `npm run build` 成功完成，零错误零警告
**And** 修复策略：
  - 未使用变量/导入：直接删除
  - `EncyclopediaPage.tsx` 的 `Record<string, unknown> as CategoryStats` ：定义正确的 API 响应类型或使用 `unknown` 中转
  - `MapPage.tsx` 的联合类型缺失 `"geographic"`：补充类型定义
**And** 修复不改变任何运行时行为

**涉及文件：**
- `src/components/entity-cards/EntityCardDrawer.tsx` — 删除未使用 `cn`
- `src/components/visualization/NovelMap.tsx` — 删除未使用 `terrainUrl`
- `src/components/visualization/VisualizationLayout.tsx` — 删除未使用 `useState`
- `src/pages/ChatPage.tsx` — 删除未使用 `openEntityCard`、`novel`、`streamingSources`
- `src/pages/EncyclopediaPage.tsx` — 修复类型断言 + 删除未使用导入/变量
- `src/pages/FactionsPage.tsx` — 删除未使用 `handleNodeDblClick`
- `src/pages/MapPage.tsx` — 补充 `"geographic"` 联合类型
- `src/pages/TimelinePage.tsx` — 删除未使用 `useRef`

### Story 22.2: ReadingPage 渲染性能优化

As a 用户,
I want 阅读大章节小说时页面响应流畅,
So that 实体高亮、侧边栏切换、章节跳转不卡顿。

**Acceptance Criteria:**

**Given** ReadingPage 当前从 `useReadingStore()` 解构 16 个字段，任一字段变更触发整页重渲染
**When** 将 store 订阅拆分为细粒度 selector
**Then** 每个组件只订阅自己需要的字段（如侧边栏组件只订阅 `sidebarOpen`、`tocSearch`）
**And** `useReadingStore` 的直接 `()` 调用全部替换为 `(s => s.xxx)` selector 模式
**And** 侧边栏开关不触发正文区域重渲染

**Given** 实体-别名匹配使用 O(aliases × entities) 嵌套 find 循环
**When** 重构为 Map 预构建
**Then** 先构建 `canonicalMap: Map<canonicalName, entity>`，再遍历 aliases 直接查 Map
**And** 时间复杂度降为 O(aliases + entities)

**Given** 实体卡片组件（PersonCard、LocationCard、ItemCard、OrgCard、ConceptCard）无 memo 包裹
**When** 为每个卡片组件添加 `React.memo` 包裹
**Then** 父组件状态变更（如切换章节）但卡片 props 未变时，卡片不重渲染
**And** EntityCardDrawer 的子组件也添加 memo

**涉及文件：**
- `src/pages/ReadingPage.tsx` — store selector 拆分 + 别名 Map 重构
- `src/components/entity-cards/PersonCard.tsx` — React.memo
- `src/components/entity-cards/LocationCard.tsx` — React.memo
- `src/components/entity-cards/ItemCard.tsx` — React.memo
- `src/components/entity-cards/OrgCard.tsx` — React.memo
- `src/components/entity-cards/ConceptCard.tsx` — React.memo
- `src/components/entity-cards/EntityCardDrawer.tsx` — React.memo

### Story 22.3: 长列表虚拟化

As a 用户,
I want 浏览大量实体（500+ 地点、概念、事件）时页面不卡顿,
So that 百科全书和时间线页面滚动流畅。

**Acceptance Criteria:**

**Given** 百科页层级树渲染 500+ TreeEntry 时直接 `.map()` 生成全部 DOM
**When** 集成虚拟列表库（`@tanstack/react-virtual`）
**Then** 只渲染可视窗口内的条目（~20-30 个），滚动时动态替换
**And** 层级缩进、展开/折叠交互不受影响
**And** 搜索过滤后列表自动重置滚动位置

**Given** 时间线页渲染全部事件无分页
**When** 对事件列表添加虚拟化
**Then** 事件数 > 200 时只渲染可视窗口内条目
**And** 泳道布局和类型筛选不受影响

**Given** 实体列表（ReadingPage 侧边栏实体列表）在实体数量多时的渲染
**When** 实体列表条目 > 100
**Then** 启用虚拟化渲染

**技术选型：** `@tanstack/react-virtual`（轻量 ~3KB，支持动态高度、API 简洁）

**涉及文件：**
- `package.json` — 新增 `@tanstack/react-virtual` 依赖
- `src/pages/EncyclopediaPage.tsx` — 层级树虚拟化
- `src/pages/TimelinePage.tsx` — 事件列表虚拟化
- `src/pages/ReadingPage.tsx` — 侧边栏实体列表虚拟化（如条目 > 100）

### Story 22.4: 图谱与地图渲染优化

As a 用户,
I want 大规模关系图（400+ 节点）和地图（200+ 地点）交互流畅,
So that 筛选滑块拖动、图谱缩放、地图平移无掉帧。

**Acceptance Criteria:**

**Given** GraphPage 每次筛选滑块变更触发 O(n+m) 重新过滤
**When** 对筛选滑块操作添加 debounce（150ms）
**Then** 快速拖动滑块时不逐帧触发重计算
**And** 松开滑块后 150ms 内完成过滤渲染

**Given** GraphPage `graphData` useMemo 每次创建新排序数组
**When** 将排序移到数据加载时一次性完成（或使用 `useMemo` 正确缓存排序结果）
**Then** 筛选条件未变时不重复排序

**Given** NovelMap 标签碰撞检测使用 O(n²) 暴力 AABB
**When** 地点数 > 100 时，使用空间索引（四叉树或网格分区）优化碰撞检测
**Then** 标签碰撞检测时间复杂度降至 O(n log n)
**And** 渲染帧率在 200+ 地点时 ≥ 30fps

**Given** react-force-graph-2d 力仿真在大图上持续消耗 CPU
**When** 仿真收敛后（`onEngineStop` 回调）
**Then** 停止仿真循环，降低 idle CPU 使用

**涉及文件：**
- `src/pages/GraphPage.tsx` — debounce + 排序缓存 + 仿真停止
- `src/components/visualization/NovelMap.tsx` — 碰撞检测优化

### Story 22.5: Bundle 体积优化

As a 用户,
I want 首次访问页面时加载更快,
So that 首屏白屏时间更短。

**Acceptance Criteria:**

**Given** 主 `index.js` chunk 349K，包含大量共享代码
**When** 分析 bundle 组成并优化
**Then** 主 `index.js` 体积 < 200K（目标降低 40%+）

**Given** Vite 配置未启用 CSS 代码分割
**When** 确认 Vite 默认行为并按需调整
**Then** 各页面的 CSS 随页面 chunk 按需加载（当前已有 MapPage 独立 CSS，验证其他页面）

**Given** `vendor-ui` chunk 包含 Radix UI 组件
**When** 检查是否存在不必要的全量导入
**Then** 确保 Radix UI 通过 `@radix-ui/react-*` 独立包导入（tree-shakeable）
**And** 未使用的 Radix 组件不打入 bundle

**Given** `lucide-react` 图标库可能全量打包
**When** 检查导入方式
**Then** 确保使用具名导入（`import { Search } from "lucide-react"`）以支持 tree-shaking
**And** 如 lucide 无法 tree-shake，考虑替换为按需导入方案

**Given** Vite 未配置 `build.reportCompressedSize: false`
**When** 添加配置
**Then** 构建速度略微提升（跳过 gzip 压缩体积计算）

**可选优化（视收益决定）：**
- 配置 `rollup-plugin-visualizer` 生成 bundle 分析 treemap
- 评估 `vendor-graph` (135K) 是否可按需加载（仅 GraphPage 使用）

**涉及文件：**
- `vite.config.ts` — chunk 策略 + 构建配置
- `package.json` — 可能新增 `rollup-plugin-visualizer`（dev 依赖）
- 各页面/组件 — 导入路径优化

### Story 22.6: 构建验证 + 回归检查

As a 开发团队,
I want 确认所有性能优化不引入功能回归,
So that 发布版本质量可靠。

**Acceptance Criteria:**

**Given** Story 22.1~22.5 全部完成
**When** 执行 `npm run build`
**Then** 零错误零警告，构建成功
**And** `dist/` 目录总体积 ≤ 2.5MB（当前 2.8MB）
**And** 主 `index.js` < 200K

**Given** 构建产物部署后
**When** 手动验证以下页面
**Then** 所有页面功能正常：
  - 书架页：上传、导入、删除
  - 阅读页：章节切换、实体高亮、别名点击、侧边栏开关、场景面板
  - 图谱页：筛选滑块、节点拖拽、缩放、点击实体卡片
  - 地图页：小说地图 + 地理地图切换、编辑模式、世界结构编辑器
  - 百科页：分类浏览、层级树、搜索、详情查看
  - 时间线页：泳道筛选、事件详情
  - 聊天页：流式对话、来源溯源
  - 设置页：主题切换、LLM 配置

---

## Implementation Notes

### 依赖变更

| 包名 | 操作 | 说明 |
|------|------|------|
| `@tanstack/react-virtual` | 新增 | 虚拟列表，~3KB gzip |
| `rollup-plugin-visualizer` | 新增 (dev) | Bundle 分析（可选） |

### Story 依赖关系

```
22.1 (构建修复) → 22.2, 22.3, 22.4, 22.5 (并行) → 22.6 (验证)
```

Story 22.1 必须先完成（否则无法构建验证），22.2~22.5 之间无依赖可并行开发，22.6 为最终验证。

*文档创建于 2026-02-21。Epic 22，6 个 Story。*
