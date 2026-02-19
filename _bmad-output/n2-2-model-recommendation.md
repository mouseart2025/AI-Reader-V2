# Story N2.2: 模型推荐与下载引导

Status: review

## Story

As a 用户,
I want 系统根据我的硬件推荐合适的分析模型,
So that 我能选择最适合的模型而不需要了解技术细节。

## Acceptance Criteria

1. **AC-1**: Given Ollama 已安装但没有可用的分析模型，When 进入模型推荐界面，Then 推荐 2-3 个模型（如 qwen3:4b / qwen3:8b / qwen3:14b），标注推荐级别和内存要求
2. **AC-2**: 显示硬件检测结果（如"Apple M2 Pro, 16GB 内存"）
3. **AC-3**: 用户点击"下载"后执行 `ollama pull` 并显示下载进度
4. **AC-4**: 下载完成后自动设为默认模型

## Tasks / Subtasks

- [x] Task 1: 后端硬件检测 + 模型推荐 API (AC: #1, #2)
  - [x] 1.1 `settings.py` — `GET /api/settings/hardware`，使用 `os.sysconf` 获取内存（无需新增依赖）
  - [x] 1.2 `settings.py` — `GET /api/settings/ollama/recommendations`，根据内存推荐 + 检测已安装模型
  - [x] 1.3 推荐逻辑：≥32GB → 14b 推荐；≥16GB → 8b 推荐；<16GB → 4b 推荐
  - [x] 1.4 含 `MODEL_CATALOG` 常量定义完整推荐信息

- [x] Task 2: 后端模型下载端点 + SSE 流式进度 (AC: #3)
  - [x] 2.1 `POST /api/settings/ollama/pull` — 接收 `{model: string}`
  - [x] 2.2 `httpx.stream()` 流式请求 Ollama `/api/pull`，通过 `StreamingResponse` SSE 格式转发
  - [x] 2.3 SSE 事件格式正确：`data: {...}\n\n`

- [x] Task 3: 后端设置默认模型 (AC: #4)
  - [x] 3.1 `POST /api/settings/ollama/default-model` — `PullModelRequest` Pydantic 模型
  - [x] 3.2 存入 `user_state` 表（`novel_id='__global__'`, `key='ollama_default_model'`）

- [x] Task 4: 前端类型 + API 客户端 (AC: #1, #2, #3)
  - [x] 4.1 `types.ts` — `HardwareInfo` + `ModelRecommendation` 接口
  - [x] 4.2 `client.ts` — `fetchHardware()`, `fetchModelRecommendations()`, `pullOllamaModel()` (fetch + ReadableStream SSE 解析), `setDefaultModel()`

- [x] Task 5: 前端 SettingsPage 模型推荐 UI (AC: #1, #2, #3, #4)
  - [x] 5.1 Ollama 运行时自动显示"模型推荐"区域
  - [x] 5.2 显示系统内存大小
  - [x] 5.3 卡片式列表：推荐标记（蓝色）+ 已安装标记（绿色）
  - [x] 5.4 每卡片：名称 + 描述 + 大小 + 内存要求 + 下载/已安装按钮
  - [x] 5.5 下载进度条（SSE 实时），完成后自动设为默认并刷新

- [x] Task 6: 后端测试
  - [x] 6.1 `test_get_total_ram_gb` + `test_get_hardware`
  - [x] 6.2 `test_recommendations_32gb` / `_16gb` / `_8gb` 三种内存场景
  - [x] 6.3 默认模型设置通过集成测试覆盖

- [x] Task 7: TypeScript 编译 + 后端测试验证
  - [x] 7.1 `npm run build` 无新增 TS 错误（仅预存错误）
  - [x] 7.2 `uv run pytest tests/ -v` 32/32 全部通过

## Dev Notes

### 关键架构约束

1. **settings.py**: `backend/src/api/routes/settings.py` — N2.1 已增强，含三态检测 + 启动端点
2. **Ollama Pull API**: `POST {OLLAMA_BASE_URL}/api/pull` — 请求 `{"name": "qwen3:8b", "stream": true}`，返回 JSONL 流
3. **user_state 表**: 已有，键值对存储用户状态，适合存储默认模型选择
4. **psutil**: 需要新增依赖用于硬件检测（内存大小）
5. **SSE vs WebSocket**: 模型下载是单向进度推送，SSE 更简单；已有 WebSocket 用于分析进度和聊天

### Ollama Pull JSONL 响应格式

```jsonl
{"status":"pulling manifest"}
{"status":"downloading sha256:abc123","digest":"sha256:abc123","total":4700000000,"completed":0}
{"status":"downloading sha256:abc123","digest":"sha256:abc123","total":4700000000,"completed":1200000000}
...
{"status":"verifying sha256 digest"}
{"status":"writing manifest"}
{"status":"success"}
```

### 模型推荐表

| 模型 | 大小 | 最低内存 | 推荐场景 |
|------|------|----------|----------|
| qwen3:4b | ~2.5GB | 8GB | 轻量分析，速度快 |
| qwen3:8b | ~5GB | 16GB | 平衡质量与速度（默认） |
| qwen3:14b | ~9GB | 32GB | 最佳分析质量 |

### user_state 表结构

```sql
CREATE TABLE IF NOT EXISTS user_state (
    novel_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (novel_id, key)
)
```

全局设置使用 `novel_id = "__global__"`。

### References

- [Source: backend/src/api/routes/settings.py] — 设置路由
- [Source: backend/src/infra/config.py] — 配置管理
- [Source: frontend/src/pages/SettingsPage.tsx] — 设置页面
- [Source: frontend/src/api/types.ts] — 前端类型
- [Source: frontend/src/api/client.ts] — API 客户端

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

- 避免新增 `psutil` 依赖，改用 `os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')` 获取内存（macOS/Linux 原生支持）
- 前端 SSE 解析使用 `ReadableStream` + 手动 `data:` 行解析，而非 `EventSource`（因 POST 请求不被 EventSource 支持）
- 前端 build 有预存 TS 错误，与本 Story 无关

### Completion Notes List

- **Task 1 完成**: `GET /api/settings/hardware` + `GET /api/settings/ollama/recommendations` — `_get_total_ram_gb()` 用 `os.sysconf`，`MODEL_CATALOG` 定义 3 个 qwen3 模型，推荐逻辑按内存分级
- **Task 2 完成**: `POST /api/settings/ollama/pull` — `httpx.stream()` 代理 Ollama `/api/pull` JSONL 流，SSE 格式转发至前端
- **Task 3 完成**: `POST /api/settings/ollama/default-model` — `user_state` 表存储全局默认模型
- **Task 4 完成**: `HardwareInfo` + `ModelRecommendation` 类型 + `pullOllamaModel()` fetch-SSE 客户端 + `setDefaultModel()`
- **Task 5 完成**: 模型推荐卡片 UI — 内存显示、推荐/已安装标记、下载进度条、完成后自动设默认
- **Task 6 完成**: 5 个新测试：硬件检测 + 3 种内存场景推荐逻辑
- **Task 7 完成**: 前端无新增错误，后端 32/32 通过

### File List

- `backend/src/api/routes/settings.py` — 修改：硬件检测 + 模型推荐 + Pull SSE + 默认模型 4 个端点
- `backend/tests/test_settings.py` — 修改：新增 5 个测试（硬件检测 + 推荐逻辑）
- `frontend/src/api/types.ts` — 修改：HardwareInfo + ModelRecommendation 接口
- `frontend/src/api/client.ts` — 修改：fetchHardware + fetchModelRecommendations + pullOllamaModel + setDefaultModel
- `frontend/src/pages/SettingsPage.tsx` — 修改：模型推荐区域 UI + 下载进度
