import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { apiFetch, checkEnvironment, startOllama, fetchModelRecommendations, pullOllamaModel, setDefaultModel, fetchCloudProviders, fetchCloudConfig, saveCloudConfig, validateCloudApi, fetchNovels, exportNovelUrl, previewImport, confirmDataImport, fetchSettings, switchLlmMode, fetchRunningTasks, restoreDefaults, fetchBudget, setBudget, fetchAnalysisRecords, fetchCostDetail, costDetailCsvUrl, downloadBackupExport, previewBackupImport, confirmBackupImport, runModelBenchmark, fetchBenchmarkHistory, deleteBenchmarkRecord } from "@/api/client"
import type { BenchmarkResult, BenchmarkRecord, EnvironmentCheck, OllamaModel, ModelRecommendation, CloudProvider, CloudConfig, Novel, ImportPreview, AnalysisRecord, CostDetailResponse, BackupPreview, BackupImportResult } from "@/api/types"
import { useReadingSettingsStore, FONT_SIZE_MAP, LINE_HEIGHT_MAP } from "@/stores/readingSettingsStore"
import { novelPath } from "@/lib/novelPaths"
import { useLlmInfoStore } from "@/stores/llmInfoStore"
import { useThemeStore } from "@/stores/themeStore"
import { Button } from "@/components/ui/button"
import { translate, useI18n, type Locale, type TranslationKey } from "@/i18n"
import { cn } from "@/lib/utils"
import { isTauri } from "@/api/sidecarBridge"
import cnFlag from "@/assets/flags/cn.svg"
import usFlag from "@/assets/flags/us.svg"
import vnFlag from "@/assets/flags/vn.svg"

const LOCALE_LABELS: Record<Locale, string> = {
  "zh-CN": "简体中文",
  en: "English",
  vi: "Tiếng Việt",
}

const LOCALE_FLAGS: Record<Locale, string> = {
  "zh-CN": cnFlag,
  en: usFlag,
  vi: vnFlag,
}

const FONT_SIZE_LABEL_KEYS = {
  small: "settings.reading.font.small",
  medium: "settings.reading.font.medium",
  large: "settings.reading.font.large",
  xlarge: "settings.reading.font.xlarge",
} as const satisfies Record<keyof typeof FONT_SIZE_MAP, TranslationKey>

const LINE_HEIGHT_LABEL_KEYS = {
  compact: "settings.reading.lineHeight.compact",
  normal: "settings.reading.lineHeight.normal",
  loose: "settings.reading.lineHeight.loose",
} as const satisfies Record<keyof typeof LINE_HEIGHT_MAP, TranslationKey>

const THEME_LABEL_KEYS = {
  light: "settings.reading.theme.light",
  dark: "settings.reading.theme.dark",
  system: "settings.reading.theme.system",
} as const satisfies Record<"light" | "dark" | "system", TranslationKey>

const PROVIDER_TAG_KEYS = {
  deepseek: "settings.aiEngine.providerTag.recommended",
  minimax: "settings.aiEngine.providerTag.longContext",
  qwen: "settings.aiEngine.providerTag.multimodal",
  moonshot: "settings.aiEngine.providerTag.context128k",
  zhipu: "settings.aiEngine.providerTag.freeTier",
  siliconflow: "settings.aiEngine.providerTag.openModels",
  yi: "settings.aiEngine.providerTag.reasoning",
  openai: "settings.aiEngine.providerTag.globalStandard",
  anthropic: "settings.aiEngine.providerTag.bestReasoning",
  gemini: "settings.aiEngine.providerTag.multimodal",
} as const satisfies Record<string, TranslationKey>

const USAGE_EVENT_LABEL_KEYS = {
  novel_upload: "settings.privacy.event.novelUpload",
  novel_delete: "settings.privacy.event.novelDelete",
  analysis_start: "settings.privacy.event.analysisStart",
  analysis_complete: "settings.privacy.event.analysisComplete",
  export_series_bible: "settings.privacy.event.exportSeriesBible",
  export_data: "settings.privacy.event.exportData",
  view_entity_card: "settings.privacy.event.viewEntityCard",
  view_graph: "settings.privacy.event.viewGraph",
  view_map: "settings.privacy.event.viewMap",
  view_timeline: "settings.privacy.event.viewTimeline",
  view_factions: "settings.privacy.event.viewFactions",
  view_encyclopedia: "settings.privacy.event.viewEncyclopedia",
  view_conflicts: "settings.privacy.event.viewConflicts",
  view_screenplay: "settings.privacy.event.viewScreenplay",
  chat_question: "settings.privacy.event.chatQuestion",
  prescan_start: "settings.privacy.event.prescanStart",
} as const satisfies Record<string, TranslationKey>

