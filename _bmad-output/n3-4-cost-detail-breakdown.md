# Story N3.4: 分析成本明细

Status: review

## Story

As a 云端 LLM 用户,
I want 分析完成后查看按章节的费用明细,
So that 我可以了解成本构成。

## Acceptance Criteria

1. **AC-1**: 设置 > 使用统计 > 分析记录 — 显示每章的输入/输出 Token、费用、发现实体数
2. **AC-2**: 显示合计行
3. **AC-3**: 支持导出为 CSV
4. **AC-4**: 显示分析时间段和使用的 LLM 模型

## Tasks / Subtasks

- [x] Task 1: DB schema — chapter_facts 增加 token/cost 列 (AC: #1)
  - [x] 1.1 `sqlite_db.py` — 迁移加 `input_tokens`, `output_tokens`, `cost_usd`, `cost_cny` 列
  - [x] 1.2 `chapter_fact_store.py` — `insert_chapter_fact` 增加 token/cost 参数 + SELECT 查询含新列

- [x] Task 2: 分析循环持久化每章成本 (AC: #1)
  - [x] 2.1 `analysis_service.py` — 计算 per-chapter cost 并传递到 `insert_chapter_fact`

- [x] Task 3: 后端 API — 成本明细端点 (AC: #1~#4)
  - [x] 3.1 `GET /api/novels/{novel_id}/analysis/cost-detail` — 每章 token/cost + 合计 + 模型 + 时间段
  - [x] 3.2 `GET /api/novels/{novel_id}/analysis/cost-detail/csv` — CSV 下载（含合计行）
  - [x] 3.3 `GET /api/settings/analysis-records` — 已完成分析任务列表（含 JOIN novels + SUM cost）

- [x] Task 4: 前端类型 + API 客户端 (AC: #1~#4)
  - [x] 4.1 `types.ts` — ChapterCostDetail, CostDetailSummary, CostDetailResponse, AnalysisRecord
  - [x] 4.2 `client.ts` — fetchCostDetail(), costDetailCsvUrl(), fetchAnalysisRecords()

- [x] Task 5: 前端设置页分析记录 UI (AC: #1~#4)
  - [x] 5.1 `SettingsPage.tsx` — 分析记录列表 + 可展开章节明细表格 + 合计行 + CSV 导出

- [x] Task 6: 编译验证 — 0 TS 错误, 57/57 后端测试通过

## Completion Notes

- chapter_facts 表新增 4 列：input_tokens, output_tokens, cost_usd, cost_cny（migration safe）
- analysis_service 计算每章 per-chapter cost 并传入 insert_chapter_fact
- 3 个新 API 端点：cost-detail（JSON）, cost-detail/csv（CSV 下载）, analysis-records（任务列表）
- 设置页使用统计 section 新增「分析记录」子区：点击展开查看章节明细，合计行，导出 CSV

### Files Changed

| File | Change |
|------|--------|
| `backend/src/db/sqlite_db.py` | Migration 新增 chapter_facts 4 列 |
| `backend/src/db/chapter_fact_store.py` | insert 增加 4 参数, SELECT 含新列 |
| `backend/src/services/analysis_service.py` | 计算 per-chapter cost, 传入 insert |
| `backend/src/api/routes/analysis.py` | 3 个新端点: cost-detail, cost-detail/csv, analysis-records |
| `frontend/src/api/types.ts` | ChapterCostDetail, CostDetailSummary, CostDetailResponse, AnalysisRecord |
| `frontend/src/api/client.ts` | fetchCostDetail(), costDetailCsvUrl(), fetchAnalysisRecords() |
| `frontend/src/pages/SettingsPage.tsx` | 分析记录列表 + 展开明细 + CSV 导出 |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
