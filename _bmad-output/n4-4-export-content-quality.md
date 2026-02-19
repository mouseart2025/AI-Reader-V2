# Story N4.4: 导出内容质量

Status: review

## Story

As a 用户,
I want 导出的 Markdown 内容准确反映分析结果,
So that 我可以直接用于创作参考。

## Acceptance Criteria

1. **AC-1**: 人物档案中的关系演变链与实体卡片一致
2. **AC-2**: 别称列表完整（包含 AliasResolver 合并的所有别称）
3. **AC-3**: 地点层级正确反映 WorldStructure 的 location_parents
4. **AC-4**: 无乱码、无截断、Markdown 语法正确

## Tasks / Subtasks

- [x] Task 1: 审查渲染器数据完整性 (AC: #1~#3)
  - [x] 1.1 确认 aggregate_person 返回完整 stages（演变链）— 已在渲染器中展示多 stage "→" 链
  - [x] 1.2 确认 aliases 列表完整 — aggregate_person 返回 AliasResolver 合并的全部别称，渲染器无额外截断
  - [x] 1.3 确认 aggregate_location 使用 WorldStructure.location_parents — parent/children 正确反映

- [x] Task 2: Markdown 语法修正 (AC: #4)
  - [x] 2.1 新增 `_escape_pipe()` — 关系表格中 pipe 字符转义
  - [x] 2.2 所有 section 渲染前检查数据非空（已有 guard）

- [x] Task 3: 编译验证 — 0 TS 错误, 57/57 测试通过

## Completion Notes

- 关系演变链：多 stage 时显示 "关系A → 关系B → 关系C" 格式
- pipe 转义确保含 | 字符的人名不会破坏 Markdown 表格
- 数据来源直接使用 aggregate_person/aggregate_location，保证与实体卡片一致

### Files Changed

| File | Change |
|------|--------|
| `backend/src/services/series_bible_renderer.py` | 关系演变链 + _escape_pipe + 空内容 guard |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
