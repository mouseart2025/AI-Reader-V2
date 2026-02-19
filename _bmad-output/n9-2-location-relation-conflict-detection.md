# Story N9.2: 地点与关系冲突检测

Status: review

## Story

As a 用户,
I want 系统检测地点层级和关系逻辑矛盾,
So that 我的世界观设定保持一致。

## Acceptance Criteria

1. **AC-1**: 检测地点层级矛盾（同一地点在不同章节有不同上级）
2. **AC-2**: 检测关系演变逻辑冲突（角色已死但后续章节以活人出场）
3. **AC-3**: 所有冲突按严重程度排序输出

## Tasks / Subtasks

- [x] Task 1: 已在 N9.1 中一并实现
  - [x] 1.1 `_detect_location_conflicts()` — 地点上级不一致检测
  - [x] 1.2 `_detect_death_continuity()` — 角色死亡连续性检测
  - [x] 1.3 结果按严重程度排序

## Completion Notes

- 功能完全包含在 `conflict_detector.py` 中，N9.1 已一并实现
- 地点冲突: 统计每个地点的上级分配，多数 vs 少数不一致时报告
- 死亡连续性: 跟踪 org_events 中的"阵亡"事件，若后续章节该角色再次出现则标记"严重"
- 所有冲突按 严重→一般→提示 排序

### Files Changed

无新增（已在 N9.1 中实现）

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
