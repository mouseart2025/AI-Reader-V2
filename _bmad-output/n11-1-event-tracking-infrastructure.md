# Story N11.1: 事件追踪基础设施

Status: review

## Story

As a 产品团队,
I want 在前端建立事件追踪系统,
So that 可以记录用户的功能使用行为。

## Acceptance Criteria

1. **AC-1**: 用户关键操作（上传/分析/导出/查看卡片/问答等）记录到本地 SQLite
2. **AC-2**: 事件格式：event_type, metadata JSON, timestamp
3. **AC-3**: 不记录小说内容、用户身份、IP 地址
4. **AC-4**: 统一通过 `trackEvent()` 函数调用
5. **AC-5**: 事件数据仅本地存储，不自动上传

## Tasks / Subtasks

- [x] Task 1: 后端事件存储
  - [x] 1.1 SQLite `app_settings` + `usage_events` 表
  - [x] 1.2 `backend/src/db/usage_event_store.py`
  - [x] 1.3 `backend/src/api/routes/usage.py` — POST track + GET stats + DELETE clear + tracking toggle
- [x] Task 2: 前端追踪
  - [x] 2.1 `frontend/src/lib/tracker.ts` — trackEvent() 统一接口
- [x] Task 3: 编译验证

## Completion Notes

- `usage_events` 表: id, event_type, metadata(JSON), created_at
- `app_settings` 表: key-value 存储，用于 tracking_enabled 开关
- API: POST /api/usage/track, GET /api/usage/stats, DELETE /api/usage/clear
- 追踪开关: GET/PUT /api/usage/tracking-enabled
- 前端 trackEvent() fire-and-forget 模式，不阻塞 UI
- 16 种预定义事件类型（novel_upload/analysis_start/view_graph 等）

### Files Changed

- `backend/src/db/sqlite_db.py` — 新增 app_settings + usage_events 表
- `backend/src/db/usage_event_store.py` — 事件存储 CRUD（新增）
- `backend/src/api/routes/usage.py` — 使用分析 API（新增）
- `backend/src/api/main.py` — 注册 usage 路由
- `frontend/src/lib/tracker.ts` — 前端追踪函数（新增）

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
