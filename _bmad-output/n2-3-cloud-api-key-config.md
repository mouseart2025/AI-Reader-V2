# Story N2.3: 云端 LLM API Key 配置

Status: review

## Story

As a 用户,
I want 通过界面配置云端 LLM 的 API Key 和提供商,
So that 我可以使用 DeepSeek 等云端服务进行分析。

## Acceptance Criteria

1. **AC-1**: Given 用户在设置页选择"云端 API"模式，When 输入 API Key 和选择提供商，Then 提供商下拉列表包含 DeepSeek、OpenAI 等预设（自动填充 Base URL 和默认模型）
2. **AC-2**: 输入 API Key 后可点击"验证"测试连通性，验证成功显示 ✅，失败显示具体错误
3. **AC-3**: API Key 通过 `keyring` 库存储到系统密钥库，绝不以明文写入配置文件或数据库
4. **AC-4**: fallback: 无系统密钥库时使用 user_state 表存储（带 obfuscation 标记）

## Tasks / Subtasks

- [x] Task 1: 后端云端提供商预设 + API Key 存储服务 (AC: #1, #3, #4)
  - [x] 1.1 `settings.py` — 定义 `CLOUD_PROVIDERS` 预设列表（DeepSeek/OpenAI/自定义），含 base_url、default_model
  - [x] 1.2 `settings.py` — 新增 `GET /api/settings/cloud/providers` 返回预设列表
  - [x] 1.3 新建 `backend/src/infra/secret_store.py` — `save_api_key(key)` / `load_api_key()` / `delete_api_key()`，优先 keyring，fallback user_state
  - [x] 1.4 `settings.py` — 新增 `POST /api/settings/cloud/config` 保存云端配置（provider, base_url, model, api_key）
  - [x] 1.5 `settings.py` — 新增 `GET /api/settings/cloud/config` 读取云端配置（api_key 返回掩码）

- [x] Task 2: 后端 API Key 验证端点 (AC: #2)
  - [x] 2.1 `settings.py` — 新增 `POST /api/settings/cloud/validate` 端点，用提交的 key+base_url 调用 `/models` API 测试连通性
  - [x] 2.2 返回 `{valid: bool, error?: string}`

- [x] Task 3: 后端运行时配置热更新 (AC: #1)
  - [x] 3.1 保存云端配置后更新 `src.infra.config` 模块级变量（LLM_PROVIDER, LLM_API_KEY 等），使新的 LLM 调用立即生效
  - [x] 3.2 `config.py` — 新增 `update_cloud_config()` 函数

- [x] Task 4: 前端类型 + API 客户端 (AC: #1, #2)
  - [x] 4.1 `types.ts` — 新增 `CloudProvider` 和 `CloudConfig` 接口
  - [x] 4.2 `client.ts` — 新增 `fetchCloudProviders()`, `fetchCloudConfig()`, `saveCloudConfig()`, `validateCloudApi()` 函数

- [x] Task 5: 前端 SettingsPage 云端配置 UI (AC: #1, #2, #3)
  - [x] 5.1 `SettingsPage.tsx` — 在 LLM 配置区域新增"云端 API 配置"折叠面板
  - [x] 5.2 提供商下拉选择，选择后自动填充 base_url 和 model
  - [x] 5.3 API Key 输入框（password 类型）+ "验证"按钮 + 状态指示
  - [x] 5.4 "保存"按钮保存全部配置，成功后刷新状态

- [x] Task 6: 后端测试
  - [x] 6.1 测试 secret_store keyring 存储 + fallback
  - [x] 6.2 测试云端提供商预设列表
  - [x] 6.3 测试 API 验证端点

- [x] Task 7: TypeScript 编译 + 后端测试验证
  - [x] 7.1 `npm run build` 确认无新增 TS 错误
  - [x] 7.2 `uv run pytest tests/ -v` 确认全部测试通过

## Dev Notes

### 云端提供商预设

| 提供商 | Base URL | 默认模型 |
|--------|----------|----------|
| DeepSeek | https://api.deepseek.com | deepseek-chat |
| OpenAI | https://api.openai.com/v1 | gpt-4o-mini |
| 自定义 | (用户输入) | (用户输入) |

### keyring 使用

```python
import keyring
keyring.set_password("ai-reader-v2", "llm-api-key", api_key)
api_key = keyring.get_password("ai-reader-v2", "llm-api-key")
```

macOS 使用 Keychain，Linux 使用 SecretService (GNOME Keyring)，Windows 使用 Credential Manager。

### 运行时配置热更新

当前 `config.py` 使用模块级常量。保存新配置后需要直接修改模块变量使其生效，避免重启。

### References

- [Source: backend/src/api/routes/settings.py] — 设置路由
- [Source: backend/src/infra/config.py] — 配置管理
- [Source: frontend/src/pages/SettingsPage.tsx] — 设置页面

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

- keyring 未安装 → `uv add keyring` 安装 25.7.0
- keyring 延迟导入导致 `patch("src.infra.secret_store.keyring")` 失败 → 改用 `patch.dict("sys.modules", {"keyring": mock_kr})`
- 前端 TS 错误均为已有代码，非本次改动引入

### Completion Notes List

- 云端提供商预设（DeepSeek / OpenAI / 自定义）完成
- keyring 安全存储 + SQLite base64 fallback 完成
- 云端 API 验证端点 + 连通性测试完成
- 运行时配置热更新（无需重启）完成
- 前端云端配置面板（折叠式、提供商自动填充、密码框、验证按钮）完成
- 19/19 后端测试通过（含 8 条新增 N2.3 测试）

### File List

- `backend/src/infra/secret_store.py` — 新建：keyring + SQLite fallback API Key 存储
- `backend/src/infra/config.py` — 新增 `update_cloud_config()` 热更新函数
- `backend/src/api/routes/settings.py` — 新增云端提供商预设、4 个云端配置端点
- `frontend/src/api/types.ts` — 新增 `CloudProvider`、`CloudConfig` 接口
- `frontend/src/api/client.ts` — 新增 `fetchCloudProviders`、`fetchCloudConfig`、`saveCloudConfig`、`validateCloudApi` 函数
- `frontend/src/pages/SettingsPage.tsx` — 新增云端 API 配置折叠面板
- `backend/tests/test_settings.py` — 新增 8 条 N2.3 测试
