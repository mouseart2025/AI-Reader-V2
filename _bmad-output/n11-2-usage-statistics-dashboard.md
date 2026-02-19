# Story N11.2: 使用统计仪表盘

Status: review

## Story

As a 产品团队,
I want 在设置页查看本地使用统计,
So that 可以了解功能使用情况。

## Acceptance Criteria

1. **AC-1**: 功能使用频率排行
2. **AC-2**: 时间维度趋势图
3. **AC-3**: 数据完全匿名，仅用于产品优化

## Tasks / Subtasks

- [x] Task 1: 使用统计面板
  - [x] 1.1 集成到 SettingsPage — 功能使用频率条形图 + 每日趋势柱状图
  - [x] 1.2 清除数据按钮
- [x] Task 2: 编译验证

## Completion Notes

- 设置页底部新增"功能使用统计"板块
- 功能频率排行：水平条形图，显示 top 10 事件类型
- 每日趋势：迷你柱状图（30天），悬停显示具体数值
- 清除使用数据按钮 + "仅本地存储"提示
- 数据来源：GET /api/usage/stats?days=30

### Files Changed

- `frontend/src/pages/SettingsPage.tsx` — 新增使用统计区块 + usageStats 状态

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
