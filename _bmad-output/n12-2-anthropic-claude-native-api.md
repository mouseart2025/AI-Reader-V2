# Story N12.2: Claude / Anthropic 原生 API 支持

Status: draft

## Story

As a 用户,
I want 在云端 API 模式下选择 Anthropic 作为供应商并使用 Claude 系列模型,
So that 我可以用世界最先进的推理模型来分析小说，获得更高的提取质量。

## Background

Anthropic API 与 OpenAI-兼容格式有本质区别，无法复用 `OpenAICompatibleClient`：

| 维度 | OpenAI-兼容 | Anthropic |
|------|------------|-----------|
| Auth Header | `Authorization: Bearer {key}` | `x-api-key: {key}` + `anthropic-version: 2023-06-01` |
| 端点 | `/chat/completions` | `/messages` |
| system 位置 | messages 数组第一个 `system` role | 顶层 `system` 字段 |
| 响应 content | `choices[0].message.content` | `content[0].text` |
| token 统计 | `usage.prompt_tokens` | `usage.input_tokens` |
| stop 原因 | `choices[0].finish_reason` | `stop_reason`（`end_turn` / `max_tokens`） |
| 流式 SSE | `data: {choices[0].delta.content}` | 多种事件类型（content_block_delta 等） |
| 模型 context | 按模型不同 | 所有 claude-3.x/4.x 均为 200K |

## Acceptance Criteria

1. **AC-1**: Given 用户选择 Anthropic 供应商并填入 API Key，When 点击"验证"，Then 系统用 Anthropic 鉴权格式调用 `GET /v1/models` 验证连通性
2. **AC-2**: Given 云端模式配置为 Anthropic + API Key，When 触发小说分析，Then `ChapterFactExtractor` 通过 `AnthropicClient.generate()` 调用 `/messages` 端点并正确解析结构化输出
3. **AC-3**: Given Anthropic 模式，When 调用 `generate()` 且输出被截断（`stop_reason="max_tokens"`），Then 自动尝试 JSON 修复（复用 `_repair_truncated_json`）
4. **AC-4**: Given Anthropic 模式，When 触发 Q&A 对话流式输出，Then `generate_stream()` 正确解析 Anthropic SSE 事件流并 yield token
5. **AC-5**: Given 保存 Anthropic 配置，When 切换模式，Then `config.py` 正确存储 `LLM_PROVIDER="openai"`（复用现有字段，用 provider id 区分）且 `AnthropicClient` 被实例化
6. **AC-6**: Anthropic 模型的 context window 自动设为 200000（不走 Ollama 检测路径）
7. **AC-7**: 前端选择 Anthropic 供应商时，在配置区域显示提示语："Claude 使用独立鉴权格式，API Key 将以 x-api-key 头传递"

## Tasks / Subtasks

