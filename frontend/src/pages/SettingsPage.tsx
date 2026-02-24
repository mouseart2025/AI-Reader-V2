import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { apiFetch, checkEnvironment, startOllama, fetchModelRecommendations, pullOllamaModel, setDefaultModel, fetchCloudProviders, fetchCloudConfig, saveCloudConfig, validateCloudApi, fetchNovels, exportNovelUrl, previewImport, confirmDataImport, fetchSettings, switchLlmMode, fetchRunningTasks, restoreDefaults, fetchBudget, setBudget, fetchAnalysisRecords, fetchCostDetail, costDetailCsvUrl, backupExportUrl, previewBackupImport, confirmBackupImport, runModelBenchmark, fetchBenchmarkHistory, deleteBenchmarkRecord } from "@/api/client"
import type { BenchmarkResult, BenchmarkRecord, EnvironmentCheck, OllamaModel, ModelRecommendation, CloudProvider, CloudConfig, Novel, ImportPreview, AnalysisRecord, CostDetailResponse, BackupPreview, BackupImportResult } from "@/api/types"
import { useReadingSettingsStore, FONT_SIZE_MAP, LINE_HEIGHT_MAP } from "@/stores/readingSettingsStore"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

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
  const [envCheck, setEnvCheck] = useState<EnvironmentCheck | null>(null)
  const [envLoading, setEnvLoading] = useState(true)
  const [novels, setNovels] = useState<Novel[]>([])

  const { fontSize, lineHeight, setFontSize, setLineHeight } = useReadingSettingsStore()

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
      setBenchmarkError(err instanceof Error ? err.message : "性能测试失败")
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
      setCloudValidResult({ valid: false, error: "验证请求失败" })
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
        setCloudSaveMsg(`已保存（密钥存储: ${res.storage}）`)
        setCloudApiKey("")
        refreshEnv()
        fetchCloudConfig().then(setCloudConfig).catch(() => {})
      }
    } catch {
      setCloudSaveMsg("保存失败")
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
      .then(setEnvCheck)
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
          prev ? { ...prev, error: res.error ?? "启动失败" } : prev,
        )
      }
    } catch {
      setEnvCheck((prev) =>
        prev ? { ...prev, error: "启动请求失败" } : prev,
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
      setImportError(err instanceof Error ? err.message : "Preview failed")
      setImportPreview(null)
    }
  }, [])

  const handleConfirmImport = useCallback(async (overwrite: boolean) => {
    if (!importFile) return
    setImporting(true)
    setImportError(null)
    try {
      await confirmDataImport(importFile, overwrite)
      setImportResult("导入成功")
      setImportFile(null)
      setImportPreview(null)
      // Refresh novel list
      fetchNovels().then((data) => setNovels(data.novels))
    } catch (err) {
      setImportError(err instanceof Error ? err.message : "Import failed")
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
          &larr; 书架
        </button>
        <span className="text-sm font-medium">设置</span>
      </header>

      <div className="flex-1 overflow-auto" id="settings-scroll">
        {/* Quick navigation */}
        <nav className="sticky top-0 z-10 border-b bg-background/95 backdrop-blur">
          <div className="max-w-2xl mx-auto flex gap-1 px-6 py-1.5 overflow-x-auto">
            {[
              { id: "sec-engine", label: "AI 引擎" },
              { id: "sec-usage", label: "使用统计" },
              { id: "sec-reading", label: "阅读偏好" },
              { id: "sec-data", label: "数据管理" },
              { id: "sec-backup", label: "全量备份" },
              { id: "sec-privacy", label: "统计与隐私" },
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
            <h2 className="text-base font-medium mb-4">AI 引擎</h2>

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
                      {envCheck.llm_provider === "openai" ? "云端 API" : "本地 Ollama"}
                    </span>
                    <span className="rounded bg-background/80 px-1.5 py-0.5 text-xs font-mono text-muted-foreground">
                      {envCheck.llm_model || "未配置"}
                    </span>
                  </div>
                  <p className="text-[10px] text-muted-foreground mt-0.5">
                    {envCheck.llm_provider === "openai"
                      ? envCheck.api_available
                        ? `已连接 · ${envCheck.llm_base_url || ""}`
                        : "未连接 — 请检查 API 配置"
                      : envCheck.ollama_status === "running"
                        ? envCheck.model_available ? "运行中" : `运行中 · 模型 ${envCheck.llm_model} 未安装`
                        : envCheck.ollama_status === "installed_not_running"
                          ? "已安装但未运行"
                          : "未安装 Ollama"}
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
                  本地 Ollama
                  {envCheck?.llm_provider !== "openai" && (
                    <span className="ml-1.5 inline-block rounded-full bg-green-100 px-1.5 py-0.5 text-[10px] text-green-600 dark:bg-green-900/40 dark:text-green-300">
                      使用中
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
                  云端 API
                  {envCheck?.llm_provider === "openai" && (
                    <span className="ml-1.5 inline-block rounded-full bg-green-100 px-1.5 py-0.5 text-[10px] text-green-600 dark:bg-green-900/40 dark:text-green-300">
                      使用中
                    </span>
                  )}
                </button>
              </div>

              <div className="p-4 space-y-3">
                {envLoading ? (
                  <p className="text-sm text-muted-foreground">检测中...</p>
                ) : viewTab === "ollama" ? (
                  /* ── Local Ollama Tab ── */
                  <>
                    <div className="flex items-center justify-between">
                      <span className="text-sm">Ollama 状态</span>
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
                          {envCheck?.ollama_status === "running"
                            ? "运行中"
                            : envCheck?.ollama_status === "installed_not_running"
                              ? "已安装未运行"
                              : "未安装"}
                        </span>
                        {envCheck?.ollama_status === "installed_not_running" && (
                          <Button
                            variant="outline"
                            size="xs"
                            onClick={handleStartOllama}
                            disabled={ollamaStarting}
                          >
                            {ollamaStarting ? "启动中..." : "启动 Ollama"}
                          </Button>
                        )}
                        {envCheck?.ollama_status === "not_installed" && (
                          <Button
                            variant="outline"
                            size="xs"
                            onClick={() => window.open("https://ollama.com/download", "_blank")}
                          >
                            下载安装
                          </Button>
                        )}
                      </div>
                    </div>

                    {/* Model selection dropdown */}
                    {envCheck?.ollama_status === "running" && (envCheck.available_models?.length ?? 0) > 0 && (
                      <div className="flex items-center justify-between">
                        <span className="text-sm">当前模型</span>
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
                      <span className="text-sm">API 地址</span>
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
                        <span className="text-sm block mb-2">模型推荐</span>
                        {recRamGb > 0 && (
                          <p className="text-[10px] text-muted-foreground mb-2">
                            系统内存: {recRamGb} GB
                          </p>
                        )}
                        {recLoading ? (
                          <p className="text-xs text-muted-foreground">加载中...</p>
                        ) : recommendations.length === 0 ? (
                          <p className="text-xs text-muted-foreground">无可推荐模型</p>
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
                                        推荐
                                      </span>
                                    )}
                                    {rec.installed && (
                                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-100 text-green-600 dark:bg-green-900 dark:text-green-300">
                                        已安装
                                      </span>
                                    )}
                                  </div>
                                  <p className="text-[10px] text-muted-foreground mt-0.5">
                                    {rec.description} · {rec.size_gb} GB · 需 {rec.min_ram_gb} GB+ 内存
                                  </p>
                                </div>
                                <div className="flex-shrink-0 ml-3">
                                  {rec.installed ? (
                                    <Button variant="ghost" size="xs" disabled>
                                      已安装
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
                                          : "准备中..."}
                                      </p>
                                    </div>
                                  ) : (
                                    <Button
                                      variant="outline"
                                      size="xs"
                                      onClick={() => handlePullModel(rec.name)}
                                      disabled={pullingModel !== null}
                                    >
                                      下载
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
                            {benchmarking ? "测试中..." : "性能测试"}
                          </Button>
                          {benchmarking && (
                            <span className="text-xs text-muted-foreground">正在调用模型，请稍候...</span>
                          )}
                        </div>
                        {benchmarkResult && (
                          <div className="mt-2 rounded-md border p-2.5 text-xs space-y-1">
                            <div className="flex flex-wrap gap-x-4 gap-y-0.5">
                              <span>响应时间: <strong>{(benchmarkResult.benchmark.elapsed_ms / 1000).toFixed(1)}秒</strong></span>
                              <span>速度: <strong>{benchmarkResult.benchmark.tokens_per_second} token/s</strong></span>
                              <span>预估单章: <strong>~{benchmarkResult.benchmark.estimated_chapter_time_s}秒</strong> <span className="text-muted-foreground font-normal">(约{benchmarkResult.benchmark.estimated_chapter_chars}字)</span></span>
                              <span>上下文窗口: <strong>{(benchmarkResult.context_window / 1024).toFixed(0)}K</strong></span>
                            </div>
                            <div className="flex items-center gap-2">
                              <span>分析质量:</span>
                              <strong className={cn(
                                benchmarkResult.quality.overall_score >= 80 ? "text-green-600" :
                                benchmarkResult.quality.overall_score >= 60 ? "text-yellow-600" : "text-red-500"
                              )}>
                                {benchmarkResult.quality.overall_score}分
                              </strong>
                              <span className="text-muted-foreground">
                                (实体识别 {benchmarkResult.quality.entity_recall}% | 关系识别 {benchmarkResult.quality.relation_recall}%)
                              </span>
                            </div>
                            {benchmarkResult.quality.notes.length > 0 && (
                              <p className="text-[10px] text-muted-foreground">
                                {benchmarkResult.quality.notes.join("，")}
                              </p>
                            )}
                            <p className="text-[10px] text-muted-foreground">
                              输入 {formatTokens(benchmarkResult.benchmark.input_tokens)} / 输出 {formatTokens(benchmarkResult.benchmark.output_tokens)} tokens
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
                              <span>历史记录 ({benchmarkHistory.length})</span>
                              <span>{showBenchmarkHistory ? "收起" : "展开"}</span>
                            </button>
                            {showBenchmarkHistory && (
                              <div className="mt-1.5 border rounded-md overflow-hidden">
                                <table className="w-full text-[11px]">
                                  <thead>
                                    <tr className="bg-muted/40 text-muted-foreground">
                                      <th className="text-left px-2 py-1 font-medium">时间</th>
                                      <th className="text-left px-2 py-1 font-medium">模型</th>
                                      <th className="text-right px-2 py-1 font-medium">速度</th>
                                      <th className="text-right px-2 py-1 font-medium">预估单章</th>
                                      <th className="text-right px-2 py-1 font-medium">质量</th>
                                      <th className="text-right px-2 py-1 font-medium"></th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {benchmarkHistory.map((rec) => (
                                      <tr key={rec.id} className="border-t hover:bg-muted/20">
                                        <td className="px-2 py-1 text-muted-foreground">{formatDateTime(rec.created_at)}</td>
                                        <td className="px-2 py-1 font-mono">{rec.model}</td>
                                        <td className="px-2 py-1 text-right font-mono">{rec.tokens_per_second} t/s</td>
                                        <td className="px-2 py-1 text-right font-mono">~{rec.estimated_chapter_time_s}秒</td>
                                        <td className={cn(
                                          "px-2 py-1 text-right font-mono",
                                          rec.quality_score != null && rec.quality_score >= 80 ? "text-green-600" :
                                          rec.quality_score != null && rec.quality_score >= 60 ? "text-yellow-600" :
                                          rec.quality_score != null ? "text-red-500" : "text-muted-foreground",
                                        )}>
                                          {rec.quality_score != null ? `${rec.quality_score}分` : "-"}
                                        </td>
                                        <td className="px-2 py-1 text-right">
                                          <button
                                            className="text-muted-foreground hover:text-red-500 transition-colors"
                                            onClick={() => handleDeleteBenchmarkRecord(rec.id)}
                                          >
                                            删除
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
                          {modeSwitching ? "切换中..." : "切换到此引擎"}
                        </Button>
                        {envCheck?.ollama_status !== "running" && (
                          <p className="text-[10px] text-muted-foreground mt-1">
                            请先启动 Ollama
                          </p>
                        )}
                        {envCheck?.ollama_status === "running" && (
                          <p className="text-[10px] text-muted-foreground mt-1">
                            切换后新的分析任务将使用此引擎
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
                        <span className="text-sm">API 状态</span>
                        <span
                          className={cn(
                            "text-xs px-2 py-0.5 rounded-full",
                            envCheck.api_available
                              ? "bg-green-50 text-green-600 dark:bg-green-950/30"
                              : "bg-yellow-50 text-yellow-600 dark:bg-yellow-950/30",
                          )}
                        >
                          {envCheck.api_available ? "已连接" : "未连接"}
                        </span>
                      </div>
                    )}

                    {/* Provider select */}
                    {(() => {
                      const DOMESTIC_IDS = ["deepseek", "minimax", "qwen", "moonshot", "zhipu", "siliconflow", "yi"]
                      const INTL_IDS = ["openai", "anthropic", "gemini"]
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
                      const domestic = cloudProviders.filter((p) => DOMESTIC_IDS.includes(p.id))
                      const intl = cloudProviders.filter((p) => INTL_IDS.includes(p.id))
                      const custom = cloudProviders.filter((p) => !DOMESTIC_IDS.includes(p.id) && !INTL_IDS.includes(p.id))
                      const selectedTag = cloudProvider ? PROVIDER_TAGS[cloudProvider] : undefined
                      return (
                        <div>
                          <span className="text-sm block mb-1.5">提供商</span>
                          <div className="flex items-center gap-2">
                            <select
                              className="flex-1 border rounded px-2 py-1.5 text-sm bg-background"
                              value={cloudProvider}
                              onChange={(e) => handleProviderChange(e.target.value)}
                            >
                              <option value="">选择提供商...</option>
                              {domestic.length > 0 && (
                                <optgroup label="国产模型">
                                  {domestic.map((p) => (
                                    <option key={p.id} value={p.id}>{p.name}</option>
                                  ))}
                                </optgroup>
                              )}
                              {intl.length > 0 && (
                                <optgroup label="海外模型">
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
                      <span className="text-sm block mb-1.5">Base URL</span>
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
                          <span className="text-sm block mb-1.5">模型</span>
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
                              <option value="__custom__">自定义...</option>
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
                          {cloudValidating ? "验证中..." : "验证"}
                        </Button>
                      </div>
                      {cloudProviders.find((p) => p.id === cloudProvider)?.api_format === "anthropic" && (
                        <p className="text-xs mt-1 text-muted-foreground">
                          Claude API 使用独立鉴权格式，Key 将通过 x-api-key 头传递，而非 Bearer Token
                        </p>
                      )}
                      {cloudValidResult && (
                        <p
                          className={cn(
                            "text-xs mt-1",
                            cloudValidResult.valid ? "text-green-600" : "text-red-500",
                          )}
                        >
                          {cloudValidResult.valid ? "验证成功" : cloudValidResult.error}
                        </p>
                      )}
                    </div>

                    {/* Save cloud config */}
                    <div className="flex items-center gap-3 pt-2">
                      <Button
                        size="xs"
                        onClick={handleSaveCloud}
                        disabled={cloudSaving || !cloudProvider || !cloudBaseUrl || !cloudModel || !cloudApiKey}
                      >
                        {cloudSaving ? "保存中..." : "保存配置"}
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
                            {benchmarking ? "测试中..." : "性能测试"}
                          </Button>
                          {benchmarking && (
                            <span className="text-xs text-muted-foreground">正在调用模型，请稍候...</span>
                          )}
                        </div>
                        {benchmarkResult && (
                          <div className="mt-2 rounded-md border p-2.5 text-xs space-y-1">
                            <div className="flex flex-wrap gap-x-4 gap-y-0.5">
                              <span>响应时间: <strong>{(benchmarkResult.benchmark.elapsed_ms / 1000).toFixed(1)}秒</strong></span>
                              <span>速度: <strong>{benchmarkResult.benchmark.tokens_per_second} token/s</strong></span>
                              <span>预估单章: <strong>~{benchmarkResult.benchmark.estimated_chapter_time_s}秒</strong> <span className="text-muted-foreground font-normal">(约{benchmarkResult.benchmark.estimated_chapter_chars}字)</span></span>
                              <span>上下文窗口: <strong>{(benchmarkResult.context_window / 1024).toFixed(0)}K</strong></span>
                            </div>
                            <div className="flex items-center gap-2">
                              <span>分析质量:</span>
                              <strong className={cn(
                                benchmarkResult.quality.overall_score >= 80 ? "text-green-600" :
                                benchmarkResult.quality.overall_score >= 60 ? "text-yellow-600" : "text-red-500"
                              )}>
                                {benchmarkResult.quality.overall_score}分
                              </strong>
                              <span className="text-muted-foreground">
                                (实体识别 {benchmarkResult.quality.entity_recall}% | 关系识别 {benchmarkResult.quality.relation_recall}%)
                              </span>
                            </div>
                            {benchmarkResult.quality.notes.length > 0 && (
                              <p className="text-[10px] text-muted-foreground">
                                {benchmarkResult.quality.notes.join("，")}
                              </p>
                            )}
                            <p className="text-[10px] text-muted-foreground">
                              输入 {formatTokens(benchmarkResult.benchmark.input_tokens)} / 输出 {formatTokens(benchmarkResult.benchmark.output_tokens)} tokens
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
                              <span>历史记录 ({benchmarkHistory.length})</span>
                              <span>{showBenchmarkHistory ? "收起" : "展开"}</span>
                            </button>
                            {showBenchmarkHistory && (
                              <div className="mt-1.5 border rounded-md overflow-hidden">
                                <table className="w-full text-[11px]">
                                  <thead>
                                    <tr className="bg-muted/40 text-muted-foreground">
                                      <th className="text-left px-2 py-1 font-medium">时间</th>
                                      <th className="text-left px-2 py-1 font-medium">模型</th>
                                      <th className="text-right px-2 py-1 font-medium">速度</th>
                                      <th className="text-right px-2 py-1 font-medium">预估单章</th>
                                      <th className="text-right px-2 py-1 font-medium">质量</th>
                                      <th className="text-right px-2 py-1 font-medium"></th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {benchmarkHistory.map((rec) => (
                                      <tr key={rec.id} className="border-t hover:bg-muted/20">
                                        <td className="px-2 py-1 text-muted-foreground">{formatDateTime(rec.created_at)}</td>
                                        <td className="px-2 py-1 font-mono">{rec.model}</td>
                                        <td className="px-2 py-1 text-right font-mono">{rec.tokens_per_second} t/s</td>
                                        <td className="px-2 py-1 text-right font-mono">~{rec.estimated_chapter_time_s}秒</td>
                                        <td className={cn(
                                          "px-2 py-1 text-right font-mono",
                                          rec.quality_score != null && rec.quality_score >= 80 ? "text-green-600" :
                                          rec.quality_score != null && rec.quality_score >= 60 ? "text-yellow-600" :
                                          rec.quality_score != null ? "text-red-500" : "text-muted-foreground",
                                        )}>
                                          {rec.quality_score != null ? `${rec.quality_score}分` : "-"}
                                        </td>
                                        <td className="px-2 py-1 text-right">
                                          <button
                                            className="text-muted-foreground hover:text-red-500 transition-colors"
                                            onClick={() => handleDeleteBenchmarkRecord(rec.id)}
                                          >
                                            删除
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
                          {modeSwitching ? "切换中..." : "切换到此引擎"}
                        </Button>
                        {!cloudConfig?.has_api_key && (
                          <p className="text-[10px] text-muted-foreground mt-1">
                            请先保存 API 配置
                          </p>
                        )}
                        {cloudConfig?.has_api_key && (
                          <p className="text-[10px] text-muted-foreground mt-1">
                            切换后新的分析任务将使用此引擎
                          </p>
                        )}
                      </div>
                    )}
                  </>
                )}

                {/* Footer: refresh + restore */}
                <div className="border-t pt-3 mt-3 flex items-center gap-3">
                  <Button variant="outline" size="xs" onClick={refreshEnv}>
                    刷新状态
                  </Button>
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={handleRestoreDefaults}
                    disabled={restoring}
                  >
                    {restoring ? "恢复中..." : "恢复默认"}
                  </Button>
                </div>
              </div>
            </div>

            {/* Switch confirmation dialog */}
            {showSwitchDialog && (
              <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
                <div className="mx-4 w-full max-w-sm rounded-lg border bg-background p-5 shadow-lg">
                  <h3 className="text-sm font-medium mb-3">
                    {runningTaskCount > 0 ? "⚠ 切换 AI 引擎" : "切换 AI 引擎"}
                  </h3>

                  {runningTaskCount > 0 && (
                    <div className="mb-3 rounded-md border border-yellow-200 bg-yellow-50/60 px-3 py-2 text-xs dark:border-yellow-900 dark:bg-yellow-950/20">
                      <p className="font-medium text-yellow-700 dark:text-yellow-300">
                        当前有 {runningTaskCount} 个分析任务正在运行
                      </p>
                      <ul className="mt-1 space-y-0.5 text-yellow-600 dark:text-yellow-400">
                        <li>· 进行中的分析将继续使用原引擎完成</li>
                        <li>· 新启动的分析将使用新引擎</li>
                      </ul>
                    </div>
                  )}

                  <p className="text-sm text-muted-foreground mb-4">
                    确定从「{envCheck?.llm_provider === "openai" ? "云端 API" : "本地 Ollama"} · {envCheck?.llm_model || "unknown"}」
                    切换到「{viewTab === "openai" ? "云端 API" : "本地 Ollama"} · {viewTab === "openai" ? (cloudModel || cloudConfig?.model || "?") : (selectedOllamaModel || "qwen3:8b")}」？
                  </p>

                  <div className="flex justify-end gap-2">
                    <Button variant="outline" size="sm" onClick={() => setShowSwitchDialog(false)}>
                      取消
                    </Button>
                    <Button size="sm" onClick={handleConfirmSwitch}>
                      确认切换
                    </Button>
                  </div>
                </div>
              </div>
            )}
          </section>

          {/* Usage & Budget */}
          <section id="sec-usage" className="scroll-mt-12">
            <h2 className="text-base font-medium mb-4">使用统计</h2>
            <div className="border rounded-lg p-4 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-sm">月度预算</span>
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
                    保存
                  </Button>
                </div>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm">本月已用</span>
                <span className="text-sm font-mono">¥{monthlyUsed.toFixed(2)}</span>
              </div>
              {budgetAmount > 0 && (
                <div>
                  <div className="flex justify-between text-xs text-muted-foreground mb-1">
                    <span>预算使用</span>
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
                设为 0 可关闭预算告警。预算按自然月重置。
              </p>

              {/* Analysis Records */}
              <div className="border-t pt-3 mt-3">
                <span className="text-sm block mb-2">分析记录</span>
                {recordsLoading ? (
                  <p className="text-xs text-muted-foreground">加载中...</p>
                ) : analysisRecords.length === 0 ? (
                  <p className="text-xs text-muted-foreground">暂无分析记录</p>
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
                                第{rec.chapter_range[0]}~{rec.chapter_range[1]}章 · {rec.chapter_count}章
                                {" · "}{formatDateTime(rec.started_at)} ~ {formatDateTime(rec.completed_at)}
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
                              <p className="text-xs text-muted-foreground">加载明细...</p>
                            ) : costDetail ? (
                              <>
                                {/* Summary row */}
                                <div className="grid grid-cols-5 gap-1 text-[10px] text-muted-foreground font-medium border-b pb-1">
                                  <span>章节</span>
                                  <span className="text-right">输入Token</span>
                                  <span className="text-right">输出Token</span>
                                  <span className="text-right">费用</span>
                                  <span className="text-right">实体数</span>
                                </div>
                                <div className="max-h-48 overflow-auto space-y-0.5">
                                  {costDetail.chapters.map((ch) => (
                                    <div
                                      key={ch.chapter_id}
                                      className="grid grid-cols-5 gap-1 text-[10px] py-0.5"
                                    >
                                      <span className="text-muted-foreground">第{ch.chapter_id}章</span>
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
                                  <span>合计</span>
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
                                    模型: {costDetail.model || "本地"}
                                  </span>
                                  <Button
                                    variant="ghost"
                                    size="xs"
                                    onClick={() => window.open(costDetailCsvUrl(rec.novel_id), "_blank")}
                                  >
                                    导出 CSV
                                  </Button>
                                </div>
                              </>
                            ) : (
                              <p className="text-xs text-muted-foreground">无明细数据</p>
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
            <h2 className="text-base font-medium mb-4">阅读偏好</h2>
            <div className="border rounded-lg p-4 space-y-4">
              {/* Font size */}
              <div>
                <span className="text-sm block mb-2">字号</span>
                <div className="flex gap-2">
                  {(Object.keys(FONT_SIZE_MAP) as Array<keyof typeof FONT_SIZE_MAP>).map((size) => (
                    <Button
                      key={size}
                      variant={fontSize === size ? "default" : "outline"}
                      size="xs"
                      onClick={() => setFontSize(size)}
                    >
                      {{ small: "小", medium: "中", large: "大", xlarge: "特大" }[size]}
                    </Button>
                  ))}
                </div>
                <p className="text-[10px] text-muted-foreground mt-1">
                  当前: {FONT_SIZE_MAP[fontSize]}
                </p>
              </div>

              {/* Line height */}
              <div>
                <span className="text-sm block mb-2">行距</span>
                <div className="flex gap-2">
                  {(Object.keys(LINE_HEIGHT_MAP) as Array<keyof typeof LINE_HEIGHT_MAP>).map((lh) => (
                    <Button
                      key={lh}
                      variant={lineHeight === lh ? "default" : "outline"}
                      size="xs"
                      onClick={() => setLineHeight(lh)}
                    >
                      {{ compact: "紧凑", normal: "正常", loose: "宽松" }[lh]}
                    </Button>
                  ))}
                </div>
                <p className="text-[10px] text-muted-foreground mt-1">
                  当前: {{ compact: "1.6x", normal: "2.0x", loose: "2.6x" }[lineHeight]}
                </p>
              </div>
            </div>
          </section>

          {/* Data Management */}
          <section id="sec-data" className="scroll-mt-12">
            <h2 className="text-base font-medium mb-4">数据管理</h2>
            <div className="border rounded-lg p-4 space-y-4">
              {novels.length === 0 ? (
                <p className="text-sm text-muted-foreground">暂无导入的小说</p>
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
                          {novel.total_chapters} 章 · {(novel.total_words / 10000).toFixed(1)} 万字
                          · 分析进度 {Math.round(novel.analysis_progress * 100)}%
                        </span>
                      </div>
                      <div className="flex gap-1.5 flex-shrink-0">
                        <Button
                          variant="outline"
                          size="xs"
                          onClick={() => handleExport(novel.id)}
                        >
                          导出
                        </Button>
                        <Button
                          variant="outline"
                          size="xs"
                          onClick={() => navigate(`/analysis/${novel.id}`)}
                        >
                          分析
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Import section */}
              <div className="border-t pt-4">
                <div className="flex items-center gap-3">
                  <span className="text-sm">导入分析数据</span>
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
                    选择文件
                  </Button>
                </div>

                {/* Import preview */}
                {importPreview && (
                  <div className="mt-3 border rounded-lg p-3 space-y-2 bg-muted/30">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">{importPreview.title}</span>
                      {importPreview.existing_novel_id && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-50 text-yellow-600 dark:bg-yellow-950/30">
                          同名小说已存在
                        </span>
                      )}
                    </div>
                    {importPreview.author && (
                      <p className="text-xs text-muted-foreground">{importPreview.author}</p>
                    )}
                    <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                      <span>{importPreview.total_chapters} 章</span>
                      <span>{(importPreview.total_words / 10000).toFixed(1)} 万字</span>
                      <span>{importPreview.analyzed_chapters} 章已分析</span>
                      <span>{importPreview.facts_count} 条分析数据</span>
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
                            {importing ? "导入中..." : "创建新书"}
                          </Button>
                          <Button
                            variant="outline"
                            size="xs"
                            onClick={() => handleConfirmImport(true)}
                            disabled={importing}
                          >
                            覆盖已有
                          </Button>
                        </>
                      ) : (
                        <Button
                          size="xs"
                          onClick={() => handleConfirmImport(false)}
                          disabled={importing}
                        >
                          {importing ? "导入中..." : "确认导入"}
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="xs"
                        onClick={cancelImport}
                        disabled={importing}
                      >
                        取消
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
            <h2 className="text-base font-medium mb-4">全量备份</h2>
            <div className="border rounded-lg p-4 space-y-4">
              <div className="flex items-center gap-3">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    const a = document.createElement("a")
                    a.href = backupExportUrl()
                    a.click()
                  }}
                >
                  导出全部数据 (.zip)
                </Button>
                <span className="text-xs text-muted-foreground">
                  包含所有小说、分析结果、用户设置（不含 API Key）
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
                      setBackupError(err instanceof Error ? err.message : "预览失败")
                    }
                  }}
                />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => backupFileRef.current?.click()}
                >
                  恢复数据 (.zip)
                </Button>

                {backupPreview && backupFile && (
                  <div className="mt-3 p-3 bg-muted/50 rounded text-sm space-y-2">
                    <p>
                      备份时间: {backupPreview.exported_at ? formatDateTime(backupPreview.exported_at) : "-"}
                      {" · "}大小: {formatBytes(backupPreview.zip_size_bytes)}
                    </p>
                    <p>
                      共 {backupPreview.novel_count} 本小说
                      {backupPreview.conflict_count > 0 && (
                        <span className="text-amber-600 ml-2">
                          ({backupPreview.conflict_count} 本已存在)
                        </span>
                      )}
                    </p>
                    <ul className="text-xs space-y-0.5 max-h-32 overflow-y-auto">
                      {backupPreview.novels.map((n) => (
                        <li key={n.id} className="flex items-center gap-1.5">
                          <span>{n.title}</span>
                          <span className="text-muted-foreground">({n.total_chapters}章)</span>
                          {n.conflict && (
                            <span className="text-amber-600 text-[10px]">已存在</span>
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
                            setBackupError(err instanceof Error ? err.message : "导入失败")
                          } finally {
                            setBackupImporting(false)
                          }
                        }}
                      >
                        {backupImporting ? "导入中..." : "导入（跳过已存在）"}
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
                              setBackupError(err instanceof Error ? err.message : "导入失败")
                            } finally {
                              setBackupImporting(false)
                            }
                          }}
                        >
                          导入（覆盖已存在）
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
                        取消
                      </Button>
                    </div>
                  </div>
                )}

                {backupResult && (
                  <p className="mt-2 text-xs text-green-600">
                    导入完成: {backupResult.imported} 本新增
                    {backupResult.overwritten > 0 && `，${backupResult.overwritten} 本覆盖`}
                    {backupResult.skipped > 0 && `，${backupResult.skipped} 本跳过`}
                    {backupResult.errors.length > 0 && `，${backupResult.errors.length} 个错误`}
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
            <h2 className="text-base font-medium mb-4">使用统计与隐私</h2>
            <div className="border rounded-lg p-4 space-y-4">
              {/* Privacy toggle */}
              <div className="flex items-center justify-between">
                <div>
                  <span className="text-sm">使用统计</span>
                  <p className="text-[10px] text-muted-foreground">
                    匿名记录功能使用频率，数据仅本地存储
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
                <p className="text-sm text-muted-foreground">加载中...</p>
              ) : usageStats ? (
                <>
                  <div className="flex items-center justify-between">
                    <span className="text-sm">累计事件</span>
                    <span className="text-sm font-mono">{usageStats.total_events}</span>
                  </div>

                  {/* Event type frequency ranking */}
                  {usageStats.by_type.length > 0 && (
                    <div>
                      <span className="text-sm block mb-2">功能使用频率（近30天）</span>
                      <div className="space-y-1.5">
                        {usageStats.by_type.slice(0, 10).map((item) => {
                          const maxCount = usageStats.by_type[0]?.count || 1
                          return (
                            <div key={item.event_type} className="flex items-center gap-2">
                              <span className="text-xs text-muted-foreground w-32 shrink-0 truncate">
                                {item.event_type}
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
                      <span className="text-sm block mb-2">每日趋势</span>
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
                    <p className="text-sm text-muted-foreground">暂无使用数据</p>
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
                      清除使用数据
                    </Button>
                    <span className="text-[10px] text-muted-foreground ml-2">
                      所有统计数据仅存储在本地
                    </span>
                  </div>
                </>
              ) : (
                <p className="text-sm text-muted-foreground">无法加载统计数据</p>
              )}
            </div>
          </section>

          {/* Version info */}
          <section className="text-center text-[10px] text-muted-foreground pb-8">
            AI Reader v{__APP_VERSION__} · 本地运行 · 完全隐私
          </section>
        </div>
      </div>
    </div>
  )
}
