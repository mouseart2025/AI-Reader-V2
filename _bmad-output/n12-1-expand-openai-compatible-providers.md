# Story N12.1: 扩展 OpenAI 兼容供应商列表

Status: draft

## Story

As a 用户,
I want 云端 API 供应商列表包含更多主流模型服务商（MiniMax、阿里云百炼、Moonshot/Kimi、智谱、SiliconFlow、零一万物、Google Gemini 等）,
So that 我可以用已有的 API Key 直接接入这些服务，无需手动填写 Base URL。

## Background

当前 `CLOUD_PROVIDERS`（`backend/src/api/routes/settings.py`）仅含 3 个条目：DeepSeek / OpenAI / 自定义。这些供应商全部兼容 OpenAI API 格式，扩展只需要在后端列表里追加条目，前端已经是动态读取不需要修改。

新增字段 `models: list[str]`（可选，按推荐顺序排列），供前端未来展示"模型预设下拉"。

## Acceptance Criteria

1. **AC-1**: Given 用户打开设置页 → 云端 API Tab，When 点击供应商下拉，Then 列表按顺序包含（原有）DeepSeek、OpenAI 及以下新增供应商：MiniMax、阿里云百炼（Qwen）、Moonshot/Kimi、智谱 GLM、SiliconFlow、零一万物（Yi）、Google Gemini、自定义
2. **AC-2**: 选择某供应商后，Base URL 和默认模型自动填充为该供应商的预设值
3. **AC-3**: 每个供应商配置包含 `models` 数组（备用，本 Story 不展示但后端返回）
4. **AC-4**: 旧的 DeepSeek / OpenAI / 自定义条目保持不变（向后兼容）
5. **AC-5**: `GET /api/settings/cloud/providers` 返回扩展后的列表，响应结构新增 `models` 字段

## Tasks / Subtasks

- [ ] Task 1: 后端 `settings.py` — 扩展 `CLOUD_PROVIDERS` (AC: #1, #2, #3, #4, #5)
  - [ ] 1.1 在 `CLOUD_PROVIDERS` 列表中新增以下条目（均为 OpenAI-兼容格式）：

    | id | name | base_url | default_model | models |
    |----|------|----------|---------------|--------|
    | minimax | MiniMax | https://api.minimax.chat/v1 | MiniMax-M2.5 | ["MiniMax-M2.5", "MiniMax-M2.5-highspeed", "MiniMax-M2.1", "MiniMax-Text-01"] |
    | qwen | 阿里云百炼（Qwen） | https://dashscope.aliyuncs.com/compatible-mode/v1 | qwen-max | ["qwen-max", "qwen-plus", "qwen-turbo", "qwen-long"] |
    | moonshot | Moonshot / Kimi | https://api.moonshot.cn/v1 | moonshot-v1-32k | ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"] |
    | zhipu | 智谱 GLM | https://open.bigmodel.cn/api/paas/v4 | glm-4 | ["glm-4", "glm-4-air", "glm-4-flash"] |
    | siliconflow | SiliconFlow | https://api.siliconflow.cn/v1 | Qwen/Qwen2.5-72B-Instruct | ["Qwen/Qwen2.5-72B-Instruct", "deepseek-ai/DeepSeek-V3", "Qwen/QwQ-32B"] |
    | yi | 零一万物（Yi） | https://api.lingyiwanwu.com/v1 | yi-large | ["yi-large", "yi-large-turbo", "yi-medium"] |
    | gemini | Google Gemini | https://generativelanguage.googleapis.com/v1beta/openai | gemini-2.0-flash | ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"] |

  - [ ] 1.2 同步更新原有 `deepseek` / `openai` 条目，补充 `models` 字段（deepseek: ["deepseek-chat", "deepseek-reasoner"]；openai: ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"]）
  - [ ] 1.3 在 `CLOUD_PROVIDERS` 每个字典条目中加入 `"models": [...]` 字段
  - [ ] 1.4 `get_cloud_providers()` 路由直接返回带 `models` 的列表（无需改路由逻辑）

- [ ] Task 2: 前端 `types.ts` — `CloudProvider` 接口新增 `models` 字段 (AC: #5)
  - [ ] 2.1 `CloudProvider.models?: string[]` 可选字段（向后兼容）

- [ ] Task 3: 验证测试
  - [ ] 3.1 `GET /api/settings/cloud/providers` 返回 10 个条目（含新增 7 个）
  - [ ] 3.2 每个条目包含 id / name / base_url / default_model / models 字段
  - [ ] 3.3 `npm run build` 无 TS 错误

## Dev Notes

### 排序规则

`CLOUD_PROVIDERS` 顺序建议：DeepSeek → MiniMax → Qwen → Moonshot → Zhipu → SiliconFlow → Yi → OpenAI → Gemini → 自定义

理由：国产供应商放前面（目标用户为中文用户），OpenAI/Gemini 放靠后，"自定义"永远放最后。

### Gemini OpenAI 兼容端点说明

Google Gemini 从 2025 年起提供 OpenAI-兼容端点：
- Base URL: `https://generativelanguage.googleapis.com/v1beta/openai`
- Auth: `Authorization: Bearer {GOOGLE_API_KEY}`（与标准 OpenAI 格式一致）
- 无需额外处理，复用 `OpenAICompatibleClient` 即可

### 影响范围

**仅后端 1 个文件** (`settings.py`) + 前端 1 个类型文件 (`types.ts`) 中加一个可选字段。改动极小，零风险。
