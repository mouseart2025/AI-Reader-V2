# Story N10.2: 并列模式视图

Status: review

## Story

As a 编剧,
I want 左侧原文 + 右侧场景列表的并列视图,
So that 我可以对照原文查看场景结构。

## Acceptance Criteria

1. **AC-1**: 左侧显示原文（按段落可滚动）
2. **AC-2**: 右侧显示当前章场景列表
3. **AC-3**: 点击场景 → 原文跳转到对应段落位置 + 高亮
4. **AC-4**: 章节切换时自动加载对应场景

## Tasks / Subtasks

- [x] Task 1: 类型定义 + API 客户端
  - [x] 1.1 `frontend/src/api/types.ts` — Scene / ChapterScenesResponse
  - [x] 1.2 `frontend/src/api/client.ts` — fetchChapterScenes
- [x] Task 2: 剧本页面
  - [x] 2.1 `frontend/src/pages/ScreenplayPage.tsx` — SplitView 组件
- [x] Task 3: 路由 + 导航
  - [x] 3.1 `frontend/src/app/router.tsx` — /screenplay/:novelId
  - [x] 3.2 `frontend/src/app/NovelLayout.tsx` — "剧本" tab
- [x] Task 4: 编译验证

## Completion Notes

- 并列视图：左侧原文段落 + 右侧 SceneCard 列表
- 点击场景卡片自动滚动到对应段落并高亮（paragraph_range 映射）
- 章节选择器下拉切换 + 自动加载场景
- 视图切换按钮（并列/独占）

### Files Changed

- `frontend/src/api/types.ts` — Scene, ChapterScenesResponse 类型
- `frontend/src/api/client.ts` — fetchChapterScenes
- `frontend/src/pages/ScreenplayPage.tsx` — 剧本模式页面（新增）
- `frontend/src/app/router.tsx` — 注册 /screenplay 路由
- `frontend/src/app/NovelLayout.tsx` — 添加"剧本"导航 tab

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
