# Story N10.3: 独占模式视图

Status: review

## Story

As a 编剧,
I want 全屏单场景显示 + 键盘导航,
So that 我可以专注查看单个场景的完整内容。

## Acceptance Criteria

1. **AC-1**: 全屏显示单个场景（标题+地点+角色+描述+原文+事件）
2. **AC-2**: 顶部场景标签导航快速切换
3. **AC-3**: 键盘快捷键 ←/→ 切换场景，Esc 返回并列模式

## Tasks / Subtasks

- [x] Task 1: FullscreenView 组件（融入 ScreenplayPage）
  - [x] 1.1 场景标签导航栏
  - [x] 1.2 场景元数据展示（标题/地点/角色/描述/对话统计）
  - [x] 1.3 场景原文段落渲染（paragraph_range 映射）
  - [x] 1.4 事件列表展示
  - [x] 1.5 键盘导航（←/→/Esc）
- [x] Task 2: 编译验证

## Completion Notes

- 独占模式已内嵌于 ScreenplayPage 的 FullscreenView 组件
- 与并列模式共享页面，通过 viewMode 切换
- 场景信息完整：标题、地点标签、对话统计、角色列表、描述、原文段落、事件列表
- 底部状态栏显示快捷键提示和当前场景序号

### Files Changed

- `frontend/src/pages/ScreenplayPage.tsx` — FullscreenView 组件（与 N10.2 合并实现）

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
