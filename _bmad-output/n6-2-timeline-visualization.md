# Story N6.2: 时间线可视化组件

Status: review

## Story

As a 用户,
I want 在时间线页面浏览小说事件的时间演进,
So that 我可以快速把握故事脉络。

## Acceptance Criteria

1. **AC-1**: 事件以圆点表示，关键事件（is_major）以大节点高亮
2. **AC-2**: 悬停事件显示摘要 tooltip
3. **AC-3**: 点击事件展开详情卡片
4. **AC-4**: 新增事件类型颜色（角色登场/物品交接/组织变动）

## Tasks / Subtasks

- [x] Task 1: 扩展 TimelinePage 事件类型 + tooltip (AC: #1~#4)
  - [x] 1.1 事件类型颜色扩展：角色登场=紫色, 物品交接=黄色, 组织变动=粉色
  - [x] 1.2 is_major 事件：大圆点(10px) + ring 高亮 + "关键"标签
  - [x] 1.3 hover tooltip: title 属性显示 summary + location + participants
  - [x] 1.4 类型筛选器扩展为 9 种 + 图例同步更新

- [x] Task 2: 编译验证 — 0 TS 错误, 57/57 测试通过

## Completion Notes

- 事件类型从 5 种扩展到 8 种（+角色登场/物品交接/组织变动）
- is_major 事件用 ring-2 ring-primary/40 视觉突出 + "关键"紫色标签
- hover tooltip 使用原生 title 属性（轻量，无需额外库）
- 图例 ●大=关键 替代 ●大=高

### Files Changed

| File | Change |
|------|--------|
| `frontend/src/pages/TimelinePage.tsx` | 扩展事件类型颜色 + is_major 高亮 + tooltip + 筛选器 + 图例 |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
