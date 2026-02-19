# Story N3.2: 实时成本追踪

Status: review

## Story

As a 云端 LLM 用户,
I want 在分析过程中看到实时费用,
So that 我可以随时决定是否暂停分析。

## Acceptance Criteria

1. **AC-1**: 云端分析进行中，进度条下方显示实时成本统计
2. **AC-2**: 每完成一章更新一次：已用 Token（输入/输出）、已花费、预估剩余、预估总计
3. **AC-3**: Token 消耗和费用数据随 WebSocket progress 消息一起推送
4. **AC-4**: 本地 Ollama 模式不显示成本面板

## Tasks / Subtasks

- [x] Task 1: 后端 LLM 客户端返回 Token Usage (AC: #3)
  - [x] 1.1 `llm_client.py` — `generate()` 返回 `(content, LlmUsage)` 元组，从 Ollama 响应解析 `prompt_eval_count`/`eval_count`
  - [x] 1.2 `openai_client.py` — `generate()` 返回 `(content, LlmUsage)` 元组，从 `data["usage"]` 解析
  - [x] 1.3 `chapter_fact_extractor.py` — `_call_and_parse` + `extract` 返回 `(ChapterFact, LlmUsage)`，多段合并累加 usage

- [x] Task 2: 后端分析循环集成成本追踪 (AC: #2, #3)
  - [x] 2.1 `analysis_service.py` — 累加每章 usage 到 `cost_stats` dict，用 `get_pricing()` 计算实时费用
  - [x] 2.2 WebSocket `progress` 消息追加 `cost` 字段（仅云端模式）
  - [x] 2.3 `task_status` 完成消息也包含最终成本汇总

- [x] Task 3: 前端类型 + Store 扩展 (AC: #1~#4)
  - [x] 3.1 `types.ts` — 新增 `AnalysisCostStats` 接口，WsProgress/WsTaskStatus 新增 `cost?` 字段
  - [x] 3.2 `analysisStore.ts` — state 新增 `costStats`，onmessage 更新

- [x] Task 4: 前端成本实时显示 (AC: #1, #2, #4)
  - [x] 4.1 `AnalysisPage.tsx` — 云端模式下进度卡片显示成本面板（已用 Token、已花费、预估剩余、预估总计）
  - [x] 4.2 分析完成卡片显示最终费用汇总

- [x] Task 5: 后端测试
  - [x] 5.1 测试 Ollama generate() 返回 usage（含格式/无格式/缺失字段）
  - [x] 5.2 测试 OpenAI generate() 返回 usage（含格式/无格式）
  - [x] 5.3 测试成本累加公式正确性

- [x] Task 6: 编译验证
  - [x] 6.1 57/57 后端测试全部通过
  - [x] 6.2 无新增 TS 编译错误

## Dev Notes

### LLM Usage 解析

**Ollama**: `data["prompt_eval_count"]` (input), `data["eval_count"]` (output)
**OpenAI**: `data["usage"]["prompt_tokens"]`, `data["usage"]["completion_tokens"]`

### 破坏性变更

`generate()` 返回值从 `str | dict` 改为 `tuple[str | dict, LlmUsage]`。所有调用方需更新：
- `chapter_fact_extractor._call_and_parse()` — 解构赋值
- `world_structure_agent._call_llm()` — `result, _usage = ...`
- `entity_pre_scanner.classify_candidates()` — `result, _usage = ...`

### WebSocket cost 字段格式

```json
{
  "type": "progress",
  "cost": {
    "total_input_tokens": 150000,
    "total_output_tokens": 12000,
    "total_cost_usd": 0.054,
    "total_cost_cny": 0.39,
    "estimated_remaining_usd": 0.162,
    "estimated_remaining_cny": 1.17,
    "is_cloud": true
  }
}
```

### 成本计算公式

- 已花费 = (input_tokens/1M × input_price) + (output_tokens/1M × output_price)
- 预估剩余 = (已花费 / 已完成章数) × 剩余章数
- 预估总计 = 已花费 + 预估剩余

### References

- [Source: backend/src/infra/llm_client.py:89] — Ollama generate() with usage
- [Source: backend/src/infra/openai_client.py:203] — OpenAI generate() with usage
- [Source: backend/src/services/analysis_service.py:314] — cost accumulation in analysis loop
- [Source: frontend/src/stores/analysisStore.ts:93] — costStats in WS handler
- [Source: frontend/src/pages/AnalysisPage.tsx:470] — cost display panel

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

- 无特殊 debug 问题

### Completion Notes List

- LLM generate() 改为返回 (content, LlmUsage) 元组，所有 3 个调用方已更新
- Ollama 解析 prompt_eval_count/eval_count，OpenAI 解析 usage.prompt_tokens/completion_tokens
- 分析循环云端模式下累加 cost_stats 并随 progress/task_status WS 消息推送
- 前端进度卡片显示：已用 Token（输入/输出分开）、已花费（¥+$）、预估剩余、预估总计
- 分析完成卡片显示最终费用汇总
- 本地 Ollama 模式不显示成本面板（`costStats?.is_cloud` 检查）
- 57/57 后端测试通过（含 6 条新增实时成本测试），无 TS 错误

### File List

- `backend/src/infra/llm_client.py` — generate() 返回 (content, LlmUsage) 元组
- `backend/src/infra/openai_client.py` — generate() 返回 (content, LlmUsage) 元组
- `backend/src/extraction/chapter_fact_extractor.py` — extract/segmented/single 返回 (ChapterFact, LlmUsage)
- `backend/src/extraction/entity_pre_scanner.py` — 更新 generate() 解构赋值
- `backend/src/services/world_structure_agent.py` — 更新 generate() 解构赋值
- `backend/src/services/analysis_service.py` — 累加 cost_stats + WS cost 字段推送
- `frontend/src/api/types.ts` — 新增 AnalysisCostStats 接口
- `frontend/src/stores/analysisStore.ts` — 新增 costStats state + WS 更新
- `frontend/src/pages/AnalysisPage.tsx` — 实时成本面板 + 完成费用汇总
- `backend/tests/test_realtime_cost.py` — 新建：6 条实时成本测试