function openExternal(url: string) {
  if (isTauri) {
    import("@tauri-apps/plugin-shell").then(({ open }) => open(url)).catch(() => window.open(url, "_blank"))
  } else {
    window.open(url, "_blank")
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`
  return String(n)
}

function formatDateTime(iso: string | null): string {
  if (!iso) return "-"
  const d = new Date(iso)
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`
}

export default function SettingsPage() {
  const navigate = useNavigate()
  const { locale, setLocale, supportedLocales, t } = useI18n()
  const [envCheck, setEnvCheck] = useState<EnvironmentCheck | null>(null)
  const [envLoading, setEnvLoading] = useState(true)
  const [novels, setNovels] = useState<Novel[]>([])

  const { fontSize, lineHeight, setFontSize, setLineHeight } = useReadingSettingsStore()
  const { theme, setTheme } = useThemeStore()

  const providerModeLabel = (provider: string | null | undefined) => (
    provider === "openai"
      ? t("settings.aiEngine.provider.cloud")
      : t("settings.aiEngine.provider.local")
  )

  const ollamaStatusLabel = (status: EnvironmentCheck["ollama_status"]) => {
    switch (status) {
      case "running":
        return t("settings.aiEngine.status.running")
      case "installed_not_running":
        return t("settings.aiEngine.status.installedNotRunning")
      default:
        return t("settings.aiEngine.status.notInstalled")
    }
  }

  const providerTagLabel = (providerId: string | null | undefined) => {
    if (!providerId) return ""
    const key = PROVIDER_TAG_KEYS[providerId as keyof typeof PROVIDER_TAG_KEYS]
    return key ? t(key) : ""
  }

  const usageEventLabel = (eventType: string) => {
    const key = USAGE_EVENT_LABEL_KEYS[eventType as keyof typeof USAGE_EVENT_LABEL_KEYS]
    return key ? t(key) : eventType
  }

  // Import state
  const importFileRef = useRef<HTMLInputElement>(null)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importPreview, setImportPreview] = useState<ImportPreview | null>(null)
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<string | null>(null)
  const [importError, setImportError] = useState<string | null>(null)

  useEffect(() => {
    setEnvLoading(true)
    checkEnvironment()
      .then(setEnvCheck)
      .finally(() => setEnvLoading(false))

    fetchSettings().then((data) => {
      const mode = data.settings.llm_provider === "openai" ? "openai" : "ollama"
      setViewTab(mode)
      setSelectedOllamaModel(data.settings.ollama_model)
    }).catch(() => {})

    fetchBudget().then((data) => {
      setBudgetAmount(data.monthly_budget_cny)
      setMonthlyUsed(data.monthly_used_cny)
    }).catch(() => {})

    setRecordsLoading(true)
    fetchAnalysisRecords()
      .then((data) => setAnalysisRecords(data.records))
      .catch(() => {})
      .finally(() => setRecordsLoading(false))

    fetchNovels().then((data) => setNovels(data.novels))
  }, [])

  const [ollamaStarting, setOllamaStarting] = useState(false)

  // Model recommendations
  const [recommendations, setRecommendations] = useState<ModelRecommendation[]>([])
  const [recRamGb, setRecRamGb] = useState(0)
  const [recLoading, setRecLoading] = useState(false)
  const [pullingModel, setPullingModel] = useState<string | null>(null)
  const [pullProgress, setPullProgress] = useState<{ completed: number; total: number } | null>(null)
  const [pullError, setPullError] = useState<string | null>(null)
  const cancelPullRef = useRef<(() => void) | null>(null)

  const loadRecommendations = useCallback(() => {
    setRecLoading(true)
    fetchModelRecommendations()
      .then((data) => {
        setRecommendations(data.recommendations)
        setRecRamGb(data.total_ram_gb)
      })
      .catch(() => {})
      .finally(() => setRecLoading(false))
  }, [])

  useEffect(() => {
    if (envCheck?.ollama_status === "running") {
      loadRecommendations()
    }
  }, [envCheck?.ollama_status, loadRecommendations])

  // Cloud config
  const [cloudProviders, setCloudProviders] = useState<CloudProvider[]>([])
  const [cloudConfig, setCloudConfig] = useState<CloudConfig | null>(null)
  const [cloudProvider, setCloudProvider] = useState("")
  const [cloudBaseUrl, setCloudBaseUrl] = useState("")
  const [cloudModel, setCloudModel] = useState("")
  const [cloudApiKey, setCloudApiKey] = useState("")
  const [cloudSaving, setCloudSaving] = useState(false)
  const [cloudValidating, setCloudValidating] = useState(false)
  const [cloudValidResult, setCloudValidResult] = useState<{ valid: boolean; error?: string } | null>(null)
  const [cloudSaveMsg, setCloudSaveMsg] = useState<string | null>(null)

  // Mode tab & advanced settings
  // viewTab: which tab panel is visible (pure UI navigation)
  // activeEngine: which engine the backend actually uses (from envCheck)
  const [viewTab, setViewTab] = useState<"ollama" | "openai">("ollama")
  const [modeSwitching, setModeSwitching] = useState(false)
  const [restoring, setRestoring] = useState(false)
  const [selectedOllamaModel, setSelectedOllamaModel] = useState("")
  // Switch confirmation dialog
  const [showSwitchDialog, setShowSwitchDialog] = useState(false)
  const [runningTaskCount, setRunningTaskCount] = useState(0)

  // Budget state
  const [budgetAmount, setBudgetAmount] = useState(50)
  const [monthlyUsed, setMonthlyUsed] = useState(0)
  const [budgetSaving, setBudgetSaving] = useState(false)

  // Analysis records state
  const [analysisRecords, setAnalysisRecords] = useState<AnalysisRecord[]>([])
  const [recordsLoading, setRecordsLoading] = useState(false)
  const [expandedRecord, setExpandedRecord] = useState<string | null>(null)
  const [costDetail, setCostDetail] = useState<CostDetailResponse | null>(null)
  const [costDetailLoading, setCostDetailLoading] = useState(false)

  // Backup state
  const backupFileRef = useRef<HTMLInputElement>(null)
  const [backupPreview, setBackupPreview] = useState<BackupPreview | null>(null)
  const [backupFile, setBackupFile] = useState<File | null>(null)
  const [backupImporting, setBackupImporting] = useState(false)
  const [backupResult, setBackupResult] = useState<BackupImportResult | null>(null)
  const [backupError, setBackupError] = useState<string | null>(null)

  // Benchmark state
  const [benchmarking, setBenchmarking] = useState(false)
  const [benchmarkResult, setBenchmarkResult] = useState<BenchmarkResult | null>(null)
  const [benchmarkError, setBenchmarkError] = useState<string | null>(null)
  const [benchmarkHistory, setBenchmarkHistory] = useState<BenchmarkRecord[]>([])
  const [showBenchmarkHistory, setShowBenchmarkHistory] = useState(true)

  const loadBenchmarkHistory = useCallback(() => {
    fetchBenchmarkHistory()
      .then(setBenchmarkHistory)
      .catch(() => {})
  }, [])

  useEffect(() => { loadBenchmarkHistory() }, [loadBenchmarkHistory])

  const handleBenchmark = useCallback(async () => {
    setBenchmarking(true)
    setBenchmarkResult(null)
    setBenchmarkError(null)
    try {
      const result = await runModelBenchmark()
      setBenchmarkResult(result)
      loadBenchmarkHistory()
    } catch (err) {
      setBenchmarkError(err instanceof Error ? err.message : translate("settings.aiEngine.benchmark.runFailed"))
    } finally {
      setBenchmarking(false)
    }
  }, [loadBenchmarkHistory])

  const handleDeleteBenchmarkRecord = useCallback(async (id: number) => {
    setBenchmarkHistory((prev) => prev.filter((r) => r.id !== id))
    await deleteBenchmarkRecord(id).catch(() => {})
  }, [])

  // Usage analytics state
  const [usageStats, setUsageStats] = useState<{
    total_events: number
    by_type: { event_type: string; count: number }[]
    daily_trend: { day: string; count: number }[]
  } | null>(null)
  const [usageLoading, setUsageLoading] = useState(false)
  const [trackingEnabled, setTrackingEnabled] = useState(true)

  useEffect(() => {
    // Load usage stats + tracking preference
    setUsageLoading(true)
    Promise.all([
      apiFetch<{ total_events: number; by_type: { event_type: string; count: number }[]; daily_trend: { day: string; count: number }[] }>("/usage/stats?days=30"),
      apiFetch<{ enabled: boolean }>("/usage/tracking-enabled"),
    ]).then(([stats, tracking]) => {
      setUsageStats(stats)
      setTrackingEnabled(tracking.enabled)
    }).catch(() => {}).finally(() => setUsageLoading(false))
  }, [])

  useEffect(() => {
    fetchCloudProviders().then((d) => setCloudProviders(d.providers)).catch(() => {})
    fetchCloudConfig()
      .then((cfg) => {
        setCloudConfig(cfg)
        setCloudProvider(cfg.provider)
        setCloudBaseUrl(cfg.base_url)
        setCloudModel(cfg.model)
      })
      .catch(() => {})
  }, [])

  // 模型预设选择器状态：false = 使用 select 预设，true = 自由输入
  const [isCustomModel, setIsCustomModel] = useState(false)

  const handleProviderChange = useCallback(
    (providerId: string) => {
      setCloudProvider(providerId)
      setCloudValidResult(null)
      setIsCustomModel(false)
      const preset = cloudProviders.find((p) => p.id === providerId)
      if (preset) {
        setCloudBaseUrl(preset.base_url)
        setCloudModel(preset.default_model)
      }
    },
    [cloudProviders],
  )

  const handleValidateCloud = useCallback(async () => {
    setCloudValidating(true)
    setCloudValidResult(null)
    try {
      const res = await validateCloudApi(cloudBaseUrl, cloudApiKey, cloudProvider)
      setCloudValidResult(res)
    } catch {
      setCloudValidResult({ valid: false, error: translate("settings.aiEngine.cloud.validationRequestFailed") })
    } finally {
      setCloudValidating(false)
    }
  }, [cloudBaseUrl, cloudApiKey])

  const handleSaveCloud = useCallback(async () => {
    setCloudSaving(true)
    setCloudSaveMsg(null)
    try {
      const res = await saveCloudConfig({
        provider: cloudProvider,
        base_url: cloudBaseUrl,
        model: cloudModel,
        api_key: cloudApiKey,
      })
      if (res.success) {
        setCloudSaveMsg(translate("settings.aiEngine.cloud.savedWithStorage", { storage: res.storage }))
        setCloudApiKey("")
        refreshEnv()
        fetchCloudConfig().then(setCloudConfig).catch(() => {})
      }
    } catch {
      setCloudSaveMsg(translate("settings.aiEngine.cloud.saveFailed"))
    } finally {
      setCloudSaving(false)
    }
  }, [cloudProvider, cloudBaseUrl, cloudModel, cloudApiKey])

  // Initiate switch: check running tasks, then show confirmation dialog
  const handleRequestSwitch = useCallback(async () => {
    try {
      const { running_count } = await fetchRunningTasks()
      setRunningTaskCount(running_count)
    } catch {
      setRunningTaskCount(0)
    }
    setShowSwitchDialog(true)
  }, [])

  // Confirmed switch — actually call the backend
  const handleConfirmSwitch = useCallback(async () => {
    setShowSwitchDialog(false)
    const targetMode = viewTab  // switch to whatever tab the user is viewing
    setModeSwitching(true)
    try {
      await switchLlmMode(targetMode, targetMode === "ollama" ? selectedOllamaModel || "qwen3:8b" : undefined)
      refreshEnv()
    } catch { /* ignore */ }
    finally { setModeSwitching(false) }
  }, [viewTab, selectedOllamaModel])

  const handleOllamaModelChange = useCallback(async (model: string) => {
    setSelectedOllamaModel(model)
    await setDefaultModel(model).catch(() => {})
    refreshEnv()
  }, [])

  const handleRestoreDefaults = useCallback(async () => {
    setRestoring(true)
    try {
      const res = await restoreDefaults()
      if (res.success) {
        setViewTab("ollama")
        setSelectedOllamaModel("qwen3:8b")
        refreshEnv()
      }
    } catch { /* ignore */ }
    finally { setRestoring(false) }
  }, [])

  const handleSaveBudget = useCallback(async () => {
    setBudgetSaving(true)
    try {
      await setBudget(budgetAmount)
    } catch { /* ignore */ }
    finally { setBudgetSaving(false) }
  }, [budgetAmount])

  const handleExpandRecord = useCallback(async (novelId: string) => {
    if (expandedRecord === novelId) {
      setExpandedRecord(null)
      setCostDetail(null)
      return
    }
    setExpandedRecord(novelId)
    setCostDetailLoading(true)
    try {
      const detail = await fetchCostDetail(novelId)
      setCostDetail(detail)
    } catch {
      setCostDetail(null)
    } finally {
      setCostDetailLoading(false)
    }
  }, [expandedRecord])

  const handlePullModel = useCallback((modelName: string) => {
    setPullingModel(modelName)
    setPullProgress(null)
    setPullError(null)
    const cancel = pullOllamaModel(
      modelName,
      (data) => {
        if (data.completed != null && data.total != null && data.total > 0) {
          setPullProgress({ completed: data.completed, total: data.total })
        }
      },
      () => {
        setPullingModel(null)
        setPullProgress(null)
        // Set as default and refresh
        setDefaultModel(modelName).catch(() => {})
        refreshEnv()
        loadRecommendations()
      },
      (error) => {
        setPullingModel(null)
        setPullProgress(null)
        setPullError(error)
      },
    )
    cancelPullRef.current = cancel
  }, [loadRecommendations])

  const refreshEnv = () => {
    setEnvLoading(true)
    checkEnvironment()
      .then((env) => {
        setEnvCheck(env)
        // Sync to global store so AnalysisPage shows updated model
        useLlmInfoStore.getState().fetch(true)
      })
      .finally(() => setEnvLoading(false))
  }

  const handleStartOllama = async () => {
    setOllamaStarting(true)
    try {
      const res = await startOllama()
      if (res.success) {
        refreshEnv()
      } else {
        setEnvCheck((prev) =>
          prev ? { ...prev, error: res.error ?? translate("settings.aiEngine.startFailed") } : prev,
        )
      }
    } catch {
      setEnvCheck((prev) =>
        prev ? { ...prev, error: translate("settings.aiEngine.startRequestFailed") } : prev,
      )
    } finally {
      setOllamaStarting(false)
    }
  }

  const handleExport = useCallback((novelId: string) => {
    window.open(exportNovelUrl(novelId), "_blank")
  }, [])

  const handleImportFileChange = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setImportFile(file)
    setImportResult(null)
    setImportError(null)
    try {
      const preview = await previewImport(file)
      setImportPreview(preview)
    } catch (err) {
      setImportError(err instanceof Error ? err.message : translate("shared.upload.error.previewFailed"))
      setImportPreview(null)
    }
  }, [])

  const handleConfirmImport = useCallback(async (overwrite: boolean) => {
    if (!importFile) return
    setImporting(true)
    setImportError(null)
    try {
      await confirmDataImport(importFile, overwrite)
      setImportResult(translate("settings.data.import.success"))
      setImportFile(null)
      setImportPreview(null)
      // Refresh novel list
      fetchNovels().then((data) => setNovels(data.novels))
    } catch (err) {
      setImportError(err instanceof Error ? err.message : translate("shared.upload.error.importFailed"))
    } finally {
      setImporting(false)
    }
  }, [importFile])

  const cancelImport = useCallback(() => {
    setImportFile(null)
    setImportPreview(null)
    setImportResult(null)
    setImportError(null)
    if (importFileRef.current) importFileRef.current.value = ""
  }, [])

  return (
    <div className="flex h-screen flex-col">
      {/* Header */}
      <header className="flex items-center gap-4 border-b px-4 py-2">
        <button
          className="text-muted-foreground text-sm hover:underline"
          onClick={() => navigate("/")}
        >
          &larr; {t("nav.bookshelf")}
        </button>
        <span className="text-sm font-medium">{t("settings.open")}</span>
      </header>

      <div className="flex-1 overflow-auto" id="settings-scroll">
        {/* Quick navigation */}
        <nav className="sticky top-0 z-10 border-b bg-background/95 backdrop-blur">
          <div className="max-w-2xl mx-auto flex gap-1 px-6 py-1.5 overflow-x-auto">
            {[
              { id: "sec-engine", label: t("settings.nav.aiEngine") },
              { id: "sec-language", label: t("settings.interfaceLanguage") },
              { id: "sec-usage", label: t("settings.nav.usage") },
              { id: "sec-reading", label: t("settings.nav.reading") },
              { id: "sec-data", label: t("settings.nav.data") },
              { id: "sec-backup", label: t("settings.nav.backup") },
              { id: "sec-privacy", label: t("settings.nav.privacy") },
            ].map((s) => (
              <button
                key={s.id}
                className="shrink-0 rounded px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                onClick={() => document.getElementById(s.id)?.scrollIntoView({ behavior: "smooth", block: "start" })}
              >
                {s.label}
              </button>
            ))}
          </div>
        </nav>

        <div className="max-w-2xl mx-auto p-6 space-y-8">
          {/* AI Engine Configuration — Unified Tabbed Interface */}
          <section id="sec-engine" className="scroll-mt-12">
            <h2 className="text-base font-medium mb-4">{t("settings.nav.aiEngine")}</h2>

            {/* Active engine status banner */}
            {envCheck && !envLoading && (
              <div className={cn(
                "mb-3 flex items-center gap-3 rounded-lg border px-4 py-2.5",
                envCheck.llm_provider === "openai"
                  ? envCheck.api_available
                    ? "border-green-200 bg-green-50/60 dark:border-green-900 dark:bg-green-950/20"
                    : "border-yellow-200 bg-yellow-50/60 dark:border-yellow-900 dark:bg-yellow-950/20"
                  : envCheck.ollama_status === "running" && envCheck.model_available
                    ? "border-green-200 bg-green-50/60 dark:border-green-900 dark:bg-green-950/20"
                    : "border-yellow-200 bg-yellow-50/60 dark:border-yellow-900 dark:bg-yellow-950/20",
              )}>
                <span className={cn(
                  "inline-block h-2.5 w-2.5 shrink-0 rounded-full",
                  envCheck.llm_provider === "openai"
                    ? envCheck.api_available ? "bg-green-500" : "bg-yellow-500"
                    : envCheck.ollama_status === "running" && envCheck.model_available
                      ? "bg-green-500" : "bg-yellow-500",
                )} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">
                      {providerModeLabel(envCheck.llm_provider)}
                    </span>
                    <span className="rounded bg-background/80 px-1.5 py-0.5 text-xs font-mono text-muted-foreground">
                      {envCheck.llm_model || t("settings.aiEngine.unconfigured")}
                    </span>
                  </div>
                  <p className="text-[10px] text-muted-foreground mt-0.5">
                    {envCheck.llm_provider === "openai"
                      ? envCheck.api_available
                        ? t("settings.aiEngine.connectedWithBaseUrl", { baseUrl: envCheck.llm_base_url || "" })
                        : t("settings.aiEngine.notConnected")
                      : envCheck.ollama_status === "running"
                        ? envCheck.model_available
                          ? t("settings.aiEngine.status.running")
                          : t("settings.aiEngine.runningModelMissing", { model: envCheck.llm_model })
                        : envCheck.ollama_status === "installed_not_running"
                          ? t("settings.aiEngine.status.installedNotRunning")
                          : t("settings.aiEngine.notInstalledOllama")}
                  </p>
                </div>
              </div>
            )}

            <div className="border rounded-lg overflow-hidden">
              {/* Mode tabs — pure navigation, no backend switching */}
              <div className="flex border-b">
                <button
                  className={cn(
                    "flex-1 py-2.5 text-sm font-medium text-center transition-colors relative",
                    viewTab === "ollama"
                      ? "bg-background text-foreground border-b-2 border-blue-500"
                      : "bg-muted/30 text-muted-foreground hover:text-foreground",
                  )}
                  onClick={() => setViewTab("ollama")}
                >
                  {t("settings.aiEngine.provider.local")}
                  {envCheck?.llm_provider !== "openai" && (
                    <span className="ml-1.5 inline-block rounded-full bg-green-100 px-1.5 py-0.5 text-[10px] text-green-600 dark:bg-green-900/40 dark:text-green-300">
                      {t("settings.aiEngine.active")}
                    </span>
                  )}
                </button>
                <button
                  className={cn(
                    "flex-1 py-2.5 text-sm font-medium text-center transition-colors relative",
                    viewTab === "openai"
                      ? "bg-background text-foreground border-b-2 border-blue-500"
                      : "bg-muted/30 text-muted-foreground hover:text-foreground",
                  )}
                  onClick={() => setViewTab("openai")}
                >
                  {t("settings.aiEngine.provider.cloud")}
                  {envCheck?.llm_provider === "openai" && (
                    <span className="ml-1.5 inline-block rounded-full bg-green-100 px-1.5 py-0.5 text-[10px] text-green-600 dark:bg-green-900/40 dark:text-green-300">
                      {t("settings.aiEngine.active")}
                    </span>
                  )}
                </button>
              </div>

              <div className="p-4 space-y-3">
                {envLoading ? (
                  <p className="text-sm text-muted-foreground">{t("settings.aiEngine.detecting")}</p>
                ) : viewTab === "ollama" ? (
                  /* ── Local Ollama Tab ── */
                  <>
                    <div className="flex items-center justify-between">
                      <span className="text-sm">{t("settings.aiEngine.ollamaStatus")}</span>
                      <div className="flex items-center gap-2">
                        <span
                          className={cn(
                            "text-xs px-2 py-0.5 rounded-full",
                            envCheck?.ollama_status === "running"
                              ? "bg-green-50 text-green-600 dark:bg-green-950/30"
                              : envCheck?.ollama_status === "installed_not_running"
                                ? "bg-yellow-50 text-yellow-600 dark:bg-yellow-950/30"
                            : "bg-red-50 text-red-600 dark:bg-red-950/30",
                          )}
                        >
                          {ollamaStatusLabel(envCheck?.ollama_status)}
                        </span>
                        {envCheck?.ollama_status === "installed_not_running" && (
                          <Button
                            variant="outline"
                            size="xs"
                            onClick={handleStartOllama}
                            disabled={ollamaStarting}
                          >
                            {ollamaStarting ? t("settings.aiEngine.starting") : t("settings.aiEngine.startOllama")}
                          </Button>
                        )}
                        {envCheck?.ollama_status === "not_installed" && (
                          <Button
                            variant="outline"
                            size="xs"
                            onClick={() => openExternal("https://ollama.com/download")}
                          >
                            {t("settings.aiEngine.downloadInstall")}
                          </Button>
                        )}
                      </div>
                    </div>

                    {/* Model selection dropdown */}
                    {envCheck?.ollama_status === "running" && (envCheck.available_models?.length ?? 0) > 0 && (
                      <div className="flex items-center justify-between">
                        <span className="text-sm">{t("settings.aiEngine.currentModel")}</span>
                        <select
                          className="border rounded px-2 py-1 text-sm bg-background font-mono"
                          value={selectedOllamaModel}
                          onChange={(e) => handleOllamaModelChange(e.target.value)}
                        >
                          {envCheck!.available_models!.map((m) => {
                            const name = typeof m === "object" && m !== null ? (m as OllamaModel).name : (m as string)
                            return (
                              <option key={name} value={name}>
                                {name}
                              </option>
                            )
                          })}
                        </select>
                      </div>
                    )}

                    <div className="flex items-center justify-between">
                      <span className="text-sm">{t("settings.aiEngine.apiUrl")}</span>
                      <span className="text-xs text-muted-foreground font-mono">
                        {envCheck?.ollama_url}
                      </span>
                    </div>

                    {envCheck?.error && (
                      <p className="text-xs text-red-500">{envCheck.error}</p>
                    )}

                    {/* Inline model recommendations */}
                    {envCheck?.ollama_status === "running" && (
                      <div className="border-t pt-3 mt-3">
                        <span className="text-sm block mb-2">{t("settings.aiEngine.modelRecommendations")}</span>
                        {recRamGb > 0 && (
                          <p className="text-[10px] text-muted-foreground mb-2">
                            {t("settings.aiEngine.systemMemory", { ram: recRamGb })}
                          </p>
                        )}
                        {recLoading ? (
                          <p className="text-xs text-muted-foreground">{t("common.loading")}</p>
                        ) : recommendations.length === 0 ? (
                          <p className="text-xs text-muted-foreground">{t("settings.aiEngine.noRecommendations")}</p>
                        ) : (
                          <div className="space-y-2">
                            {recommendations.map((rec) => (
                              <div
                                key={rec.name}
                                className={cn(
                                  "flex items-center justify-between p-2.5 rounded-md border",
                                  rec.recommended && "border-blue-200 bg-blue-50/50 dark:border-blue-900 dark:bg-blue-950/20",
                                )}
                              >
                                <div className="flex-1 min-w-0">
                                  <div className="flex items-center gap-2">
                                    <span className="text-sm font-medium">{rec.display_name}</span>
                                    {rec.recommended && (
                                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-100 text-blue-600 dark:bg-blue-900 dark:text-blue-300">
                                        {t("shared.llmSetup.recommended")}
                                      </span>
                                    )}
                                    {rec.installed && (
                                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-100 text-green-600 dark:bg-green-900 dark:text-green-300">
                                        {t("settings.aiEngine.installed")}
                                      </span>
                                    )}
                                  </div>
                                  <p className="text-[10px] text-muted-foreground mt-0.5">
                                    {t("settings.aiEngine.recommendationDescription", {
                                      description: rec.description,
                                      size: rec.size_gb,
                                      ram: rec.min_ram_gb,
                                    })}
                                  </p>
                                </div>
                                <div className="flex-shrink-0 ml-3">
                                  {rec.installed ? (
                                    <Button variant="ghost" size="xs" disabled>
                                      {t("settings.aiEngine.installed")}
                                    </Button>
                                  ) : pullingModel === rec.name ? (
                                    <div className="w-24">
                                      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                                        <div
                                          className="h-full bg-blue-500 rounded-full transition-all duration-300"
                                          style={{
                                            width: pullProgress
                                              ? `${Math.round((pullProgress.completed / pullProgress.total) * 100)}%`
                                              : "5%",
                                          }}
                                        />
                                      </div>
                                      <p className="text-[10px] text-muted-foreground mt-0.5 text-center">
                                        {pullProgress
                                          ? `${Math.round((pullProgress.completed / pullProgress.total) * 100)}%`
                                          : t("settings.aiEngine.preparing")}
                                      </p>
                                    </div>
                                  ) : (
                                    <Button
                                      variant="outline"
                                      size="xs"
                                      onClick={() => handlePullModel(rec.name)}
                                      disabled={pullingModel !== null}
                                    >
                                      {t("settings.aiEngine.download")}
                                    </Button>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                        {pullError && (
                          <p className="text-xs text-red-500 mt-1">{pullError}</p>
                        )}
                      </div>
                    )}

                    {/* Performance benchmark */}
                    {envCheck?.ollama_status === "running" && (
                      <div className="border-t pt-3 mt-3">
                        <div className="flex items-center gap-3">
                          <Button
                            variant="outline"
                            size="xs"
                            onClick={handleBenchmark}
                            disabled={benchmarking}
                          >
                            {benchmarking ? t("settings.aiEngine.benchmark.testing") : t("settings.aiEngine.benchmark.run")}
                          </Button>
                          {benchmarking && (
                            <span className="text-xs text-muted-foreground">{t("settings.aiEngine.benchmark.waiting")}</span>
                          )}
                        </div>
                        {benchmarkResult && (
                          <div className="mt-2 rounded-md border p-2.5 text-xs space-y-1">
                            <div className="flex flex-wrap gap-x-4 gap-y-0.5">
                              <span>{t("settings.aiEngine.benchmark.responseTime", { seconds: (benchmarkResult.benchmark.elapsed_ms / 1000).toFixed(1) })}</span>
                              <span>{t("settings.aiEngine.benchmark.speed", { speed: benchmarkResult.benchmark.tokens_per_second })}</span>
                              <span>
                                {t("settings.aiEngine.benchmark.estimatedChapter", { seconds: benchmarkResult.benchmark.estimated_chapter_time_s })}
                                {" "}
                                <span className="text-muted-foreground font-normal">
                                  {t("settings.aiEngine.benchmark.approxChars", { chars: benchmarkResult.benchmark.estimated_chapter_chars })}
                                </span>
                              </span>
                              <span>{t("settings.aiEngine.benchmark.contextWindow", { size: (benchmarkResult.context_window / 1024).toFixed(0) })}</span>
                            </div>
                            <div className="flex items-center gap-2">
                              <span>{t("settings.aiEngine.benchmark.analysisQuality")}</span>
                              <strong className={cn(
                                benchmarkResult.quality.overall_score >= 80 ? "text-green-600" :
                                benchmarkResult.quality.overall_score >= 60 ? "text-yellow-600" : "text-red-500"
                              )}>
                                {t("settings.aiEngine.benchmark.scorePoints", { score: benchmarkResult.quality.overall_score })}
                              </strong>
                              <span className="text-muted-foreground">
                                {t("settings.aiEngine.benchmark.qualityBreakdown", {
                                  entityRecall: benchmarkResult.quality.entity_recall,
                                  relationRecall: benchmarkResult.quality.relation_recall,
                                })}
                              </span>
                            </div>
                            {benchmarkResult.quality.notes.length > 0 && (
                              <p className="text-[10px] text-muted-foreground">
                                {benchmarkResult.quality.notes.join(t("common.listSeparator"))}
                              </p>
                            )}
                            <p className="text-[10px] text-muted-foreground">
                              {t("settings.aiEngine.benchmark.tokenUsage", {
                                input: formatTokens(benchmarkResult.benchmark.input_tokens),
                                output: formatTokens(benchmarkResult.benchmark.output_tokens),
                              })}
                            </p>
                          </div>
                        )}
                        {benchmarkError && (
                          <p className="mt-1 text-xs text-red-500">{benchmarkError}</p>
                        )}
                        {/* Benchmark history */}
                        {benchmarkHistory.length > 0 && (
                          <div className="mt-3">
                            <button
                              className="flex w-full items-center justify-between text-xs text-muted-foreground hover:text-foreground"
                              onClick={() => setShowBenchmarkHistory(!showBenchmarkHistory)}
                            >
                              <span>{t("settings.aiEngine.benchmark.history", { count: benchmarkHistory.length })}</span>
                              <span>{showBenchmarkHistory ? t("entity.showLess") : t("common.expand")}</span>
                            </button>
                            {showBenchmarkHistory && (
                              <div className="mt-1.5 border rounded-md overflow-hidden">
                                <table className="w-full text-[11px]">
                                  <thead>
                                    <tr className="bg-muted/40 text-muted-foreground">
                                      <th className="text-left px-2 py-1 font-medium">{t("settings.aiEngine.benchmark.table.time")}</th>
                                      <th className="text-left px-2 py-1 font-medium">{t("settings.aiEngine.benchmark.table.model")}</th>
                                      <th className="text-right px-2 py-1 font-medium">{t("settings.aiEngine.benchmark.table.speed")}</th>
                                      <th className="text-right px-2 py-1 font-medium">{t("settings.aiEngine.benchmark.table.estimatedChapter")}</th>
                                      <th className="text-right px-2 py-1 font-medium">{t("settings.aiEngine.benchmark.table.quality")}</th>
                                      <th className="text-right px-2 py-1 font-medium"></th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {benchmarkHistory.map((rec) => (
                                      <tr key={rec.id} className="border-t hover:bg-muted/20">
                                        <td className="px-2 py-1 text-muted-foreground">{formatDateTime(rec.created_at)}</td>
                                        <td className="px-2 py-1 font-mono">{rec.model}</td>
                                        <td className="px-2 py-1 text-right font-mono">{rec.tokens_per_second} t/s</td>
                                        <td className="px-2 py-1 text-right font-mono">{t("settings.aiEngine.benchmark.table.estimatedChapterValue", { seconds: rec.estimated_chapter_time_s })}</td>
                                        <td className={cn(
                                          "px-2 py-1 text-right font-mono",
                                          rec.quality_score != null && rec.quality_score >= 80 ? "text-green-600" :
                                          rec.quality_score != null && rec.quality_score >= 60 ? "text-yellow-600" :
                                          rec.quality_score != null ? "text-red-500" : "text-muted-foreground",
                                        )}>
                                          {rec.quality_score != null ? t("settings.aiEngine.benchmark.scorePoints", { score: rec.quality_score }) : "-"}
                                        </td>
                                        <td className="px-2 py-1 text-right">
                                          <button
                                            className="text-muted-foreground hover:text-red-500 transition-colors"
                                            onClick={() => handleDeleteBenchmarkRecord(rec.id)}
                                          >
                                            {t("common.delete")}
                                          </button>
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Switch button — only when Ollama is NOT the active engine */}
                    {envCheck?.llm_provider === "openai" && (
                      <div className="border-t pt-3 mt-3">
                        <Button
                          onClick={handleRequestSwitch}
                          disabled={modeSwitching || envCheck?.ollama_status !== "running"}
                          size="sm"
                        >
                          {modeSwitching ? t("settings.aiEngine.switching") : t("settings.aiEngine.switchToThisEngine")}
                        </Button>
                        {envCheck?.ollama_status !== "running" && (
                          <p className="text-[10px] text-muted-foreground mt-1">
                            {t("settings.aiEngine.startOllamaFirst")}
                          </p>
                        )}
                        {envCheck?.ollama_status === "running" && (
                          <p className="text-[10px] text-muted-foreground mt-1">
                            {t("settings.aiEngine.switchHint")}
                          </p>
                        )}
                      </div>
                    )}
                  </>
                ) : (
                  /* ── Cloud API Tab ── */
                  <>
                    {envCheck?.llm_provider === "openai" && (
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-sm">{t("settings.aiEngine.apiStatus")}</span>
                        <span
                          className={cn(
                            "text-xs px-2 py-0.5 rounded-full",
                            envCheck.api_available
                              ? "bg-green-50 text-green-600 dark:bg-green-950/30"
                              : "bg-yellow-50 text-yellow-600 dark:bg-yellow-950/30",
                          )}
                        >
                          {envCheck.api_available ? t("settings.aiEngine.connectedShort") : t("settings.aiEngine.notConnectedShort")}
                        </span>
                      </div>
                    )}

                    {/* Provider select */}
                    {(() => {
                      const DOMESTIC_IDS = ["deepseek", "minimax", "qwen", "moonshot", "zhipu", "siliconflow", "yi"]
                      const INTL_IDS = ["openai", "anthropic", "gemini"]
                      const domestic = cloudProviders.filter((p) => DOMESTIC_IDS.includes(p.id))
                      const intl = cloudProviders.filter((p) => INTL_IDS.includes(p.id))
                      const custom = cloudProviders.filter((p) => !DOMESTIC_IDS.includes(p.id) && !INTL_IDS.includes(p.id))
                      const selectedTag = providerTagLabel(cloudProvider)
                      return (
                        <div>
                          <span className="text-sm block mb-1.5">{t("settings.aiEngine.cloud.provider")}</span>
                          <div className="flex items-center gap-2">
                            <select
                              className="flex-1 border rounded px-2 py-1.5 text-sm bg-background"
                              value={cloudProvider}
                              onChange={(e) => handleProviderChange(e.target.value)}
                            >
                              <option value="">{t("settings.aiEngine.cloud.selectProvider")}</option>
                              {domestic.length > 0 && (
                                <optgroup label={t("settings.aiEngine.cloud.providerGroup.domestic")}>
                                  {domestic.map((p) => (
                                    <option key={p.id} value={p.id}>{p.name}</option>
                                  ))}
                                </optgroup>
                              )}
                              {intl.length > 0 && (
                                <optgroup label={t("settings.aiEngine.cloud.providerGroup.international")}>
                                  {intl.map((p) => (
                                    <option key={p.id} value={p.id}>{p.name}</option>
                                  ))}
                                </optgroup>
                              )}
                              {custom.map((p) => (
                                <option key={p.id} value={p.id}>{p.name}</option>
                              ))}
                            </select>
                            {selectedTag && (
                              <span className="text-xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground whitespace-nowrap">
                                {selectedTag}
                              </span>
                            )}
                          </div>
                        </div>
                      )
                    })()}

                    {/* Base URL */}
                    <div>
                      <span className="text-sm block mb-1.5">{t("settings.aiEngine.cloud.baseUrl")}</span>
                      <input
                        className="w-full border rounded px-2 py-1.5 text-sm bg-background font-mono"
                        value={cloudBaseUrl}
                        onChange={(e) => setCloudBaseUrl(e.target.value)}
                        placeholder="https://api.example.com/v1"
                      />
                    </div>

                    {/* Model */}
                    {(() => {
                      const selectedProvider = cloudProviders.find((p) => p.id === cloudProvider)
                      const hasPresets = selectedProvider?.models && selectedProvider.models.length > 0
                      return (
                        <div>
                          <span className="text-sm block mb-1.5">{t("settings.aiEngine.cloud.model")}</span>
                          {hasPresets && !isCustomModel ? (
                            <select
                              className="w-full border rounded px-2 py-1.5 text-sm bg-background font-mono"
                              value={cloudModel}
                              onChange={(e) => {
                                if (e.target.value === "__custom__") {
                                  setIsCustomModel(true)
                                  setCloudModel("")
                                } else {
                                  setCloudModel(e.target.value)
                                }
                              }}
                            >
                              {selectedProvider!.models!.map((m) => (
                                <option key={m} value={m}>{m}</option>
                              ))}
                              <option value="__custom__">{t("settings.aiEngine.cloud.customModel")}</option>
                            </select>
                          ) : (
                            <input
                              className="w-full border rounded px-2 py-1.5 text-sm bg-background font-mono"
                              value={cloudModel}
                              onChange={(e) => setCloudModel(e.target.value)}
                              placeholder="model-name"
                            />
                          )}
                        </div>
                      )
                    })()}

                    {/* API Key */}
                    <div>
                      <span className="text-sm block mb-1.5">API Key</span>
                      <div className="flex gap-2">
                        <input
                          type="password"
                          className="flex-1 border rounded px-2 py-1.5 text-sm bg-background font-mono"
                          value={cloudApiKey}
                          onChange={(e) => {
                            setCloudApiKey(e.target.value)
                            setCloudValidResult(null)
                          }}
                          placeholder={cloudConfig?.has_api_key ? cloudConfig.api_key_masked : "sk-..."}
                        />
                        <Button
                          variant="outline"
                          size="xs"
                          onClick={handleValidateCloud}
                          disabled={cloudValidating || !cloudApiKey || !cloudBaseUrl}
                        >
                          {cloudValidating ? t("settings.aiEngine.cloud.validating") : t("settings.aiEngine.cloud.validate")}
                        </Button>
                      </div>
                      {cloudProviders.find((p) => p.id === cloudProvider)?.api_format === "anthropic" && (
                        <p className="text-xs mt-1 text-muted-foreground">
                          {t("settings.aiEngine.cloud.anthropicHint")}
                        </p>
                      )}
                      {cloudValidResult && (
                        <p
                          className={cn(
                            "text-xs mt-1",
                            cloudValidResult.valid ? "text-green-600" : "text-red-500",
                          )}
                        >
                          {cloudValidResult.valid ? t("settings.aiEngine.cloud.validationSuccess") : cloudValidResult.error}
                        </p>
                      )}
                    </div>

                    {/* Save cloud config */}
                    <div className="flex items-center gap-3 pt-2">
                      <Button
                        size="xs"
                        onClick={handleSaveCloud}
                        disabled={cloudSaving || !cloudProvider || !cloudBaseUrl || !cloudModel || (!cloudApiKey && !cloudConfig?.has_api_key)}
                      >
                        {cloudSaving ? t("settings.aiEngine.cloud.saving") : t("settings.aiEngine.cloud.saveConfig")}
                      </Button>
                      {cloudSaveMsg && (
                        <span className="text-xs text-muted-foreground">{cloudSaveMsg}</span>
                      )}
                    </div>

                    {/* Performance benchmark (cloud) */}
                    {(envCheck?.api_available || cloudConfig?.has_api_key) && (
                      <div className="border-t pt-3 mt-3">
                        <div className="flex items-center gap-3">
                          <Button
                            variant="outline"
                            size="xs"
                            onClick={handleBenchmark}
                            disabled={benchmarking}
                          >
                            {benchmarking ? t("settings.aiEngine.benchmark.testing") : t("settings.aiEngine.benchmark.run")}
                          </Button>
                          {benchmarking && (
                            <span className="text-xs text-muted-foreground">{t("settings.aiEngine.benchmark.waiting")}</span>
                          )}
                        </div>
                        {benchmarkResult && (
                          <div className="mt-2 rounded-md border p-2.5 text-xs space-y-1">
                            <div className="flex flex-wrap gap-x-4 gap-y-0.5">
                              <span>{t("settings.aiEngine.benchmark.responseTime", { seconds: (benchmarkResult.benchmark.elapsed_ms / 1000).toFixed(1) })}</span>
                              <span>{t("settings.aiEngine.benchmark.speed", { speed: benchmarkResult.benchmark.tokens_per_second })}</span>
                              <span>
                                {t("settings.aiEngine.benchmark.estimatedChapter", { seconds: benchmarkResult.benchmark.estimated_chapter_time_s })}
                                {" "}
                                <span className="text-muted-foreground font-normal">
                                  {t("settings.aiEngine.benchmark.approxChars", { chars: benchmarkResult.benchmark.estimated_chapter_chars })}
                                </span>
                              </span>
                              <span>{t("settings.aiEngine.benchmark.contextWindow", { size: (benchmarkResult.context_window / 1024).toFixed(0) })}</span>
                            </div>
                            <div className="flex items-center gap-2">
                              <span>{t("settings.aiEngine.benchmark.analysisQuality")}</span>
                              <strong className={cn(
                                benchmarkResult.quality.overall_score >= 80 ? "text-green-600" :
                                benchmarkResult.quality.overall_score >= 60 ? "text-yellow-600" : "text-red-500"
                              )}>
                                {t("settings.aiEngine.benchmark.scorePoints", { score: benchmarkResult.quality.overall_score })}
                              </strong>
                              <span className="text-muted-foreground">
                                {t("settings.aiEngine.benchmark.qualityBreakdown", {
                                  entityRecall: benchmarkResult.quality.entity_recall,
                                  relationRecall: benchmarkResult.quality.relation_recall,
                                })}
                              </span>
                            </div>
                            {benchmarkResult.quality.notes.length > 0 && (
                              <p className="text-[10px] text-muted-foreground">
                                {benchmarkResult.quality.notes.join(t("common.listSeparator"))}
                              </p>
                            )}
                            <p className="text-[10px] text-muted-foreground">
                              {t("settings.aiEngine.benchmark.tokenUsage", {
                                input: formatTokens(benchmarkResult.benchmark.input_tokens),
                                output: formatTokens(benchmarkResult.benchmark.output_tokens),
                              })}
                            </p>
                          </div>
                        )}
                        {benchmarkError && (
                          <p className="mt-1 text-xs text-red-500">{benchmarkError}</p>
                        )}
                        {/* Benchmark history (cloud) */}
                        {benchmarkHistory.length > 0 && (
                          <div className="mt-3">
                            <button
                              className="flex w-full items-center justify-between text-xs text-muted-foreground hover:text-foreground"
                              onClick={() => setShowBenchmarkHistory(!showBenchmarkHistory)}
                            >
                              <span>{t("settings.aiEngine.benchmark.history", { count: benchmarkHistory.length })}</span>
                              <span>{showBenchmarkHistory ? t("entity.showLess") : t("common.expand")}</span>
                            </button>
                            {showBenchmarkHistory && (
                              <div className="mt-1.5 border rounded-md overflow-hidden">
                                <table className="w-full text-[11px]">
                                  <thead>
                                    <tr className="bg-muted/40 text-muted-foreground">
                                      <th className="text-left px-2 py-1 font-medium">{t("settings.aiEngine.benchmark.table.time")}</th>
                                      <th className="text-left px-2 py-1 font-medium">{t("settings.aiEngine.benchmark.table.model")}</th>
                                      <th className="text-right px-2 py-1 font-medium">{t("settings.aiEngine.benchmark.table.speed")}</th>
                                      <th className="text-right px-2 py-1 font-medium">{t("settings.aiEngine.benchmark.table.estimatedChapter")}</th>
                                      <th className="text-right px-2 py-1 font-medium">{t("settings.aiEngine.benchmark.table.quality")}</th>
                                      <th className="text-right px-2 py-1 font-medium"></th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {benchmarkHistory.map((rec) => (
                                      <tr key={rec.id} className="border-t hover:bg-muted/20">
                                        <td className="px-2 py-1 text-muted-foreground">{formatDateTime(rec.created_at)}</td>
                                        <td className="px-2 py-1 font-mono">{rec.model}</td>
                                        <td className="px-2 py-1 text-right font-mono">{rec.tokens_per_second} t/s</td>
                                        <td className="px-2 py-1 text-right font-mono">{t("settings.aiEngine.benchmark.table.estimatedChapterValue", { seconds: rec.estimated_chapter_time_s })}</td>
                                        <td className={cn(
                                          "px-2 py-1 text-right font-mono",
                                          rec.quality_score != null && rec.quality_score >= 80 ? "text-green-600" :
                                          rec.quality_score != null && rec.quality_score >= 60 ? "text-yellow-600" :
                                          rec.quality_score != null ? "text-red-500" : "text-muted-foreground",
                                        )}>
                                          {rec.quality_score != null ? t("settings.aiEngine.benchmark.scorePoints", { score: rec.quality_score }) : "-"}
                                        </td>
                                        <td className="px-2 py-1 text-right">
                                          <button
                                            className="text-muted-foreground hover:text-red-500 transition-colors"
                                            onClick={() => handleDeleteBenchmarkRecord(rec.id)}
                                          >
                                            {t("common.delete")}
                                          </button>
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Switch button — only when cloud is NOT the active engine */}
                    {envCheck?.llm_provider !== "openai" && (
                      <div className="border-t pt-3 mt-3">
                        <Button
                          onClick={handleRequestSwitch}
                          disabled={modeSwitching || !cloudConfig?.has_api_key}
                          size="sm"
                        >
                          {modeSwitching ? t("settings.aiEngine.switching") : t("settings.aiEngine.switchToThisEngine")}
                        </Button>
                        {!cloudConfig?.has_api_key && (
                          <p className="text-[10px] text-muted-foreground mt-1">
                            {t("settings.aiEngine.cloud.saveConfigFirst")}
                          </p>
                        )}
                        {cloudConfig?.has_api_key && (
                          <p className="text-[10px] text-muted-foreground mt-1">
                            {t("settings.aiEngine.switchHint")}
                          </p>
                        )}
                      </div>
                    )}
                  </>
                )}

                {/* Footer: refresh + restore */}
                <div className="border-t pt-3 mt-3 flex items-center gap-3">
                  <Button variant="outline" size="xs" onClick={refreshEnv}>
                    {t("settings.aiEngine.refreshStatus")}
                  </Button>
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={handleRestoreDefaults}
                    disabled={restoring}
                  >
                    {restoring ? t("settings.aiEngine.restoringDefaults") : t("settings.aiEngine.restoreDefaults")}
                  </Button>
                </div>
              </div>
            </div>

            {/* Switch confirmation dialog */}
            {showSwitchDialog && (
              <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
                <div className="mx-4 w-full max-w-sm rounded-lg border bg-background p-5 shadow-lg">
                  <h3 className="text-sm font-medium mb-3">
                    {runningTaskCount > 0 ? t("settings.aiEngine.switchDialog.titleWarning") : t("settings.aiEngine.switchDialog.title")}
                  </h3>

                  {runningTaskCount > 0 && (
                    <div className="mb-3 rounded-md border border-yellow-200 bg-yellow-50/60 px-3 py-2 text-xs dark:border-yellow-900 dark:bg-yellow-950/20">
                      <p className="font-medium text-yellow-700 dark:text-yellow-300">
                        {t("settings.aiEngine.switchDialog.runningTasks", { count: runningTaskCount })}
                      </p>
                      <ul className="mt-1 space-y-0.5 text-yellow-600 dark:text-yellow-400">
                        <li>{t("settings.aiEngine.switchDialog.runningTasksLine1")}</li>
                        <li>{t("settings.aiEngine.switchDialog.runningTasksLine2")}</li>
                      </ul>
                    </div>
                  )}

                  <p className="text-sm text-muted-foreground mb-4">
                    {t("settings.aiEngine.switchDialog.body", {
                      currentMode: providerModeLabel(envCheck?.llm_provider),
                      currentModel: envCheck?.llm_model || "unknown",
                      targetMode: providerModeLabel(viewTab),
                      targetModel: viewTab === "openai" ? (cloudModel || cloudConfig?.model || "?") : (selectedOllamaModel || "qwen3:8b"),
                    })}
                  </p>

                  <div className="flex justify-end gap-2">
                    <Button variant="outline" size="sm" onClick={() => setShowSwitchDialog(false)}>
                      {t("common.cancel")}
                    </Button>
                    <Button size="sm" onClick={handleConfirmSwitch}>
                      {t("settings.aiEngine.switchDialog.confirm")}
                    </Button>
                  </div>
                </div>
              </div>
            )}
          </section>

          {/* Interface Language */}
          <section id="sec-language" className="scroll-mt-12">
            <h2 className="text-base font-medium mb-4">{t("settings.interfaceLanguage")}</h2>
            <div className="border rounded-lg p-4 space-y-3">
              <div>
                <span className="text-sm block mb-2">
                  {t("settings.language")}
                </span>
                <div className="grid gap-2 sm:grid-cols-3">
                  {supportedLocales.map((item) => (
                    <button
                      key={item}
                      type="button"
                      aria-pressed={locale === item}
                      className={cn(
                        "flex items-center gap-3 rounded-md border px-3 py-2 text-left text-sm transition-colors",
                        locale === item
                          ? "border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-950/30 dark:text-blue-200"
                          : "border-border bg-background hover:bg-muted",
                      )}
                      onClick={() => setLocale(item)}
                    >
                      <img
                        src={LOCALE_FLAGS[item]}
                        alt=""
                        className="h-5 w-7 shrink-0 rounded-[2px] object-cover ring-1 ring-border"
                      />
                      <span className="min-w-0">
                        <span className="block truncate font-medium">{LOCALE_LABELS[item]}</span>
                        <span className="block text-[10px] text-muted-foreground">{item}</span>
                      </span>
                    </button>
                  ))}
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                {t("settings.interfaceLanguageDescription")}
              </p>
              <p className="text-[10px] text-muted-foreground">
                {t("settings.languageCurrent", { language: LOCALE_LABELS[locale] })}
              </p>
            </div>
          </section>

          {/* Usage & Budget */}
          <section id="sec-usage" className="scroll-mt-12">
            <h2 className="text-base font-medium mb-4">{t("settings.nav.usage")}</h2>
            <div className="border rounded-lg p-4 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-sm">{t("settings.usage.monthlyBudget")}</span>
                <div className="flex items-center gap-2">
                  <span className="text-sm text-muted-foreground">¥</span>
                  <input
                    type="number"
                    className="w-20 border rounded px-2 py-1 text-sm bg-background font-mono text-right"
                    value={budgetAmount}
                    onChange={(e) => setBudgetAmount(parseFloat(e.target.value) || 0)}
                    min={0}
                    step={10}
                  />
                  <Button
                    variant="outline"
                    size="xs"
                    onClick={handleSaveBudget}
                    disabled={budgetSaving}
                  >
                    {t("common.save")}
                  </Button>
                </div>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm">{t("settings.usage.monthlyUsed")}</span>
                <span className="text-sm font-mono">¥{monthlyUsed.toFixed(2)}</span>
              </div>
              {budgetAmount > 0 && (
                <div>
                  <div className="flex justify-between text-xs text-muted-foreground mb-1">
                    <span>{t("settings.usage.budgetUsage")}</span>
                    <span>{Math.min(100, Math.round((monthlyUsed / budgetAmount) * 100))}%</span>
                  </div>
                  <div className="h-2 rounded-full bg-muted overflow-hidden">
                    <div
                      className={cn(
                        "h-full rounded-full transition-all",
                        monthlyUsed / budgetAmount >= 1
                          ? "bg-red-500"
                          : monthlyUsed / budgetAmount >= 0.8
                            ? "bg-yellow-500"
                            : "bg-blue-500",
                      )}
                      style={{ width: `${Math.min(100, (monthlyUsed / budgetAmount) * 100)}%` }}
                    />
                  </div>
                </div>
              )}
              <p className="text-[10px] text-muted-foreground">
                {t("settings.usage.budgetHint")}
              </p>

              {/* Analysis Records */}
              <div className="border-t pt-3 mt-3">
                <span className="text-sm block mb-2">{t("settings.usage.analysisRecords")}</span>
                {recordsLoading ? (
                  <p className="text-xs text-muted-foreground">{t("common.loading")}</p>
                ) : analysisRecords.length === 0 ? (
                  <p className="text-xs text-muted-foreground">{t("settings.usage.noAnalysisRecords")}</p>
                ) : (
                  <div className="space-y-2">
                    {analysisRecords.map((rec) => (
                      <div key={rec.task_id} className="border rounded-md">
                        <button
                          className="w-full text-left p-2.5 hover:bg-muted/30 transition-colors"
                          onClick={() => handleExpandRecord(rec.novel_id)}
                        >
                          <div className="flex items-center justify-between">
                            <div className="flex-1 min-w-0">
                              <span className="text-sm font-medium truncate block">
                                {rec.novel_title}
                              </span>
                              <span className="text-[10px] text-muted-foreground">
                                {t("settings.usage.analysisRecordSummary", {
                                  start: rec.chapter_range[0],
                                  end: rec.chapter_range[1],
                                  count: rec.chapter_count,
                                  startedAt: formatDateTime(rec.started_at),
                                  completedAt: formatDateTime(rec.completed_at),
                                })}
                              </span>
                            </div>
                            <div className="flex items-center gap-2 flex-shrink-0">
                              {rec.total_cost_cny > 0 && (
                                <span className="text-xs font-mono">¥{rec.total_cost_cny.toFixed(2)}</span>
                              )}
                              <span className="text-xs text-muted-foreground">
                                {expandedRecord === rec.novel_id ? "▲" : "▼"}
                              </span>
                            </div>
                          </div>
                        </button>

                        {expandedRecord === rec.novel_id && (
                          <div className="border-t px-2.5 py-2 space-y-2">
                            {costDetailLoading ? (
                              <p className="text-xs text-muted-foreground">{t("settings.usage.loadingDetails")}</p>
                            ) : costDetail ? (
                              <>
                                {/* Summary row */}
                                <div className="grid grid-cols-5 gap-1 text-[10px] text-muted-foreground font-medium border-b pb-1">
                                  <span>{t("settings.usage.recordsTable.chapter")}</span>
                                  <span className="text-right">{t("settings.usage.recordsTable.inputTokens")}</span>
                                  <span className="text-right">{t("settings.usage.recordsTable.outputTokens")}</span>
                                  <span className="text-right">{t("settings.usage.recordsTable.cost")}</span>
                                  <span className="text-right">{t("settings.usage.recordsTable.entities")}</span>
                                </div>
                                <div className="max-h-48 overflow-auto space-y-0.5">
                                  {costDetail.chapters.map((ch) => (
                                    <div
                                      key={ch.chapter_id}
                                      className="grid grid-cols-5 gap-1 text-[10px] py-0.5"
                                    >
                                      <span className="text-muted-foreground">{t("common.chapterShort", { chapter: ch.chapter_id })}</span>
                                      <span className="text-right font-mono">{formatTokens(ch.input_tokens)}</span>
                                      <span className="text-right font-mono">{formatTokens(ch.output_tokens)}</span>
                                      <span className="text-right font-mono">
                                        {ch.cost_cny > 0 ? `¥${ch.cost_cny.toFixed(3)}` : "-"}
                                      </span>
                                      <span className="text-right">{ch.entity_count}</span>
                                    </div>
                                  ))}
                                </div>
                                {/* Totals */}
                                <div className="grid grid-cols-5 gap-1 text-[10px] font-medium border-t pt-1">
                                  <span>{t("settings.usage.recordsTable.total")}</span>
                                  <span className="text-right font-mono">
                                    {formatTokens(costDetail.summary.total_input_tokens)}
                                  </span>
                                  <span className="text-right font-mono">
                                    {formatTokens(costDetail.summary.total_output_tokens)}
                                  </span>
                                  <span className="text-right font-mono">
                                    {costDetail.summary.total_cost_cny > 0
                                      ? `¥${costDetail.summary.total_cost_cny.toFixed(2)}`
                                      : "-"}
                                  </span>
                                  <span className="text-right">{costDetail.summary.total_entities}</span>
                                </div>
                                {/* Model + CSV export */}
                                <div className="flex items-center justify-between pt-1">
                                  <span className="text-[10px] text-muted-foreground">
                                    {t("settings.usage.recordsTable.model", { model: costDetail.model || t("settings.aiEngine.provider.localShort") })}
                                  </span>
                                  <Button
                                    variant="ghost"
                                    size="xs"
                                    onClick={() => window.open(costDetailCsvUrl(rec.novel_id), "_blank")}
                                  >
                                    {t("settings.usage.exportCsv")}
                                  </Button>
                                </div>
                              </>
                            ) : (
                              <p className="text-xs text-muted-foreground">{t("settings.usage.noDetailData")}</p>
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </section>

          {/* Reading Preferences */}
          <section id="sec-reading" className="scroll-mt-12">
            <h2 className="text-base font-medium mb-4">{t("settings.readingPreferences")}</h2>
            <div className="border rounded-lg p-4 space-y-4">
              {/* Font size */}
              <div>
                <span className="text-sm block mb-2">{t("settings.reading.fontSize")}</span>
                <div className="flex gap-2">
                  {(Object.keys(FONT_SIZE_MAP) as Array<keyof typeof FONT_SIZE_MAP>).map((size) => (
                    <Button
                      key={size}
                      variant={fontSize === size ? "default" : "outline"}
                      size="xs"
                      onClick={() => setFontSize(size)}
                    >
                      {t(FONT_SIZE_LABEL_KEYS[size])}
                    </Button>
                  ))}
                </div>
                <p className="text-[10px] text-muted-foreground mt-1">
                  {t("settings.reading.current", { value: FONT_SIZE_MAP[fontSize] })}
                </p>
              </div>

              {/* Line height */}
              <div>
                <span className="text-sm block mb-2">{t("settings.reading.lineHeight")}</span>
                <div className="flex gap-2">
                  {(Object.keys(LINE_HEIGHT_MAP) as Array<keyof typeof LINE_HEIGHT_MAP>).map((lh) => (
                    <Button
                      key={lh}
                      variant={lineHeight === lh ? "default" : "outline"}
                      size="xs"
                      onClick={() => setLineHeight(lh)}
                    >
                      {t(LINE_HEIGHT_LABEL_KEYS[lh])}
                    </Button>
                  ))}
                </div>
                <p className="text-[10px] text-muted-foreground mt-1">
                  {t("settings.reading.current", { value: { compact: "1.6x", normal: "2.0x", loose: "2.6x" }[lineHeight] })}
                </p>
              </div>

              {/* Theme */}
              <div>
                <span className="text-sm block mb-2">{t("settings.reading.appearance")}</span>
                <div className="flex gap-2">
                  {(["light", "dark", "system"] as const).map((value) => (
                    <Button
                      key={value}
                      variant={theme === value ? "default" : "outline"}
                      size="xs"
                      onClick={() => setTheme(value)}
                    >
                      {t(THEME_LABEL_KEYS[value])}
                    </Button>
                  ))}
                </div>
              </div>
            </div>
          </section>

          {/* Data Management */}
          <section id="sec-data" className="scroll-mt-12">
            <h2 className="text-base font-medium mb-4">{t("settings.nav.data")}</h2>
            <div className="border rounded-lg p-4 space-y-4">
              {novels.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("settings.data.empty")}</p>
              ) : (
                <div className="space-y-2">
                  {novels.map((novel) => (
                    <div
                      key={novel.id}
                      className="flex items-center justify-between text-sm py-1.5"
                    >
                      <div className="flex-1 min-w-0">
                        <span className="truncate block">{novel.title}</span>
                        <span className="text-[10px] text-muted-foreground">
                          {t("common.chapterCount", { count: novel.total_chapters })}
                          {t("common.listSeparator")}
                          {t("bookshelf.wordCountWan", { count: (novel.total_words / 10000).toFixed(1) })}
                          {t("common.listSeparator")}
                          {t("settings.data.analysisProgress", { percent: Math.round(novel.analysis_progress * 100) })}
                        </span>
                      </div>
                      <div className="flex gap-1.5 flex-shrink-0">
                        <Button
                          variant="outline"
                          size="xs"
                          onClick={() => handleExport(novel.id)}
                        >
                          {t("common.export")}
                        </Button>
                        <Button
                          variant="outline"
                          size="xs"
                          onClick={() => navigate(novelPath(novel.id, "analysis"))}
                        >
                          {t("nav.analysis")}
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Import section */}
              <div className="border-t pt-4">
                <div className="flex items-center gap-3">
                  <span className="text-sm">{t("settings.data.import.title")}</span>
                  <input
                    ref={importFileRef}
                    type="file"
                    accept=".json"
                    className="hidden"
                    onChange={handleImportFileChange}
                  />
                  <Button
                    variant="outline"
                    size="xs"
                    onClick={() => importFileRef.current?.click()}
                    disabled={importing}
                  >
                    {t("settings.data.import.selectFile")}
                  </Button>
                </div>

                {/* Import preview */}
                {importPreview && (
                  <div className="mt-3 border rounded-lg p-3 space-y-2 bg-muted/30">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">{importPreview.title}</span>
                      {importPreview.existing_novel_id && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-50 text-yellow-600 dark:bg-yellow-950/30">
                          {t("settings.data.import.existingNovel")}
                        </span>
                      )}
                    </div>
                    {importPreview.author && (
                      <p className="text-xs text-muted-foreground">{importPreview.author}</p>
                    )}
                    <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                      <span>{t("common.chapterCount", { count: importPreview.total_chapters })}</span>
                      <span>{t("bookshelf.wordCountWan", { count: (importPreview.total_words / 10000).toFixed(1) })}</span>
                      <span>{t("shared.upload.analyzedChapters", { count: importPreview.analyzed_chapters })}</span>
                      <span>{t("settings.data.import.analysisFacts", { count: importPreview.facts_count })}</span>
                      <span>{formatBytes(importPreview.data_size_bytes)}</span>
                    </div>
                    <div className="flex gap-2 pt-1">
                      {importPreview.existing_novel_id ? (
                        <>
                          <Button
                            size="xs"
                            onClick={() => handleConfirmImport(false)}
                            disabled={importing}
                          >
                            {importing ? t("shared.upload.importing") : t("shared.upload.importAsNew")}
                          </Button>
                          <Button
                            variant="outline"
                            size="xs"
                            onClick={() => handleConfirmImport(true)}
                            disabled={importing}
                          >
                            {t("shared.upload.overwriteExisting")}
                          </Button>
                        </>
                      ) : (
                        <Button
                          size="xs"
                          onClick={() => handleConfirmImport(false)}
                          disabled={importing}
                        >
                          {importing ? t("shared.upload.importing") : t("shared.upload.confirmImport")}
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="xs"
                        onClick={cancelImport}
                        disabled={importing}
                      >
                        {t("common.cancel")}
                      </Button>
                    </div>
                  </div>
                )}

                {importResult && (
                  <p className="mt-2 text-xs text-green-600">{importResult}</p>
                )}
                {importError && (
                  <p className="mt-2 text-xs text-red-600">{importError}</p>
                )}
              </div>
            </div>
          </section>

          {/* Full Backup / Restore */}
          <section id="sec-backup" className="scroll-mt-12">
            <h2 className="text-base font-medium mb-4">{t("settings.nav.backup")}</h2>
            <div className="border rounded-lg p-4 space-y-4">
              <div className="flex items-center gap-3">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={async () => {
                    try {
                      await downloadBackupExport()
                    } catch (err) {
                      alert(err instanceof Error ? err.message : translate("bookshelf.backupExportFailed"))
                    }
                  }}
                >
                  {t("settings.backup.exportZip")}
                </Button>
                <span className="text-xs text-muted-foreground">
                  {t("settings.backup.exportDescription")}
                </span>
              </div>

              <div className="border-t pt-4">
                <input
                  ref={backupFileRef}
                  type="file"
                  accept=".zip"
                  className="hidden"
                  onChange={async (e) => {
                    const f = e.target.files?.[0]
                    if (!f) return
                    setBackupFile(f)
                    setBackupResult(null)
                    setBackupError(null)
                    try {
                      const preview = await previewBackupImport(f)
                      setBackupPreview(preview)
                    } catch (err) {
                      setBackupError(err instanceof Error ? err.message : translate("settings.backup.previewFailed"))
                    }
                  }}
                />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => backupFileRef.current?.click()}
                >
                  {t("settings.backup.restoreZip")}
                </Button>

                {backupPreview && backupFile && (
                  <div className="mt-3 p-3 bg-muted/50 rounded text-sm space-y-2">
                    <p>
                      {t("settings.backup.preview.exportedAt", {
                        value: backupPreview.exported_at ? formatDateTime(backupPreview.exported_at) : "-",
                      })}
                      {t("common.listSeparator")}
                      {t("settings.backup.preview.size", {
                        value: formatBytes(backupPreview.zip_size_bytes),
                      })}
                    </p>
                    <p>
                      {t("settings.backup.preview.novelCount", { count: backupPreview.novel_count })}
                      {backupPreview.conflict_count > 0 && (
                        <span className="text-amber-600 ml-2">
                          {t("settings.backup.preview.conflictCount", { count: backupPreview.conflict_count })}
                        </span>
                      )}
                    </p>
                    <ul className="text-xs space-y-0.5 max-h-32 overflow-y-auto">
                      {backupPreview.novels.map((n) => (
                        <li key={n.id} className="flex items-center gap-1.5">
                          <span>{n.title}</span>
                          <span className="text-muted-foreground">
                            ({t("common.chapterCount", { count: n.total_chapters })})
                          </span>
                          {n.conflict && (
                            <span className="text-amber-600 text-[10px]">{t("settings.backup.preview.exists")}</span>
                          )}
                        </li>
                      ))}
                    </ul>
                    <div className="flex gap-2 mt-2">
                      <Button
                        size="xs"
                        disabled={backupImporting}
                        onClick={async () => {
                          setBackupImporting(true)
                          setBackupError(null)
                          try {
                            const r = await confirmBackupImport(backupFile, "skip")
                            setBackupResult(r)
                            setBackupPreview(null)
                          } catch (err) {
                            setBackupError(err instanceof Error ? err.message : translate("settings.backup.importFailed"))
                          } finally {
                            setBackupImporting(false)
                          }
                        }}
                      >
                        {backupImporting ? t("shared.upload.importing") : t("settings.backup.import.skipExisting")}
                      </Button>
                      {backupPreview.conflict_count > 0 && (
                        <Button
                          size="xs"
                          variant="outline"
                          disabled={backupImporting}
                          onClick={async () => {
                            setBackupImporting(true)
                            setBackupError(null)
                            try {
                              const r = await confirmBackupImport(backupFile, "overwrite")
                              setBackupResult(r)
                              setBackupPreview(null)
                            } catch (err) {
                              setBackupError(err instanceof Error ? err.message : translate("settings.backup.importFailed"))
                            } finally {
                              setBackupImporting(false)
                            }
                          }}
                        >
                          {t("settings.backup.import.overwriteExisting")}
                        </Button>
                      )}
                      <Button
                        size="xs"
                        variant="ghost"
                        onClick={() => {
                          setBackupPreview(null)
                          setBackupFile(null)
                        }}
                      >
                        {t("common.cancel")}
                      </Button>
                    </div>
                  </div>
                )}

                {backupResult && (
                  <p className="mt-2 text-xs text-green-600">
                    {t("settings.backup.import.result", {
                      imported: backupResult.imported,
                      overwritten: backupResult.overwritten,
                      skipped: backupResult.skipped,
                      errors: backupResult.errors.length,
                    })}
                  </p>
                )}
                {backupError && (
                  <p className="mt-2 text-xs text-red-600">{backupError}</p>
                )}
              </div>
            </div>
          </section>

          {/* Usage Analytics & Privacy */}
          <section id="sec-privacy" className="scroll-mt-12">
            <h2 className="text-base font-medium mb-4">{t("settings.nav.privacy")}</h2>
            <div className="border rounded-lg p-4 space-y-4">
              {/* Privacy toggle */}
              <div className="flex items-center justify-between">
                <div>
                  <span className="text-sm">{t("settings.privacy.tracking")}</span>
                  <p className="text-[10px] text-muted-foreground">
                    {t("settings.privacy.trackingDescription")}
                  </p>
                </div>
                <button
                  className={cn(
                    "relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors",
                    trackingEnabled ? "bg-blue-500" : "bg-muted",
                  )}
                  onClick={async () => {
                    const next = !trackingEnabled
                    setTrackingEnabled(next)
                    await apiFetch("/usage/tracking-enabled", {
                      method: "PUT",
                      body: JSON.stringify({ enabled: next }),
                    })
                  }}
                >
                  <span
                    className={cn(
                      "pointer-events-none block h-4 w-4 rounded-full bg-white shadow transition-transform",
                      trackingEnabled ? "translate-x-4" : "translate-x-0",
                    )}
                  />
                </button>
              </div>

              {usageLoading ? (
                <p className="text-sm text-muted-foreground">{t("common.loading")}</p>
              ) : usageStats ? (
                <>
                  <div className="flex items-center justify-between">
                    <span className="text-sm">{t("settings.privacy.totalEvents")}</span>
                    <span className="text-sm font-mono">{usageStats.total_events}</span>
                  </div>

                  {/* Event type frequency ranking */}
                  {usageStats.by_type.length > 0 && (
                    <div>
                      <span className="text-sm block mb-2">{t("settings.privacy.frequency30Days")}</span>
                      <div className="space-y-1.5">
                        {usageStats.by_type.slice(0, 10).map((item) => {
                          const maxCount = usageStats.by_type[0]?.count || 1
                          return (
                            <div key={item.event_type} className="flex items-center gap-2">
                              <span className="text-xs text-muted-foreground w-32 shrink-0 truncate">
                                {usageEventLabel(item.event_type)}
                              </span>
                              <div className="flex-1 h-3 bg-muted rounded overflow-hidden">
                                <div
                                  className="h-full bg-blue-500 rounded"
                                  style={{ width: `${(item.count / maxCount) * 100}%` }}
                                />
                              </div>
                              <span className="text-xs font-mono w-8 text-right">{item.count}</span>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )}

                  {/* Daily trend */}
                  {usageStats.daily_trend.length > 0 && (
                    <div>
                      <span className="text-sm block mb-2">{t("settings.privacy.dailyTrend")}</span>
                      <div className="flex items-end gap-px h-16">
                        {usageStats.daily_trend.map((d) => {
                          const maxDay = Math.max(...usageStats.daily_trend.map((t) => t.count), 1)
                          return (
                            <div
                              key={d.day}
                              className="flex-1 bg-blue-400 dark:bg-blue-600 rounded-t min-w-[2px]"
                              style={{ height: `${(d.count / maxDay) * 100}%` }}
                              title={`${d.day}: ${d.count}`}
                            />
                          )
                        })}
                      </div>
                      <div className="flex justify-between text-[10px] text-muted-foreground mt-1">
                        <span>{usageStats.daily_trend[0]?.day.slice(5)}</span>
                        <span>{usageStats.daily_trend[usageStats.daily_trend.length - 1]?.day.slice(5)}</span>
                      </div>
                    </div>
                  )}

                  {usageStats.by_type.length === 0 && usageStats.daily_trend.length === 0 && (
                    <p className="text-sm text-muted-foreground">{t("settings.privacy.noUsageData")}</p>
                  )}

                  {/* Clear data */}
                  <div className="border-t pt-3 mt-3">
                    <Button
                      variant="ghost"
                      size="xs"
                      onClick={async () => {
                        await apiFetch("/usage/clear", { method: "DELETE" })
                        setUsageStats({ total_events: 0, by_type: [], daily_trend: [] })
                      }}
                    >
                      {t("settings.privacy.clearUsageData")}
                    </Button>
                    <span className="text-[10px] text-muted-foreground ml-2">
                      {t("settings.privacy.localOnly")}
                    </span>
                  </div>
                </>
              ) : (
                <p className="text-sm text-muted-foreground">{t("settings.privacy.loadFailed")}</p>
              )}
            </div>
          </section>

          {/* Version info */}
          <section className="text-center text-[10px] text-muted-foreground pb-8 space-y-1">
            <p>{t("settings.footer.versionLine", { version: __APP_VERSION__ })}</p>
            <p>
              <a href="https://ai-reader.cc/docs/" target="_blank" rel="noopener" className="text-blue-500 hover:text-blue-400 transition">{t("settings.footer.docs")}</a>
              {" · "}
              <a href="https://ai-reader.cc/docs/faq" target="_blank" rel="noopener" className="text-blue-500 hover:text-blue-400 transition">{t("settings.footer.faq")}</a>
            </p>
          </section>
        </div>
      </div>
    </div>
  )
}
