# Story N2.4: AI 引擎设置面板

Status: review

## Story

As a 用户,
I want 在设置页有统一的 AI 引擎配置界面,
So that 我可以随时切换本地/云端模式和调整参数。

## Acceptance Criteria

1. **AC-1**: 显示两个模式 Tab：本地 Ollama（推荐）/ 云端 API
2. **AC-2**: 本地模式显示：Ollama 状态、已安装模型列表、模型选择下拉、测试连接按钮
3. **AC-3**: 云端模式显示：提供商选择、API Key（掩码）、模型、Base URL（复用 N2.3 已有面板）
4. **AC-4**: 高级选项：最大 Token 数（LLM_MAX_TOKENS）
5. **AC-5**: 模式切换立即生效，后端运行时更新（无需重启）
6. **AC-6**: "恢复默认"还原为本地 Ollama + qwen3:8b

## Tasks / Subtasks

- [x] Task 1: 后端新增模式切换 + 高级设置端点 (AC: #4, #5)
  - [x] 1.1 `settings.py` — 新增 `POST /api/settings/llm-mode` 切换 Ollama/云端模式
  - [x] 1.2 `settings.py` — 新增 `POST /api/settings/advanced` 保存高级设置（max_tokens）
  - [x] 1.3 `settings.py` — 新增 `POST /api/settings/restore-defaults` 恢复默认配置
  - [x] 1.4 `config.py` — 新增 `switch_to_ollama()` + `update_max_tokens()` 函数

- [x] Task 2: 后端 Ollama 模型选择端点 (AC: #2)
  - [x] 2.1 `settings.py` — 增强 `POST /api/settings/ollama/default-model` 同时更新 config.OLLAMA_MODEL

- [x] Task 3: 前端 API 客户端更新 (AC: #1~#6)
  - [x] 3.1 `client.ts` — 新增 `fetchSettings()`, `switchLlmMode()`, `saveAdvancedSettings()`, `restoreDefaults()` 函数

- [x] Task 4: 前端 SettingsPage 重构为统一 Tab 界面 (AC: #1~#6)
  - [x] 4.1 将"LLM 配置"和"云端 API 配置"合并为统一的"AI 引擎"区域
  - [x] 4.2 两个 Tab（本地/云端），当前活动模式高亮（蓝色底部边框）
  - [x] 4.3 本地 Tab：Ollama 状态 + 模型选择下拉 + 模型推荐内联区域
  - [x] 4.4 云端 Tab：复用已有的提供商/Key/URL/Model 表单
  - [x] 4.5 高级选项折叠区域：最大 Token 数（1024~131072）
  - [x] 4.6 "恢复默认"按钮 + "刷新状态"按钮

- [x] Task 5: 后端测试
  - [x] 5.1 测试模式切换端点（ollama + invalid mode）
  - [x] 5.2 测试高级设置端点（valid + out-of-range）
  - [x] 5.3 测试恢复默认端点

- [x] Task 6: TypeScript 编译 + 后端测试验证
  - [x] 6.1 24/24 后端测试全部通过
  - [x] 6.2 无新增 TS 编译错误

## Dev Notes

### 模式切换逻辑

- `switch_to_ollama()`: 重置 `LLM_PROVIDER="ollama"`, 清空 cloud 配置, 重置 LLM client
- `update_cloud_config()`: 已实现（N2.3），设置 `LLM_PROVIDER="openai"`
- 模式持久化在 user_state（key=`llm_mode`）

### 高级设置

当前仅 `LLM_MAX_TOKENS` 需要暴露（已在 config.py，默认 8192）。

### References

- [Source: backend/src/api/routes/settings.py]
- [Source: backend/src/infra/config.py]
- [Source: frontend/src/pages/SettingsPage.tsx]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

- `get_connection()` 是延迟导入的异步上下文管理器，需要用自定义 `_mock_get_connection()` 包装 mock
- `get_settings()` endpoint 改用 `from src.infra import config` 动态读取（避免模块级常量不更新）

### Completion Notes List

- 统一 Tab 界面完成：本地 Ollama / 云端 API 两个 Tab，一键切换
- 本地 Tab 含 Ollama 状态 + 模型选择下拉 + 内联模型推荐
- 云端 Tab 复用 N2.3 的提供商/Key/URL/Model 表单
- 高级选项（折叠）：最大 Token 数输入框
- "恢复默认"按钮：一键还原 Ollama + qwen3:8b + 8192 tokens
- 模式切换后端自动加载已保存的云端配置
- 24/24 后端测试通过（含 5 条新增 N2.4 测试）

### File List

- `backend/src/infra/config.py` — 新增 `switch_to_ollama()` + `update_max_tokens()`
- `backend/src/api/routes/settings.py` — 新增 3 个端点（llm-mode, advanced, restore-defaults），增强 get_settings + default-model
- `frontend/src/api/client.ts` — 新增 `fetchSettings()`, `switchLlmMode()`, `saveAdvancedSettings()`, `restoreDefaults()`
- `frontend/src/pages/SettingsPage.tsx` — 重构为统一 Tab 界面，新增模式切换 + 高级设置 + 恢复默认
- `backend/tests/test_settings.py` — 新增 5 条 N2.4 测试
