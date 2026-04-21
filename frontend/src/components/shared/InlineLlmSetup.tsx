/**
 * InlineLlmSetup - inline AI engine configuration.
 * Used on the analysis page when no LLM is configured.
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
import type { CloudProvider, EnvironmentCheck, OllamaModel } from "@/api/types"
import { useI18n } from "@/i18n"
import { useLlmInfoStore } from "@/stores/llmInfoStore"

interface InlineLlmSetupProps {
  onReady: () => void
}

// Top recommended providers
const RECOMMENDED_IDS = ["deepseek", "minimax", "qwen"]

export function InlineLlmSetup({ onReady }: InlineLlmSetupProps) {
  const { t } = useI18n()
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
        setValidResult({ valid: false, error: result.error || t("shared.llmSetup.validationFailed") })
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
        error: err instanceof Error ? err.message : t("shared.llmSetup.configFailed"),
      })
    } finally {
      setValidating(false)
    }
  }, [currentProvider, apiKey, onReady, t])

  const ollamaStatus = env?.ollama_status ?? "not_installed"
  const formatModelList = (models: string[]) => {
    const visible = models.slice(0, 3).join(t("common.listSeparator"))
    return models.length > 3
      ? `${visible}${t("shared.llmSetup.moreModelsSuffix", { count: models.length })}`
      : visible
  }

  // Mode selection screen
  if (mode === null) {
    return (
      <div className="rounded-xl border bg-card p-6">
        <h3 className="text-base font-semibold mb-1">{t("shared.llmSetup.title")}</h3>
        <p className="text-sm text-muted-foreground mb-5">
          {t("shared.llmSetup.description")}
        </p>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {/* Cloud card */}
          <button
            onClick={() => setMode("cloud")}
            className="group relative rounded-lg border-2 border-transparent bg-muted/50 p-5 text-left transition hover:border-blue-500/50 hover:bg-blue-500/5"
          >
            <div className="absolute top-3 right-3 rounded-full bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-600 dark:text-blue-400">
              {t("shared.llmSetup.recommended")}
            </div>
            <Cloud className="h-8 w-8 text-blue-500 mb-3" />
            <h4 className="font-semibold">{t("shared.llmSetup.cloudApi")}</h4>
            <p className="text-xs text-muted-foreground mt-1">
              {t("shared.llmSetup.cloudDescription")}
            </p>
          </button>

          {/* Local card */}
          <button
            onClick={() => setMode("local")}
            className="group rounded-lg border-2 border-transparent bg-muted/50 p-5 text-left transition hover:border-green-500/50 hover:bg-green-500/5"
          >
            <HardDrive className="h-8 w-8 text-green-500 mb-3" />
            <h4 className="font-semibold">{t("shared.llmSetup.localOllama")}</h4>
            <p className="text-xs text-muted-foreground mt-1">
              {t("shared.llmSetup.localDescription")}
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
          <h3 className="text-base font-semibold">{t("shared.llmSetup.cloudConfigTitle")}</h3>
          <button
            onClick={() => setMode(null)}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            {t("common.back")}
          </button>
        </div>

        <div className="space-y-4">
          {/* Provider select */}
          <div>
            <label className="text-sm font-medium mb-1.5 block">{t("shared.llmSetup.provider")}</label>
            <Select value={selectedProvider} onValueChange={setSelectedProvider}>
              <SelectTrigger>
                <SelectValue placeholder={t("shared.llmSetup.providerPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {providers
                  .filter((p) => RECOMMENDED_IDS.includes(p.id))
                  .map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name} {p.id === "deepseek" ? t("shared.llmSetup.recommendedSuffix") : ""}
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
              <label className="text-sm font-medium mb-1.5 block">{t("shared.llmSetup.apiKey")}</label>
              <Input
                type="password"
                placeholder={t("shared.llmSetup.apiKeyPlaceholder")}
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value)
                  setValidResult(null)
                }}
              />
              {currentProvider && (
                <p className="text-[11px] text-muted-foreground mt-1">
                  {t("shared.llmSetup.modelLabel", { model: currentProvider.default_model })}
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
                  <Check className="h-4 w-4" /> {t("shared.llmSetup.configSuccessPreparing")}
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
              <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> {t("shared.llmSetup.validating")}</>
            ) : (
              t("shared.llmSetup.validateAndStart")
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
        <h3 className="text-base font-semibold">{t("shared.llmSetup.localOllama")}</h3>
        <button
          onClick={() => setMode(null)}
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          {t("common.back")}
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
              ? t("shared.llmSetup.ollamaRunning")
              : ollamaStatus === "installed_not_running"
                ? t("shared.llmSetup.ollamaInstalledNotRunning")
                : t("shared.llmSetup.ollamaNotInstalled")}
          </span>
        </div>

        {/* Installation guide */}
        {ollamaStatus === "not_installed" && (
          <div className="rounded-md bg-muted/50 p-4 space-y-2">
            <p className="text-sm font-medium">{t("shared.llmSetup.installSteps")}</p>
            <ol className="text-sm text-muted-foreground space-y-1 list-decimal list-inside">
              <li>
                {t("shared.llmSetup.installVisit")}{" "}
                <a
                  href="https://ollama.com"
                  target="_blank"
                  rel="noopener"
                  className="text-primary hover:underline inline-flex items-center gap-0.5"
                >
                  ollama.com <ExternalLink className="h-3 w-3" />
                </a>{" "}
                {t("shared.llmSetup.installDownload")}
              </li>
              <li>{t("shared.llmSetup.installStartOllama")}</li>
              <li>
                {t("shared.llmSetup.runCommand")}<code className="bg-muted rounded px-1 text-xs">ollama pull qwen3:8b</code>
              </li>
              <li>{t("shared.llmSetup.returnAndDetect")}</li>
            </ol>
          </div>
        )}

        {ollamaStatus === "installed_not_running" && (
          <div className="rounded-md bg-yellow-500/10 p-3 text-sm text-yellow-700 dark:text-yellow-400">
            {t("shared.llmSetup.startOllamaAndRetry")}
          </div>
        )}

        {(() => {
          if (ollamaStatus !== "running") return null
          const recommended = env?.recommended_model ?? env?.required_model ?? "qwen3:8b"
          const recommendedInstalled = env?.recommended_model_installed ?? env?.model_available ?? false
          const availableList = (env?.available_models ?? [])
            .map((m) => (typeof m === "string" ? m : (m as OllamaModel).name))
            .filter((n): n is string => !!n)
          // State A: recommended model is installed → green, use it
          if (recommendedInstalled) {
            return (
              <div className="rounded-md bg-green-500/10 p-3 text-sm text-green-700 dark:text-green-400 flex items-center gap-1.5">
                <Check className="h-4 w-4" /> {t("shared.llmSetup.ollamaReadyModel", { model: recommended })}
              </div>
            )
          }
          // State B: some other model is installed but not the recommended one
          if (env?.model_available && availableList.length > 0) {
            return (
              <div className="rounded-md bg-green-500/10 p-3 text-sm text-green-700 dark:text-green-400 space-y-1">
                <div className="flex items-center gap-1.5">
                  <Check className="h-4 w-4" /> {t("shared.llmSetup.localModelsDetected", { count: availableList.length })}
                </div>
                <div className="text-xs text-muted-foreground">
                  {t("shared.llmSetup.availableModels", { models: formatModelList(availableList) })}
                </div>
                <div className="text-xs text-muted-foreground">
                  {t("shared.llmSetup.switchToRecommended")} <code className="bg-muted rounded px-1">{recommended}</code>:
                  <code className="bg-muted rounded px-1 ml-1">ollama pull {recommended}</code>
                </div>
              </div>
            )
          }
          // State C: no models installed
          return (
            <div className="rounded-md bg-yellow-500/10 p-3 text-sm text-yellow-700 dark:text-yellow-400">
              {t("shared.llmSetup.ollamaRunningNoModels")}
              {t("shared.llmSetup.pleaseRun")}<code className="bg-muted rounded px-1 text-xs">ollama pull {recommended}</code>
            </div>
          )
        })()}

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
            {checking ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />{t("shared.llmSetup.detecting")}</> : t("shared.llmSetup.redetect")}
          </Button>
          {ollamaStatus === "running" && env?.model_available && (
            <Button
              onClick={async () => {
                // v0.71.3: prefer recommended if installed, else first available
                const recommended = env?.recommended_model ?? env?.required_model ?? "qwen3:8b"
                const recommendedInstalled = env?.recommended_model_installed ?? false
                const availableList = (env?.available_models ?? [])
                  .map((m) => (typeof m === "string" ? m : (m as OllamaModel).name))
                  .filter((n): n is string => !!n)
                const chosen = recommendedInstalled ? recommended : (availableList[0] ?? recommended)
                await switchLlmMode("ollama", chosen)
                useLlmInfoStore.getState().fetch(true)
                onReady()
              }}
            >
              {t("shared.llmSetup.startUsing")}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
