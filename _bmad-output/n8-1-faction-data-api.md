# Story N8.1: 势力数据聚合 API

Status: review

## Story

As a 用户,
I want 后端提供势力/阵营数据接口,
So that 前端可以渲染势力图。

## Acceptance Criteria

1. **AC-1**: GET /api/novels/{id}/factions?chapter_start=1&chapter_end=100
2. **AC-2**: 返回组织列表（含层级结构、成员列表）和组织间关系
3. **AC-3**: 支持章节范围过滤

## Tasks / Subtasks

- [x] Task 1: 已在 legacy epic-4 (4-6) 中实现

## Completion Notes

- 功能已在 epic-4-6 中完整实现
- `backend/src/api/routes/factions.py` — GET endpoint with range params
- `backend/src/services/visualization_service.py` — `get_factions_data()` 4 源聚合
- 输出结构: orgs[], relations[], members{}, analyzed_range

### Files Changed

无新增（已有实现）

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
