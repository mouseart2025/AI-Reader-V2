# Story N12.3: 前端供应商模型预设选择器

Status: draft

## Story

As a 用户,
I want 选择供应商后能从该供应商支持的模型列表中选择，而不是手动输入模型名,
So that 我不需要记住各家供应商的具体模型名称，减少配置错误。

## Background

N12.1 在后端为每个供应商添加了 `models` 数组，N12.2 在前端 `CloudProvider` 接口加了 `api_format` 字段。本 Story 在此基础上增强前端 UI：

- 当供应商有 `models` 列表时，将模型输入框替换为带预设的 select + 可编辑 input 组合
- 对供应商按国产/海外分组展示
- 显示每个供应商的推荐用途标签

## Acceptance Criteria

1. **AC-1**: Given 选择含 `models` 列表的供应商，When 模型字段渲染，Then 显示下拉预设选择器（`<select>`），选项来自 `provider.models`；下拉末尾有"自定义..."选项，点击后切换为自由输入框
2. **AC-2**: Given 选择供应商，When `default_model` 非空，Then 模型字段自动填入 `default_model`（已有逻辑无需改动，确保与新选择器兼容）
3. **AC-3**: Given 打开供应商下拉，Then 供应商按 `国产模型` / `海外模型` 两个 optgroup 分组显示（`自定义` 始终在末尾独立）
4. **AC-4**: Given 供应商列表，Then 每个供应商名称后显示小标签（`text-xs` 徽章）标注推荐用途（如"推荐"、"快速"、"长文本"），标签内容由前端硬编码映射
5. **AC-5**: `npm run build` 无 TS 错误，无 lint 警告

## Tasks / Subtasks

- [ ] Task 1: 前端模型预设选择器组件 (AC: #1, #2)
  - [ ] 1.1 在 `SettingsPage.tsx` 云端配置区域，将模型输入 `<input type="text" value={cloudModel}>` 改为：
    - 当 `selectedProvider?.models && selectedProvider.models.length > 0` 时：渲染 `<select>` + 最后一个 option 为"自定义..."
    - 当选择"自定义..."时，`setIsCustomModel(true)` 切换到 `<input type="text">`
    - 当 `selectedProvider?.models` 为空或未定义时，保持原有 `<input type="text">`
  - [ ] 1.2 本地 state 新增 `isCustomModel: boolean`，切换供应商时重置为 `false`

- [ ] Task 2: 供应商分组 optgroup (AC: #3)
  - [ ] 2.1 前端定义 `DOMESTIC_PROVIDER_IDS = ["deepseek", "minimax", "qwen", "moonshot", "zhipu", "siliconflow", "yi"]`
  - [ ] 2.2 `INTERNATIONAL_PROVIDER_IDS = ["openai", "anthropic", "gemini"]`
  - [ ] 2.3 渲染供应商 `<select>` 时使用 `<optgroup label="国产模型">` / `<optgroup label="海外模型">` / 最后 `<option value="custom">自定义</option>`

- [ ] Task 3: 供应商推荐标签 (AC: #4)
  - [ ] 3.1 前端定义 `PROVIDER_TAGS: Record<string, string>` 映射：
    ```ts
    const PROVIDER_TAGS: Record<string, string> = {
      deepseek: "推荐",
      minimax: "长文本",
      qwen: "多模态",
      moonshot: "128K",
      zhipu: "免费额度",
      siliconflow: "开源模型",
      yi: "推理",
      openai: "国际标准",
      anthropic: "最强推理",
      gemini: "多模态",
    }
    ```
  - [ ] 3.2 在供应商 `<option>` 文字后追加标签（或在 option 外的说明文字区域显示，因 option 不支持 HTML 样式，改为在选择后的供应商名称旁展示 `<Badge variant="secondary">` 徽章）

- [ ] Task 4: 验证
  - [ ] 4.1 手动测试：选择 MiniMax → 模型列表出现 → 选 "自定义..." → 切换为输入框
  - [ ] 4.2 手动测试：选择"自定义"供应商 → 无模型预设 → 直接显示输入框（不回归）
  - [ ] 4.3 `npm run build` 通过

## Dev Notes

### select + 自定义 input 的状态逻辑

```tsx
const [isCustomModel, setIsCustomModel] = useState(false)
const selectedProvider = cloudProviders.find(p => p.id === cloudProvider)
const hasModelPresets = selectedProvider?.models && selectedProvider.models.length > 0

// 切换供应商时重置
const handleProviderChange = useCallback((providerId: string) => {
  setIsCustomModel(false)
  // ... 已有逻辑
}, [cloudProviders])

// 渲染
{hasModelPresets && !isCustomModel ? (
  <select value={cloudModel} onChange={e => {
    if (e.target.value === "__custom__") { setIsCustomModel(true); setCloudModel("") }
    else setCloudModel(e.target.value)
  }}>
    {selectedProvider!.models!.map(m => <option key={m} value={m}>{m}</option>)}
    <option value="__custom__">自定义...</option>
  </select>
) : (
  <input type="text" value={cloudModel} onChange={e => setCloudModel(e.target.value)} />
)}
```

### 标签显示位置

`<option>` 内无法放 HTML。推荐做法：在供应商 select 外，选中后在旁边 inline 显示徽章：

```tsx
<div className="flex items-center gap-2">
  <select ...>{/* 供应商选项 */}</select>
  {PROVIDER_TAGS[cloudProvider] && (
    <span className="text-xs px-1.5 py-0.5 bg-muted rounded text-muted-foreground">
      {PROVIDER_TAGS[cloudProvider]}
    </span>
  )}
</div>
```

### 优先级说明

本 Story 是 UI 增强，不影响功能正确性。N12.1 和 N12.2 完成后可独立实施，也可跳过。
