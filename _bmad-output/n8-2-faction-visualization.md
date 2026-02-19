# Story N8.2: 势力图可视化组件

Status: review

## Story

As a 用户,
I want 在势力图页面查看阵营分布和关系,
So that 我可以理解小说中的势力格局。

## Acceptance Criteria

1. **AC-1**: 显示树形/层级图，组织内部展示成员
2. **AC-2**: 势力间关系以连线表示：同盟=绿实线、敌对=红虚线、从属=灰箭头
3. **AC-3**: 节点大小按成员数量缩放
4. **AC-4**: 20 个组织/200 个成员 < 2 秒渲染

## Tasks / Subtasks

- [x] Task 1: 已在 legacy epic-4 (4-6) 中实现

## Completion Notes

- 功能已在 epic-4-6 中完整实现
- `frontend/src/pages/FactionsPage.tsx` — force graph + 6 色组织类型 + 4 种关系连线
- 右侧面板显示选中组织的成员列表（姓名/角色/状态）
- hover 高亮关联节点，双击打开实体卡片

### Files Changed

无新增（已有实现）

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
