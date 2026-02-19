# Story N11.3: 隐私控制

Status: review

## Story

As a 用户,
I want 可以完全关闭行为追踪,
So that 我的使用习惯不被记录。

## Acceptance Criteria

1. **AC-1**: 设置页可关闭"使用统计"开关
2. **AC-2**: 关闭后停止记录所有行为事件
3. **AC-3**: 可选择删除已收集的历史数据
4. **AC-4**: 默认状态：开启

## Tasks / Subtasks

- [x] Task 1: 隐私开关 UI
  - [x] 1.1 SettingsPage 追踪 toggle 开关（默认开启）
  - [x] 1.2 开关调用 PUT /api/usage/tracking-enabled
  - [x] 1.3 清除数据按钮（DELETE /api/usage/clear，已在 N11.2 实现）
- [x] Task 2: 编译验证

## Completion Notes

- 设置页"使用统计与隐私"区块顶部添加 toggle 开关
- 开关状态持久化到 app_settings 表
- 关闭后 POST /api/usage/track 返回 tracking_disabled，不记录事件
- 清除按钮可删除所有历史数据
- 默认状态：开启

### Files Changed

- `frontend/src/pages/SettingsPage.tsx` — 追踪开关 toggle UI

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
