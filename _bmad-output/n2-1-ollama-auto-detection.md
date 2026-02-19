# Story N2.1: Ollama 自动检测与状态显示

Status: review

## Story

As a 用户,
I want 应用自动检测本地 Ollama 的安装和运行状态,
So that 我能了解当前环境是否就绪。

## Acceptance Criteria

1. **AC-1**: Given 用户打开设置页 > AI 引擎面板，When 页面加载时，Then 自动检测 Ollama 状态：已安装已运行 / 已安装未运行 / 未安装
2. **AC-2**: 显示可用模型列表（已下载的模型名称和大小）
3. **AC-3**: 未安装时提供"下载安装 Ollama"链接（打开 ollama.com）
4. **AC-4**: 已安装未运行时提供"启动 Ollama"按钮
5. **AC-5**: 检测过程 < 2 秒，失败时显示具体错误

## Tasks / Subtasks

- [x] Task 1: 后端增强 Ollama 检测 API (AC: #1, #2, #5)
  - [x] 1.1 `settings.py` — `_check_ollama()` 增加三态检测：通过 `shutil.which("ollama")` 检测安装状态，区分 `not_installed` / `installed_not_running` / `running`
  - [x] 1.2 返回 `ollama_status` 字段（枚举值：`"not_installed"` / `"installed_not_running"` / `"running"`），保留 `ollama_running` 向后兼容
  - [x] 1.3 模型列表增加大小信息：从 `/api/tags` 响应中提取 `size` 字段，返回 `available_models` 改为对象数组 `[{name, size, modified_at}]`
  - [x] 1.4 检测超时控制在 2 秒内（`httpx.AsyncClient(timeout=2.0)`）

- [x] Task 2: 后端新增 Ollama 启动端点 (AC: #4)
  - [x] 2.1 `settings.py` — 新增 `POST /api/settings/ollama/start` 端点
  - [x] 2.2 macOS: `subprocess.Popen(["open", "-a", "Ollama"])`；Linux/Windows: `subprocess.Popen(["ollama", "serve"])`
  - [x] 2.3 启动后等待最多 5 秒轮询 `/api/tags` 确认 Ollama 可达，返回启动结果

- [x] Task 3: 前端类型更新 (AC: #1, #2)
  - [x] 3.1 `types.ts` — `EnvironmentCheck` 增加 `ollama_status` 字段（联合类型）
  - [x] 3.2 `types.ts` — 新增 `OllamaModel` 接口 `{name: string, size: number, modified_at?: string}`
  - [x] 3.3 `types.ts` — `available_models` 改为 `(OllamaModel | string)[]` 兼容新旧格式
  - [x] 3.4 `client.ts` — 新增 `startOllama()` 函数

- [x] Task 4: 前端 SettingsPage Ollama 面板增强 (AC: #1, #2, #3, #4)
  - [x] 4.1 `SettingsPage.tsx` — Ollama 状态三态显示：绿/黄/红
  - [x] 4.2 未安装状态：显示"下载安装"按钮（打开 ollama.com/download）
  - [x] 4.3 已安装未运行：显示"启动 Ollama"按钮 + loading 状态，成功后自动刷新
  - [x] 4.4 模型列表显示名称 + 大小（`formatBytes` 复用）
  - [x] 4.5 错误信息显示（`envCheck.error`）

- [x] Task 5: 后端测试 (AC: #1, #5)
  - [x] 5.1 测试三态检测：running / installed_not_running / not_installed
  - [x] 5.2 测试 Ollama 启动端点：未安装返回错误 / 正常启动成功
  - [x] 5.3 测试模型列表含 name + size + modified_at

- [x] Task 6: TypeScript 编译 + 后端测试验证
  - [x] 6.1 `npm run build` 无新增 TS 错误（仅预存错误）
  - [x] 6.2 `uv run pytest tests/ -v` 27/27 全部通过

## Dev Notes

### 关键架构约束

1. **当前 settings.py**: `backend/src/api/routes/settings.py` — 已有 `_check_ollama()` 和 `_check_openai()`，两个方法 + health-check 路由
2. **当前 config.py**: `backend/src/infra/config.py` — 纯环境变量读取，`LLM_PROVIDER` / `OLLAMA_BASE_URL` / `OLLAMA_MODEL` 等
3. **当前 SettingsPage.tsx**: `frontend/src/pages/SettingsPage.tsx` — LLM 配置已有只读显示（模式/模型/状态/地址），Ollama 状态仅 running/not-running 两态
4. **EnvironmentCheck 类型**: `frontend/src/api/types.ts:123-136` — 需增加 `ollama_status` 和 `OllamaModel` 相关字段
5. **client.ts**: `frontend/src/api/client.ts` — `checkEnvironment()` 已存在，需新增 `startOllama()`

### 现有 _check_ollama 行为

当前 `_check_ollama()` 仅通过 httpx 请求 `{OLLAMA_BASE_URL}/api/tags` 判断 Ollama 是否运行。无法区分"未安装"和"已安装但未运行"。需增加 `shutil.which("ollama")` 检测二进制是否在 PATH 中。

### Ollama /api/tags 响应格式

```json
{
  "models": [
    {
      "name": "qwen3:8b",
      "model": "qwen3:8b",
      "modified_at": "2025-06-01T...",
      "size": 5365624832,
      "digest": "...",
      "details": {"format": "gguf", ...}
    }
  ]
}
```

`size` 字段为字节数，前端需格式化为 GB 显示。

### 启动 Ollama

- macOS 推荐 `open -a Ollama`（启动 Ollama.app GUI）
- Linux/Windows 推荐 `ollama serve`（启动后台服务）
- 启动是异步的，需轮询确认

### 向后兼容

`available_models` 字段从 `string[]` 改为 `OllamaModel[]`，但前端需兼容旧格式（直接字符串数组），因为 health-check 可能在旧后端运行。

### References

- [Source: backend/src/api/routes/settings.py] — 设置 API 路由
- [Source: backend/src/infra/config.py] — 配置管理
- [Source: frontend/src/pages/SettingsPage.tsx] — 设置页面 UI
- [Source: frontend/src/api/types.ts:123-136] — EnvironmentCheck 类型
- [Source: frontend/src/api/client.ts] — API 客户端

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6

### Debug Log References

- 前端 build 有预存 TS 错误（ChatPage, EncyclopediaPage, FactionsPage, MapPage, TimelinePage, analysisStore），与本 Story 无关
- `shutil.which("ollama")` 在 macOS/Linux 上检测 PATH 中的 ollama 二进制

### Completion Notes List

- **Task 1 完成**: `_check_ollama()` 重写为三态检测，`shutil.which` 判断安装状态，API 可达判断运行状态，`available_models` 改为含 name/size/modified_at 的对象数组，超时 2 秒
- **Task 2 完成**: `POST /api/settings/ollama/start` — macOS 用 `open -a Ollama`，其他平台用 `ollama serve`，轮询 5 秒等待就绪
- **Task 3 完成**: `OllamaModel` 接口 + `ollama_status` 联合类型 + `startOllama()` API 函数
- **Task 4 完成**: SettingsPage 三态 UI — 绿色运行中/黄色已安装未运行（+启动按钮）/红色未安装（+下载链接），模型列表含大小，错误显示
- **Task 5 完成**: 6 个测试覆盖三态检测 + 启动端点 + 模型大小信息
- **Task 6 完成**: 前端无新增 TS 错误，后端 27/27 pytest 通过

### File List

- `backend/src/api/routes/settings.py` — 修改：三态检测 + Ollama 启动端点
- `backend/tests/test_settings.py` — 新增：6 个设置 API 测试
- `frontend/src/api/types.ts` — 修改：OllamaModel 接口 + EnvironmentCheck 增强
- `frontend/src/api/client.ts` — 修改：新增 startOllama() 函数
- `frontend/src/pages/SettingsPage.tsx` — 修改：三态 Ollama 面板 + 启动/下载按钮
