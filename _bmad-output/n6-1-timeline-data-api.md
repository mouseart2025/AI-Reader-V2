# Story N6.1: 时间线数据聚合 API

Status: review

## Story

As a 用户,
I want 后端提供丰富的时间线数据接口,
So that 前端可以渲染完整的事件时间线。

## Acceptance Criteria

1. **AC-1**: 返回按章节排序的事件列表，每个事件包含 chapter, type, summary, participants, location
2. **AC-2**: 事件类型分类：角色登场/退场、关键冲突、地点转移、物品交接、组织变动
3. **AC-3**: 涉及 3+ 角色的事件标记 is_major: true
4. **AC-4**: 响应时间 < 1 秒（500 章）

## Tasks / Subtasks

- [x] Task 1: 扩展 get_timeline_data() (AC: #1~#3)
  - [x] 1.1 提取衍生事件：characters 首次出场 → "角色登场"，item_events → "物品交接"，org_events → "组织变动"
  - [x] 1.2 添加 is_major 标记 (participants >= 3)，is_major 时 importance 自动提升到 high

- [x] Task 2: 编译验证 — 0 TS 错误, 57/57 测试通过

## Completion Notes

- 衍生事件通过遍历 ChapterFact 的 characters/item_events/org_events 生成
- 角色登场：使用 seen_characters set 追踪首次出场
- 物品交接：格式 "actor action item_name [→ recipient]"
- 组织变动：格式 "member action org_name [(role)]"
- is_major=true 时 medium importance 自动升为 high
- 事件类型扩展为 9 种：战斗/成长/社交/旅行/其他 + 角色登场/物品交接/组织变动（+退场留给 N6.2 前端渲染时处理）

### Files Changed

| File | Change |
|------|--------|
| `backend/src/services/visualization_service.py` | 扩展 get_timeline_data(): 衍生事件 + is_major |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
