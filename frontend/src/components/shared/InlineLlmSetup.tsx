/**
 * InlineLlmSetup — 内联 AI 引擎配置器
 * 两列卡片布局：云端 API（推荐） / 本地 Ollama
 * 用于分析页，当 LLM 未配置时替代原有黄色警告
 */

import { useCallback, useEffect, useState } from "react"
import { Cloud, HardDrive, Check, Loader2, ExternalLink } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  checkEnvironment,
  fetchCloudProviders,
  saveCloudConfig,
  validateCloudApi,
  switchLlmMode,
} from "@/api/client"
import type { CloudProvider, EnvironmentCheck } from "@/api/types"
import { useLlmInfoStore } from "@/stores/llmInfoStore"

interface InlineLlmSetupProps {
  onReady: () => void
}

// Top recommended providers
const RECOMMENDED_IDS = ["deepseek", "minimax", "qwen"]

export function InlineLlmSetup({ onReady }: InlineLlmSetupProps) {
  const [mode, setMode] = useState<"cloud" | "local" | null>(null)
  const [providers, setProviders] = useState<CloudProvider[]>([])
  const [env, setEnv] = useState<EnvironmentCheck | null>(null)

  const [checking, setChecking] = useState(false)

  // Cloud state
  const [selectedProvider, setSelectedProvider] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [validating, setValidating] = useState(false)
  const [validResult, setValidResult] = useState<{ valid: boolean; error?: string } | null>(null)

  // Load providers and env on mount
  useEffect(() => {
    fetchCloudProviders()
      .then((res) => setProviders(res.providers))
      .catch(() => {})
    checkEnvironment()
      .then(setEnv)
      .catch(() => {})
  }, [])

  const currentProvider = providers.find((p) => p.id === selectedProvider)

  const handleValidateAndSave = useCallback(async () => {
    if (!currentProvider || !apiKey.trim()) return
    setValidating(true)
    setValidResult(null)

    try {
      // Validate API key
      const result = await validateCloudApi(
        currentProvider.base_url,
        apiKey.trim(),
        currentProvider.id
      )
      if (!result.valid) {
        setValidResult({ valid: false, error: result.error || "验证失败" })
        return
      }

      // Save config
      await saveCloudConfig({
        provider: currentProvider.id,
        base_url: currentProvider.base_url,
        model: currentProvider.default_model,
        api_key: apiKey.trim(),
      })

      // Switch to cloud mode
      await switchLlmMode("openai")

      // Refresh global state
      useLlmInfoStore.getState().fetch(true)

      setApiKey("")
      setValidResult({ valid: true })

      // Notify parent
      setTimeout(onReady, 500)
    } catch (err) {
      setValidResult({
        valid: false,
        error: err instanceof Error ? err.message : "配置失败",
      })
    } finally {
      setValidating(false)
    }
  }, [currentProvider, apiKey, onReady])

  const ollamaStatus = env?.ollama_status ?? "not_installed"

  // Mode selection screen
  if (mode === null) {
    return (
      <div className="rounded-xl border bg-card p-6">
        <h3 className="text-base font-semibold mb-1">配置 AI 引擎</h3>
        <p className="text-sm text-muted-foreground mb-5">
          分析小说需要 AI 引擎支持，请选择一种方式：
        </p>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {/* Cloud card */}
          <button
            onClick={() => setMode("cloud")}
            className="group relative rounded-lg border-2 border-transparent bg-muted/50 p-5 text-left transition hover:border-blue-500/50 hover:bg-blue-500/5"
          >
            <div className="absolute top-3 right-3 rounded-full bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-600 dark:text-blue-400">
              推荐
            </div>
            <Cloud className="h-8 w-8 text-blue-500 mb-3" />
            <h4 className="font-semibold">☁️ 云端 API</h4>
            <p className="text-xs text-muted-foreground mt-1">
              30 秒配置，无需安装，分析速度快
            </p>
          </button>

          {/* Local card */}
          <button
            onClick={() => setMode("local")}
            className="group rounded-lg border-2 border-transparent bg-muted/50 p-5 text-left transition hover:border-green-500/50 hover:bg-green-500/5"
          >
            <HardDrive className="h-8 w-8 text-green-500 mb-3" />
            <h4 className="font-semibold">💻 本地 Ollama</h4>
            <p className="text-xs text-muted-foreground mt-1">
              隐私优先，数据不出本机
            </p>
          </button>
        </div>
      </div>
    )
  }

  // Cloud config screen
  if (mode === "cloud") {
    return (
      <div className="rounded-xl border bg-card p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold">☁️ 云端 API 配置</h3>
          <button
            onClick={() => setMode(null)}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            ← 返回
          </button>
        </div>

        <div className="space-y-4">
          {/* Provider select */}
          <div>
            <label className="text-sm font-medium mb-1.5 block">AI 提供商</label>
            <Select value={selectedProvider} onValueChange={setSelectedProvider}>
              <SelectTrigger>
                <SelectValue placeholder="选择提供商..." />
              </SelectTrigger>
              <SelectContent>
                {providers
                  .filter((p) => RECOMMENDED_IDS.includes(p.id))
                  .map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name} {p.id === "deepseek" ? "（推荐）" : ""}
                    </SelectItem>
                  ))}
                {providers
                  .filter((p) => !RECOMMENDED_IDS.includes(p.id))
                  .map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>

          {/* API Key */}
          {selectedProvider && (
            <div>
              <label className="text-sm font-medium mb-1.5 block">API Key</label>
              <Input
                type="password"
                placeholder="粘贴你的 API Key..."
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value)
                  setValidResult(null)
                }}
              />
              {currentProvider && (
                <p className="text-[11px] text-muted-foreground mt-1">
                  模型：{currentProvider.default_model}
                </p>
              )}
            </div>
          )}

          {/* Validation result */}
          {validResult && (
            <div
              className={`rounded-md px-3 py-2 text-sm ${
                validResult.valid
                  ? "bg-green-500/10 text-green-700 dark:text-green-400"
                  : "bg-red-500/10 text-red-700 dark:text-red-400"
              }`}
            >
              {validResult.valid ? (
                <span className="flex items-center gap-1.5">
                  <Check className="h-4 w-4" /> 配置成功！正在准备分析...
                </span>
              ) : (
                validResult.error
              )}
            </div>
          )}

          {/* Submit */}
          <Button
            onClick={handleValidateAndSave}
            disabled={!selectedProvider || !apiKey.trim() || validating}
            className="w-full"
          >
            {validating ? (
              <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> 验证中...</>
            ) : (
              "验证并开始"
            )}
          </Button>
        </div>
      </div>
    )
  }

  // Local Ollama screen
  return (
    <div className="rounded-xl border bg-card p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-base font-semibold">💻 本地 Ollama</h3>
        <button
          onClick={() => setMode(null)}
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← 返回
        </button>
      </div>

      <div className="space-y-4">
        {/* Status */}
        <div className="flex items-center gap-2">
          <span
            className={`inline-block h-2 w-2 rounded-full ${
              ollamaStatus === "running"
                ? "bg-green-500"
                : ollamaStatus === "installed_not_running"
                  ? "bg-yellow-500"
                  : "bg-red-500"
            }`}
          />
          <span className="text-sm">
            {ollamaStatus === "running"
              ? "Ollama 运行中"
              : ollamaStatus === "installed_not_running"
                ? "Ollama 已安装但未运行"
                : "Ollama 未安装"}
          </span>
        </div>

        {/* Installation guide */}
        {ollamaStatus === "not_installed" && (
          <div className="rounded-md bg-muted/50 p-4 space-y-2">
            <p className="text-sm font-medium">安装步骤：</p>
            <ol className="text-sm text-muted-foreground space-y-1 list-decimal list-inside">
              <li>
                访问{" "}
                <a
                  href="https://ollama.com"
                  target="_blank"
                  rel="noopener"
                  className="text-primary hover:underline inline-flex items-center gap-0.5"
                >
                  ollama.com <ExternalLink className="h-3 w-3" />
                </a>{" "}
                下载安装
              </li>
              <li>安装完成后启动 Ollama</li>
              <li>
                运行命令：<code className="bg-muted rounded px-1 text-xs">ollama pull qwen3:8b</code>
              </li>
              <li>回到此页面，点击下方按钮检测</li>
            </ol>
          </div>
        )}

        {ollamaStatus === "installed_not_running" && (
          <div className="rounded-md bg-yellow-500/10 p-3 text-sm text-yellow-700 dark:text-yellow-400">
            请启动 Ollama 应用，然后点击下方按钮重新检测
          </div>
        )}

        {ollamaStatus === "running" && env?.model_available && (
          <div className="rounded-md bg-green-500/10 p-3 text-sm text-green-700 dark:text-green-400 flex items-center gap-1.5">
            <Check className="h-4 w-4" /> Ollama 就绪，模型：{env.required_model}
          </div>
        )}

        {ollamaStatus === "running" && !env?.model_available && (
          <div className="rounded-md bg-yellow-500/10 p-3 text-sm text-yellow-700 dark:text-yellow-400">
            Ollama 运行中，但推荐模型 {env?.required_model} 未安装。
            请运行：<code className="bg-muted rounded px-1 text-xs">ollama pull {env?.required_model}</code>
          </div>
        )}

        <div className="flex gap-2">
          <Button
            variant="outline"
            disabled={checking}
            onClick={async () => {
              setChecking(true)
              try {
                const fresh = await checkEnvironment()
                setEnv(fresh)
              } finally {
                setChecking(false)
              }
            }}
          >
            {checking ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />检测中...</> : "重新检测"}
          </Button>
          {ollamaStatus === "running" && env?.model_available && (
            <Button
              onClick={async () => {
                await switchLlmMode("ollama", env?.required_model ?? undefined)
                useLlmInfoStore.getState().fetch(true)
                onReady()
              }}
            >
              开始使用
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
