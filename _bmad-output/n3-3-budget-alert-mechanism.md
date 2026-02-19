# Story N3.3: 预算告警机制

Status: review

## Story

As a 云端 LLM 用户,
I want 在接近预算上限时收到告警,
So that 我不会无意中超出预算。

## Acceptance Criteria

1. **AC-1**: 设置页可配置月度预算上限（默认 ¥50/月）
2. **AC-2**: 本月消费达到预算 80% 时前端 Toast 提示
3. **AC-3**: 达到 100% 时弹窗确认："已达预算上限。[继续] [暂停] [切换本地]"
4. **AC-4**: 单次分析预估超出剩余预算时，分析前 CostPreviewDialog 中提示
5. **AC-5**: 预算设置位于设置页 > 使用统计

## Tasks / Subtasks

- [x] Task 1: 后端预算与月度用量持久化 (AC: #1, #2~#4)
  - [x] 1.1 `cost_service.py` — get/set monthly_budget + get/add monthly_usage（user_state 表）
  - [x] 1.2 `settings.py` — `GET/POST /api/settings/budget` 端点
  - [x] 1.3 `analysis.py` estimate 端点 — 返回 `monthly_used_cny` 和 `monthly_budget_cny`

- [x] Task 2: 后端分析循环月度用量累加 (AC: #2, #3)
  - [x] 2.1 `analysis_service.py` — 每章完成后调用 `add_monthly_usage`
  - [x] 2.2 WS cost 字段追加 `monthly_used_cny` 和 `monthly_budget_cny`

- [x] Task 3: 前端类型 + API 客户端 (AC: #1~#5)
  - [x] 3.1 `types.ts` — BudgetInfo, CostEstimate 扩展 monthly 字段, AnalysisCostStats 扩展
  - [x] 3.2 `client.ts` — fetchBudget(), setBudget()

- [x] Task 4: 前端设置页预算配置 (AC: #1, #5)
  - [x] 4.1 `SettingsPage.tsx` — 使用统计 section：月度预算输入 + 本月用量显示

- [x] Task 5: 前端预算告警 (AC: #2, #3, #4)
  - [x] 5.1 `CostPreviewDialog.tsx` — 显示剩余预算，预估超出时警告
  - [x] 5.2 `AnalysisPage.tsx` — WS progress 中检测 80%/100% 阈值，Toast + 弹窗

- [x] Task 6: 后端测试 + 编译验证

## Dev Notes

### 持久化方案

月度用量存储在 `user_state` 表：
- key: `budget_monthly_cny` → 月度预算（默认 50）
- key: `cost_2026_02` → 当月累计用量 JSON: `{"usd": 0.5, "cny": 3.6, "input_tokens": 100000, "output_tokens": 8000}`

## Completion Notes

- 57/57 后端测试通过，0 TS 编译错误
- 月度预算默认 ¥50，持久化在 user_state 表（key=`budget_monthly_cny`/`cost_YYYY_MM`）
- 80% 阈值触发底部 Toast，100% 触发 AlertDialog（暂停/继续选项）
- CostPreviewDialog 显示剩余预算，预估超支时按钮变为"超出预算，仍然开始"
- 设置页「使用统计」section：月度预算输入 + 彩色进度条

### Files Changed

| File | Change |
|------|--------|
| `backend/src/services/cost_service.py` | 新增 get/set_monthly_budget, get/add_monthly_usage |
| `backend/src/api/routes/settings.py` | 新增 GET/POST /api/settings/budget |
| `backend/src/api/routes/analysis.py` | estimate 端点追加 monthly_budget_cny/monthly_used_cny |
| `backend/src/services/analysis_service.py` | 每章完成调用 add_monthly_usage，WS cost 字段追加 monthly 数据 |
| `frontend/src/api/types.ts` | 新增 BudgetInfo，扩展 AnalysisCostStats/CostEstimate |
| `frontend/src/api/client.ts` | 新增 fetchBudget(), setBudget() |
| `frontend/src/pages/SettingsPage.tsx` | 使用统计 section：预算输入 + 用量进度条 |
| `frontend/src/components/shared/CostPreviewDialog.tsx` | 剩余预算显示 + 超支警告 |
| `frontend/src/pages/AnalysisPage.tsx` | 80%/100% 预算告警 Toast + Dialog |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
