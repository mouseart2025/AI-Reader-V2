# Story N1.3: 引导式功能展示（教学气泡）

Status: review

## Story

As a 新用户,
I want 在样本小说中看到功能引导提示,
So that 我能快速了解各功能的入口和用法。

## Acceptance Criteria

1. **AC-1**: Given 用户打开样本小说的阅读页，When 首次进入阅读页，Then 显示步骤 1/4 引导气泡："试试点击高亮的人物名称，查看 AI 自动生成的角色卡片"
2. **AC-2**: 用户点击"知道了"后进入下一步引导（关系图→世界地图→问答）
3. **AC-3**: 用户点击"不再提示"后关闭所有引导
4. **AC-4**: 引导状态持久化到 localStorage，每个引导点只展示一次
5. **AC-5**: 仅在样本小说中显示引导气泡，用户自己上传的小说不显示

## Tasks / Subtasks

- [x] Task 1: 创建引导状态 store (AC: #3, #4)
  - [x] 1.1 新建 `frontend/src/stores/tourStore.ts` — Zustand store with localStorage 持久化
  - [x] 1.2 Store 状态：`currentStep: number`（0-3，-1=完成/关闭）、`dismissed: boolean`
  - [x] 1.3 Store 方法：`nextStep()`、`dismiss()`、`reset()`；导出 `TOUR_STEPS` 和 `TOTAL_TOUR_STEPS`

- [x] Task 2: 创建 GuidedTourBubble 组件 (AC: #1, #2, #3)
  - [x] 2.1 新建 `frontend/src/components/shared/GuidedTourBubble.tsx` — 气泡组件
  - [x] 2.2 Props: `step`, `totalSteps`, `message`, `onNext`, `onDismiss`, `position`
  - [x] 2.3 UI: `bg-gray-900/95` 深色背景 + CSS 三角箭头 + 步骤指示器 + "知道了"/"不再提示"
  - [x] 2.4 CSS absolute 定位，`position` prop 控制 top/bottom

- [x] Task 3: 阅读页集成 Step 1 引导 (AC: #1, #5)
  - [x] 3.1 `ReadingPage.tsx` — 导入 tourStore 和 GuidedTourBubble
  - [x] 3.2 新增 `ReadingTourBubble` 内部组件，条件渲染：`isSample && currentStep === 0 && !dismissed`
  - [x] 3.3 气泡定位在章节标题上方

- [x] Task 4: NovelLayout 集成 Steps 2-4 引导 (AC: #2, #5)
  - [x] 4.1 `NovelLayout.tsx` — 导入 tourStore 和 GuidedTourBubble
  - [x] 4.2 Step 2: 气泡指向"关系图"tab
  - [x] 4.3 Step 3: 气泡指向"地图"tab
  - [x] 4.4 Step 4: 气泡指向"问答"tab
  - [x] 4.5 `TOUR_TAB_MAP` 映射 step→tab key，仅 `novel?.is_sample` 时显示
  - [x] 4.6 NovelLayout 已有 `fetchNovel()` 调用，Task 5 确保返回 is_sample

- [x] Task 5: 确保 fetchNovel API 返回 is_sample (AC: #5)
  - [x] 5.1 `novel_store.py` — `get_novel()` SQL 增加 `is_sample`
  - [x] 5.2 `schemas/novels.py` — `NovelResponse` 增加 `is_sample: bool = False`

- [x] Task 6: TypeScript 编译验证
  - [x] 6.1 `npm run build` — 无新增 TS 错误（仅预存错误）
  - [x] 6.2 `uv run pytest tests/ -v` — 21/21 通过

## Dev Notes

### 关键架构约束

1. **NovelLayout**: `frontend/src/app/NovelLayout.tsx` — 统一顶部导航栏，包含 7 个 tab（分析/阅读/关系图/地图/时间线/百科/问答）
2. **ReadingPage**: `frontend/src/pages/ReadingPage.tsx` — 阅读页面，已有实体高亮和点击交互
3. **is_sample 字段**: Story N1.2 已在 `Novel` 类型和 `list_novels()` API 中添加 `is_sample`，但 `get_novel()` 和 `NovelResponse` 尚未包含
4. **shadcn/ui**: 项目已有 10 个组件（badge, button, card, dialog, input, label, progress, range-slider, select, alert-dialog），无 Popover/Tooltip
5. **Zustand stores**: 8 个现有 store，使用 `zustand/middleware` 的 `persist` 进行 localStorage 持久化

### 引导步骤设计

| Step | 位置 | 目标元素 | 提示文字 |
|------|------|----------|----------|
| 1/4 | ReadingPage | 阅读内容区 | 试试点击高亮的人物名称，查看 AI 自动生成的角色卡片 |
| 2/4 | NovelLayout nav | "关系图" tab | 点击查看人物关系网络，发现隐藏的角色关联 |
| 3/4 | NovelLayout nav | "地图" tab | 探索小说中的地理世界，查看角色行动轨迹 |
| 4/4 | NovelLayout nav | "问答" tab | 向 AI 提问关于小说的任何问题，获得基于原文的回答 |

### 气泡组件设计

不引入额外依赖（Popover），使用简单的 absolute 定位 + CSS 箭头实现。气泡样式：
- 深色半透明背景（`bg-gray-900/95 text-white`）
- 圆角 + 阴影
- 步骤指示器（如 "1/4"）
- 主文字 + 两个操作按钮

### localStorage 键名

- `ai-reader-tour-state` — JSON: `{ currentStep: number, dismissed: boolean }`

### NovelLayout 中获取 is_sample

NovelLayout 已有 `fetchNovel(novelId)` 调用，返回的 `Novel` 对象中需要包含 `is_sample`。当前 `get_novel()` SQL 查询和 `NovelResponse` schema 不包含 `is_sample`，需要 Task 5 补充。

### 前端无测试框架

项目未配置 vitest，前端验证通过 `npm run build` TypeScript 编译检查。

### References

- [Source: frontend/src/app/NovelLayout.tsx] — 导航布局
- [Source: frontend/src/pages/ReadingPage.tsx] — 阅读页面
- [Source: frontend/src/pages/BookshelfPage.tsx] — is_sample 徽章（N1.2）
- [Source: frontend/src/stores/] — 现有 Zustand stores
- [Source: backend/src/db/novel_store.py] — get_novel() 需要增加 is_sample
- [Source: backend/src/api/schemas/novels.py] — NovelResponse 需要增加 is_sample

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

- 前端 build 有预存 TS 错误（ChatPage, EncyclopediaPage, FactionsPage, MapPage, TimelinePage, analysisStore），与本 Story 无关
- `useTourStore.getState()` 在 JSX 中不触发 re-render，改用 `ReadingTourBubble` 子组件 + hook 订阅

### Completion Notes List

- **Task 1 完成**: `tourStore.ts` — Zustand + persist localStorage，4 个 tour 步骤定义，currentStep/dismissed 状态 + nextStep/dismiss/reset 方法
- **Task 2 完成**: `GuidedTourBubble.tsx` — 深色气泡组件，CSS 三角箭头，步骤指示器，"知道了"/"不再提示"按钮，position prop
- **Task 3 完成**: `ReadingPage.tsx` — `ReadingTourBubble` 子组件，Step 1 仅在样本小说 + 首次阅读时显示
- **Task 4 完成**: `NovelLayout.tsx` — Steps 2-4 通过 `TOUR_TAB_MAP` 映射到 graph/map/chat tabs，仅样本小说显示
- **Task 5 完成**: `novel_store.py` get_novel() SQL + `NovelResponse` schema 增加 is_sample
- **Task 6 完成**: 前端编译无新增错误，后端 21/21 pytest 通过

### File List

- `frontend/src/stores/tourStore.ts` — 新增：引导 tour 状态管理
- `frontend/src/components/shared/GuidedTourBubble.tsx` — 新增：引导气泡组件
- `frontend/src/pages/ReadingPage.tsx` — 新增 ReadingTourBubble 子组件 + tour imports
- `frontend/src/app/NovelLayout.tsx` — nav tabs 包裹 relative div + tour bubble 渲染
- `backend/src/db/novel_store.py` — get_novel() SQL 增加 is_sample
- `backend/src/api/schemas/novels.py` — NovelResponse 增加 is_sample
