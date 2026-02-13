# Story 8.5: API 路由与注册

Status: ready-for-dev

## Story

As a 开发者/前端,
I want 通过 REST API 查询预扫描状态和词典内容,
So that 前端可以展示预扫描进度和词典数据（当前 Sprint 仅后端，前端为后续迭代）。

## Acceptance Criteria

1. `POST /api/novels/{novel_id}/prescan` — 手动触发预扫描，返回 `{ "status": "running" }`；若已 running 返回 409
2. `GET /api/novels/{novel_id}/prescan` — 查询预扫描状态，返回 `{ "status": "...", "entity_count": N, "created_at": "..." }`
3. `GET /api/novels/{novel_id}/entity-dictionary` — 获取词典内容，支持 `?type=person&limit=50` 查询参数
4. 词典列表返回格式遵循项目规范：`{ "data": [...], "total": N }`
5. 路由注册到 FastAPI app（main.py）
6. 小说不存在时返回 404

## Tasks / Subtasks

- [ ] Task 1: 创建路由模块 (AC: #1, #2, #3, #4, #6)
  - [ ] 新建 `backend/src/api/routes/prescan.py`
  - [ ] 定义 `router = APIRouter(prefix="/api/novels", tags=["prescan"])`
  - [ ] 实现 `POST /{novel_id}/prescan`:
    - 检查小说存在
    - 检查 prescan_status，若 running 返回 409
    - 触发 `asyncio.create_task(scanner.scan(novel_id))`
    - 返回 `{"status": "running"}`
  - [ ] 实现 `GET /{novel_id}/prescan`:
    - 查询 prescan_status
    - 查询 entity_dictionary 条目数
    - 返回 `{"status": "...", "entity_count": N}`
  - [ ] 实现 `GET /{novel_id}/entity-dictionary`:
    - 接收 query params: `type: str | None`, `limit: int = 100`
    - 如果 type 有值，调用 `get_by_type()`，否则 `get_all()`
    - 返回 `{"data": [...], "total": N}`

- [ ] Task 2: 注册路由 (AC: #5)
  - [ ] 修改 `backend/src/api/main.py`
  - [ ] 添加 `from src.api.routes.prescan import router as prescan_router`
  - [ ] 添加 `app.include_router(prescan_router)`

- [ ] Task 3: Pydantic Response 模型 (AC: #4)
  - [ ] 在路由模块中或 schemas 中定义响应模型
  - [ ] `PrescanStatusResponse`: status, entity_count, created_at
  - [ ] `EntityDictionaryResponse`: data(list), total(int)

## Dev Notes

- 路由命名遵循项目规范：小写复数名词，kebab-case
- 参考现有路由模式：`backend/src/api/routes/novels.py`、`backend/src/api/routes/analysis.py`
- POST prescan 是幂等操作——如果已 completed，重新触发会先 delete_all 再重新扫描
- entity-dictionary 的返回格式中，aliases 字段已从 JSON 字符串解析为 list

### Project Structure Notes

- `backend/src/api/routes/prescan.py` — 新文件，与 `novels.py`、`analysis.py` 同级
- `backend/src/api/main.py` — 修改，添加 router 注册

### References

- [Source: _bmad-output/entity-prescan-architecture.md#5.3 API]
- [Source: _bmad-output/architecture.md#7.2 API 响应格式] — 响应格式规范
- [Source: backend/src/api/routes/novels.py] — 路由实现参考

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
