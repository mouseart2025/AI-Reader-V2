# Story N4.1: 导出引擎核心架构

Status: review

## Story

As a 用户,
I want 一个可扩展的导出引擎,
So that 当前支持 Markdown，未来可扩展 Word/Excel/PDF。

## Acceptance Criteria

1. **AC-1**: ExportService 从 EntityAggregator 获取人物/地点/物品/组织 Profile 数据
2. **AC-2**: 从 VizService 获取关系图数据
3. **AC-3**: 支持按章节范围导出（chapter_start / chapter_end）
4. **AC-4**: 支持选择导出模块：人物档案、关系网络、地点百科、物品道具、组织势力、时间线
5. **AC-5**: 导出 API: `POST /api/novels/{id}/series-bible/export` 返回 .md 文件下载

## Tasks / Subtasks

- [x] Task 1: 后端 SeriesBibleService 核心 (AC: #1~#4)
  - [x] 1.1 `series_bible_service.py` — collect_data() 聚合 6 类模块数据
  - [x] 1.2 支持 modules 选择 + chapter_start/chapter_end 范围

- [x] Task 2: Markdown 渲染器 (AC: #5)
  - [x] 2.1 `series_bible_renderer.py` — render_markdown() 生成完整 .md（目录 + 6 模块）

- [x] Task 3: API 端点 (AC: #5)
  - [x] 3.1 `series_bible.py` route — POST /api/novels/{id}/series-bible/export

- [x] Task 4: 前端类型 + API 客户端
  - [x] 4.1 `types.ts` — SeriesBibleRequest, SERIES_BIBLE_MODULES
  - [x] 4.2 `client.ts` — exportSeriesBible() (blob download)

- [x] Task 5: 编译验证 — 0 TS 错误, 57/57 后端测试通过

## Completion Notes

- SeriesBibleService.collect_data() 异步聚合: person/location/item/org profiles + graph edges + timeline events
- 实体限制: 人物 top 50, 地点 top 50, 物品 top 30, 组织 top 20（按 chapter_count 排序）
- Markdown 渲染器输出: 标题 + 作者 + 分析范围 + 目录 + 6 模块内容
- 前端 exportSeriesBible() 使用 blob download 触发文件保存

### Files Changed

| File | Change |
|------|--------|
| `backend/src/services/series_bible_service.py` | 新建: collect_data(), SeriesBibleData |
| `backend/src/services/series_bible_renderer.py` | 新建: render_markdown() |
| `backend/src/api/routes/series_bible.py` | 新建: POST /api/novels/{id}/series-bible/export |
| `backend/src/api/main.py` | 注册 series_bible router |
| `frontend/src/api/types.ts` | SeriesBibleRequest, SERIES_BIBLE_MODULES |
| `frontend/src/api/client.ts` | exportSeriesBible() |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
