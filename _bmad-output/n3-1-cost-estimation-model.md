# Story N3.1: 成本预估模型

Status: review

## Story

As a 云端 LLM 用户,
I want 在分析前看到预估费用,
So that 我可以决定是否继续或调整分析范围。

## Acceptance Criteria

1. **AC-1**: 云端模式下点击"开始分析"时弹出成本预览弹窗
2. **AC-2**: 显示：小说名称、分析范围（章节数/字数）、LLM 提供商和模型
3. **AC-3**: 显示预估 Token 消耗（输入/输出分开）和预估费用
4. **AC-4**: 显示"实际费用可能浮动 ±30%"提示
5. **AC-5**: 用户确认后开始分析，取消则返回
6. **AC-6**: 本地 Ollama 模式不弹窗，直接开始

## Tasks / Subtasks

- [x] Task 1: 后端成本预估 API (AC: #1~#4)
  - [x] 1.1 新建 `cost_service.py` — 定价模型（DeepSeek/OpenAI/默认）+ Token 预估公式
  - [x] 1.2 `analysis.py` — 新增 `GET /api/novels/{id}/analyze/estimate` 端点

- [x] Task 2: 后端 LLM 响应 Token 追踪基础设施 (AC: 为 N3.2 铺路)
  - [x] 2.1 `llm_client.py` — 新增 `LlmUsage` dataclass（N3.2 将用于实际追踪）

- [x] Task 3: 前端类型 + API 客户端 (AC: #1~#5)
  - [x] 3.1 `types.ts` — 新增 `CostEstimate` 接口
  - [x] 3.2 `client.ts` — 新增 `fetchCostEstimate()` 函数

- [x] Task 4: 前端成本预览弹窗 (AC: #1~#6)
  - [x] 4.1 新建 `CostPreviewDialog.tsx` — 成本预览弹窗组件
  - [x] 4.2 `AnalysisPage.tsx` — 云端模式下点击分析时弹出预览，确认后才开始

- [x] Task 5: 后端测试
  - [x] 5.1 测试定价查询（已知/未知模型）
  - [x] 5.2 测试成本预估计算（含/不含预扫描、不同章节数）

- [x] Task 6: TypeScript 编译 + 后端测试验证
  - [x] 6.1 30/30 后端测试全部通过
  - [x] 6.2 无新增 TS 编译错误

## Dev Notes

### 定价模型

| 模型 | 输入 ($/1M tokens) | 输出 ($/1M tokens) |
|------|-------------------|--------------------|
| deepseek-chat | 0.27 | 1.10 |
| deepseek-reasoner | 0.55 | 2.19 |
| gpt-4o-mini | 0.15 | 0.60 |
| gpt-4o | 2.50 | 10.00 |
| gpt-4.1-mini | 0.40 | 1.60 |
| gpt-4.1-nano | 0.10 | 0.40 |
| 默认 | 0.50 | 1.50 |

### Token 预估公式

- 固定开销: ~47K tokens/章（system prompt 10KB + examples 21KB → ~47K tokens）
- 章节文本: 字数 × 1.5 tokens/字
- 上下文摘要: ~2000 字 × 1.5 = 3K tokens/章
- 输出: ~4000 tokens/章
- 预扫描: 额外 25K+5K tokens（一次性）
- 费用 = (input_tokens / 1M × input_price) + (output_tokens / 1M × output_price)
- CNY = USD × 7.2

### 前端流程

- `handleStartAnalysis()` → 先调 estimate API → 如果 `is_cloud=true` → 弹窗
- 用户确认 → `doStartAnalysis()` 真正开始
- 本地模式: estimate API 返回 `is_cloud=false` → 直接开始

### References

- [Source: backend/src/services/cost_service.py:1] — 成本预估服务
- [Source: backend/src/api/routes/analysis.py:48] — estimate 端点
- [Source: frontend/src/components/shared/CostPreviewDialog.tsx:1] — 预览弹窗
- [Source: frontend/src/pages/AnalysisPage.tsx:267] — 前端集成

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

- 无特殊 debug 问题

### Completion Notes List

- 成本预估服务完成，支持 6 种已知模型 + 默认定价
- estimate API 返回 is_cloud 标记，前端据此决定是否弹窗
- CostPreviewDialog 显示完整信息：Token 分解、费用、定价参考、浮动提示
- LlmUsage dataclass 已定义，为 N3.2 实时追踪铺路
- 30/30 后端测试通过（含 6 条新增成本预估测试）

### File List

- `backend/src/services/cost_service.py` — 新建：定价模型 + Token 预估
- `backend/src/api/routes/analysis.py` — 新增 `GET /analyze/estimate` 端点
- `backend/src/infra/llm_client.py` — 新增 `LlmUsage` dataclass
- `frontend/src/api/types.ts` — 新增 `CostEstimate` 接口
- `frontend/src/api/client.ts` — 新增 `fetchCostEstimate()` 函数
- `frontend/src/components/shared/CostPreviewDialog.tsx` — 新建：成本预览弹窗
- `frontend/src/pages/AnalysisPage.tsx` — 集成成本预览流程
- `backend/tests/test_cost_service.py` — 新建：6 条成本预估测试
