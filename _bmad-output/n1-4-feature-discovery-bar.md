# Story N1.4: 功能发现底部导航条

Status: review

## Story

As a 新用户,
I want 在样本小说阅读页底部看到功能入口,
So that 我能发现并跳转到关系图、地图等可视化页面。

## Acceptance Criteria

1. **AC-1**: Given 用户在样本小说的阅读页，When 页面加载完成，Then 底部显示功能发现条：📊 关系图 / 🗺️ 世界地图 / 📅 时间线 / 📤 导出
2. **AC-2**: 点击可跳转到对应页面
3. **AC-3**: 完成 4 步引导后显示"✅ 体验完成！[上传我自己的小说] [继续探索]"
4. **AC-4**: 仅在样本小说中显示，用户上传的小说不显示

## Tasks / Subtasks

- [x] Task 1: 创建 FeatureDiscoveryBar 组件 (AC: #1, #2, #3)
  - [x] 1.1 新建 `frontend/src/components/shared/FeatureDiscoveryBar.tsx`
  - [x] 1.2 Props: `novelId: string`、`onNavigate: (path: string) => void`
  - [x] 1.3 默认状态：4 个功能按钮（关系图/世界地图/时间线/百科），带 emoji 图标
  - [x] 1.4 完成状态：tour 完成后（currentStep === -1 或 dismissed）显示"体验完成"提示 + "上传我自己的小说"和"继续探索"按钮
  - [x] 1.5 使用 tourStore 读取当前引导状态

- [x] Task 2: 阅读页集成 FeatureDiscoveryBar (AC: #1, #4)
  - [x] 2.1 `ReadingPage.tsx` — 导入 FeatureDiscoveryBar
  - [x] 2.2 条件渲染：仅当 `novel?.is_sample` 时在底部显示
  - [x] 2.3 传递 novelId 和 navigate 函数

- [x] Task 3: TypeScript 编译 + 后端测试验证
  - [x] 3.1 `npm run build` 确认无新增 TS 错误
  - [x] 3.2 `uv run pytest tests/ -v` 确认后端测试全部通过（21/21）

## Dev Notes

### 关键架构约束

1. **ReadingPage**: `frontend/src/pages/ReadingPage.tsx` — 已有 tourStore 导入（N1.3）
2. **tourStore**: `frontend/src/stores/tourStore.ts` — currentStep -1 表示 tour 完成/关闭
3. **NovelLayout nav tabs**: 已有路由：`/graph/{id}`, `/map/{id}`, `/timeline/{id}`
4. **导出页面**: 尚未实现独立导出页（N-Epic-4），暂跳转到 `/encyclopedia/{id}` 作为替代（百科是最接近"导出数据浏览"的功能），或用 settings

### 功能按钮路由

| 按钮 | Emoji | 路由 |
|------|-------|------|
| 关系图 | 📊 | `/graph/{novelId}` |
| 世界地图 | 🗺️ | `/map/{novelId}` |
| 时间线 | 📅 | `/timeline/{novelId}` |
| 导出 | 📤 | `/encyclopedia/{novelId}` (暂用百科) |

### 完成状态判断

tourStore.currentStep === -1 时表示 tour 已完成或被关闭。此时显示"体验完成"横幅。

### References

- [Source: frontend/src/pages/ReadingPage.tsx] — 阅读页面
- [Source: frontend/src/stores/tourStore.ts] — 引导状态
- [Source: frontend/src/app/NovelLayout.tsx] — 路由结构

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

- 前端 build 有预存 TS 错误（ChatPage, EncyclopediaPage, FactionsPage, MapPage, TimelinePage, analysisStore），与本 Story 无关
- 导出按钮路由改为 `/encyclopedia/{novelId}`（百科），因 N-Epic-4 导出功能尚未实现

### Completion Notes List

- **Task 1 完成**: `FeatureDiscoveryBar.tsx` — 4 个功能按钮（关系图/世界地图/时间线/百科）+ tour 完成后显示"体验完成"横幅 + "上传我自己的小说"/"继续探索"按钮
- **Task 2 完成**: `ReadingPage.tsx` — 底部条件渲染 FeatureDiscoveryBar，仅 `novel?.is_sample && novelId` 时显示，传递 navigate 函数
- **Task 3 完成**: 前端编译无新增错误，后端 21/21 pytest 通过

### File List

- `frontend/src/components/shared/FeatureDiscoveryBar.tsx` — 新增：功能发现底部导航条组件
- `frontend/src/pages/ReadingPage.tsx` — 修改：底部集成 FeatureDiscoveryBar（仅样本小说）
