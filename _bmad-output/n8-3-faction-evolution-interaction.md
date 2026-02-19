# Story N8.3: 势力演变与交互

Status: review

## Story

As a 用户,
I want 通过章节范围滑块查看势力变化,
So that 我可以追踪势力格局的演变。

## Acceptance Criteria

1. **AC-1**: 章节范围滑块动态更新势力图 — ✅ 已有 (VisualizationLayout)
2. **AC-2**: 点击组织节点展开详情卡片 — ✅ 已有 (双击打开实体卡片)
3. **AC-3**: 支持按类型筛选（宗门/国家/家族/帮派/商会/军队）
4. **AC-4**: 支持组织层级展开/折叠

## Tasks / Subtasks

- [x] Task 1: AC-1, AC-2 已在 legacy epic-4 实现
- [x] Task 2: 组织类型多选筛选工具栏 (AC: #3)
  - [x] 2.1 filterTypes Set + toggleTypeFilter，availableTypes 从数据动态提取
  - [x] 2.2 工具栏按钮 + 组织计数
  - [x] 2.3 filteredOrgs / filteredRelations 联动 graph 和成员面板
- [x] Task 3: 成员面板折叠/展开 (AC: #4)
  - [x] 3.1 右侧面板改为组织列表，每个组织可展开/折叠查看成员
  - [x] 3.2 expandedOrgs Set + toggleOrgExpand
  - [x] 3.3 展开状态显示成员（角色/状态标签）+ 组织详情入口
- [x] Task 4: 编译验证 — 0 TS 错误, 57/57 测试通过

## Completion Notes

- 工具栏: 全部 + 动态类型按钮（带颜色点），显示 "N / M 组织" 计数
- 筛选联动: graph nodes + links + 右侧面板全部使用 filteredOrgs
- 右侧面板重构: 从单选显示改为全组织可折叠列表，点击展开成员 + 选中高亮
- 删除不再需要的 selectedMembers useMemo

### Files Changed

| File | Change |
|------|--------|
| `frontend/src/pages/FactionsPage.tsx` | 类型筛选 + 组织折叠/展开 + 面板重构 |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
