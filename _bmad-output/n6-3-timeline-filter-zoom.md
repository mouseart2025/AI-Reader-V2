# Story N6.3: 时间线筛选与缩放

Status: review

## Story

As a 用户,
I want 按角色和事件类型筛选时间线,
So that 我可以聚焦特定角色或事件。

## Acceptance Criteria

1. **AC-1**: 可按角色多选筛选（仅显示所选角色相关事件）
2. **AC-2**: 可按事件类型多选筛选
3. **AC-3**: 支持章节范围选择器（与其他可视化页面共享 chapterRangeStore）
4. **AC-4**: 缩放功能：折叠/展开章节详情

## Tasks / Subtasks

- [x] Task 1: 多选事件类型筛选 (AC: #2)
  - [x] 1.1 filterType → filterTypes (Set\<FilterType\>)，支持多选 toggle
  - [x] 1.2 "全部" 按钮重置为 all，其他按钮 toggle 具体类型

- [x] Task 2: 章节折叠/展开 (AC: #4)
  - [x] 2.1 collapsedChapters Set 状态 + toggleChapterCollapse
  - [x] 2.2 工具栏 "折叠"/"展开" 按钮
  - [x] 2.3 章节标题点击切换折叠，显示事件计数 + 折叠箭头

- [x] Task 3: 编译验证 — 0 TS 错误, 57/57 测试通过

## Completion Notes

- AC-1 (角色多选) 已在 v0.16.0 中实现（swimlane 侧栏）
- AC-3 (章节范围选择器) 已通过 VisualizationLayout + chapterRangeStore 实现
- 事件类型筛选改为多选：可同时选择"战斗"+"社交"等多种类型
- 章节折叠：点击章节标题折叠/展开事件列表，显示事件计数
- 全局折叠/展开按钮在工具栏

### Files Changed

| File | Change |
|------|--------|
| `frontend/src/pages/TimelinePage.tsx` | filterTypes 多选 + collapsedChapters 折叠/展开 |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