- [ ] Task 1: 新建 `backend/src/infra/anthropic_client.py` (AC: #2, #3, #4)
  - [ ] 1.1 `AnthropicClient.__init__(base_url, api_key, model)` — 与 `OpenAICompatibleClient` 接口一致
  - [ ] 1.2 `_headers()` — 返回 `{"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}`
  - [ ] 1.3 `generate(system, prompt, format, temperature, max_tokens, timeout, num_ctx)` 实现：
    - 组装 Anthropic Messages payload：`{"model": ..., "max_tokens": ..., "system": system, "messages": [{"role": "user", "content": prompt}]}`
    - 调用 `POST {base_url}/v1/messages`
    - 从 `content[0]["text"]` 提取响应
    - 从 `usage.input_tokens` / `usage.output_tokens` 提取 token 统计
    - 当 `stop_reason == "max_tokens"` 且 `format` 非空时，调用 `_repair_truncated_json()`
    - 当 `format` 非空时，解析 JSON（复用 `_extract_json`）
  - [ ] 1.4 `generate_stream(system, prompt, timeout)` 实现：
    - 调用 `POST {base_url}/v1/messages`，`"stream": true`
    - 解析 Anthropic SSE 事件流：监听 `event: content_block_delta` + `{"type": "delta", "delta": {"type": "text_delta", "text": "..."}}`
    - yield token string；收到 `event: message_stop` 或 `message_delta` 含 `stop_reason` 时 break
  - [ ] 1.5 复用 `_get_cloud_semaphore()`（并发限制 3，与 OpenAI client 共享）
  - [ ] 1.6 异常处理：`httpx.TimeoutException` → `LLMTimeoutError`；`HTTPStatusError` → `LLMError`

- [ ] Task 2: 更新 `backend/src/infra/llm_client.py` — 工厂函数 (AC: #5)
  - [ ] 2.1 `get_llm_client()` 中新增：当 `LLM_PROVIDER == "openai"` 且检测到 `LLM_BASE_URL` 包含 `anthropic.com` 或 config 中 provider_id 为 `"anthropic"` 时，实例化 `AnthropicClient`
  - [ ] 2.2 更简洁方案：在 `config.py` 增加 `LLM_PROVIDER_FORMAT: str = "openai"` 变量（`"openai"` 或 `"anthropic"`），`get_llm_client()` 依据此变量选择 Client 类

- [ ] Task 3: 更新 `backend/src/infra/config.py` (AC: #5, #6)
  - [ ] 3.1 新增全局变量 `LLM_PROVIDER_FORMAT: str = "openai"`（对应 API 协议格式）
  - [ ] 3.2 `update_cloud_config(provider, api_key, base_url, model)` 中，当 `provider == "anthropic"` 时将 `LLM_PROVIDER_FORMAT = "anthropic"`，否则 `"openai"`
  - [ ] 3.3 `switch_to_ollama()` 中重置 `LLM_PROVIDER_FORMAT = "openai"`

- [ ] Task 4: 更新 `backend/src/api/routes/settings.py` (AC: #1, #6)
  - [ ] 4.1 `CLOUD_PROVIDERS` 新增 Anthropic 条目：
    ```python
    {
        "id": "anthropic",
        "name": "Anthropic (Claude)",
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-5",
        "models": ["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"],
        "api_format": "anthropic",   # 新字段，标识非 OpenAI 兼容
    }
    ```
  - [ ] 4.2 `POST /api/settings/cloud/validate` — 当 `base_url` 包含 `anthropic.com` 或检测到 `api_format=="anthropic"` 时，改用 Anthropic 鉴权格式调用 `GET {base_url}/v1/models`（传 `x-api-key` + `anthropic-version` 头）
  - [ ] 4.3 `POST /api/settings/cloud/config`（已有端点）— 将 `provider` 字段传入 `update_cloud_config()`（已有，无需改）

- [ ] Task 5: 更新上下文窗口检测 (AC: #6)
  - [ ] 5.1 `backend/src/infra/context_budget.py` 中 `detect_and_update_context_window()` — 当 `LLM_PROVIDER_FORMAT == "anthropic"` 时直接返回 200000，跳过 Ollama 检测逻辑

- [ ] Task 6: 前端 `types.ts` + `SettingsPage.tsx` (AC: #7)
  - [ ] 6.1 `CloudProvider` 接口新增 `api_format?: string` 可选字段
  - [ ] 6.2 `SettingsPage.tsx` — 云端配置区域：当 `cloudProviders.find(p => p.id === cloudProvider)?.api_format === "anthropic"` 时，在 API Key 输入框下方显示提示文字（`text-muted-foreground text-xs`）：「Claude API 使用独立鉴权格式，您的 Key 将通过 x-api-key 请求头传递，而非 Bearer Token」

- [ ] Task 7: 测试
  - [ ] 7.1 单元测试 `AnthropicClient.generate()` — mock httpx，验证请求结构和响应解析
  - [ ] 7.2 单元测试 `AnthropicClient.generate_stream()` — mock SSE 流，验证 token yield
  - [ ] 7.3 `validate` 端点在 Anthropic URL 时使用正确鉴权头
  - [ ] 7.4 `npm run build` 无 TS 错误

## Dev Notes

### AnthropicClient 接口对齐

`AnthropicClient` 需与 `OpenAICompatibleClient` 保持完全相同的方法签名：
- `generate(system, prompt, format, temperature, max_tokens, timeout, num_ctx) -> tuple[str|dict, LlmUsage]`
- `generate_stream(system, prompt, timeout) -> AsyncIterator[str]`
- `num_ctx` 参数接受但忽略（Anthropic 不支持动态 context window）

### `format` 参数处理

当调用方传 `format={"type": "json_object"}` 或具体 JSON Schema 时，Anthropic API 不支持原生 JSON 模式（只有 `claude-3-5-sonnet` 支持 tool_use 模拟）。

建议方案：**忽略 format 参数，直接解析 LLM 输出的 JSON 文本**（与 Ollama client 的 `_extract_json` 一致）。大多数 Claude 模型能可靠输出符合 schema 的 JSON，无需强制 JSON 模式。

### Anthropic SSE 事件格式（关键参考）

```
event: message_start
data: {"type":"message_start","message":{"id":"...","usage":{...}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{...}}

event: message_stop
data: {"type":"message_stop"}
```

流式解析伪代码：
```python
async for line in resp.aiter_lines():
    if line.startswith("data: "):
        data = json.loads(line[6:])
        if data.get("type") == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                yield delta.get("text", "")
        elif data.get("type") == "message_stop":
            break
```

### Config 变量方案选择

推荐在 `config.py` 新增 `LLM_PROVIDER_FORMAT` 而非在 URL 上做字符串匹配，理由：用户可能使用 Anthropic-compatible 代理（如 LiteLLM），URL 不一定含 `anthropic.com`。

### 文件改动汇总

| 文件 | 改动类型 |
|------|---------|
| `backend/src/infra/anthropic_client.py` | 新建 |
| `backend/src/infra/llm_client.py` | 修改工厂函数 |
| `backend/src/infra/config.py` | 新增变量 + 修改函数 |
| `backend/src/api/routes/settings.py` | 新增 CLOUD_PROVIDERS 条目 + 修改 validate 逻辑 |
| `backend/src/infra/context_budget.py` | 修改检测逻辑 |
| `frontend/src/api/types.ts` | 新增 api_format 字段 |
| `frontend/src/pages/SettingsPage.tsx` | 新增提示文字 |
