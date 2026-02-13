import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { checkEnvironment, fetchNovels, exportNovelUrl, previewImport, confirmDataImport } from "@/api/client"
import type { EnvironmentCheck, Novel, ImportPreview } from "@/api/types"
import { useReadingSettingsStore, FONT_SIZE_MAP, LINE_HEIGHT_MAP } from "@/stores/readingSettingsStore"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
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

    fetchNovels().then((data) => setNovels(data.novels))
  }, [])

  const refreshEnv = () => {
    setEnvLoading(true)
    checkEnvironment()
      .then(setEnvCheck)
      .finally(() => setEnvLoading(false))
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

      <div className="flex-1 overflow-auto">
        <div className="max-w-2xl mx-auto p-6 space-y-8">
          {/* LLM Configuration */}
          <section>
            <h2 className="text-base font-medium mb-4">LLM 配置</h2>
            <div className="border rounded-lg p-4 space-y-3">
              {envLoading ? (
                <p className="text-sm text-muted-foreground">检测中...</p>
              ) : envCheck ? (
                <>
                  <div className="flex items-center justify-between">
                    <span className="text-sm">模式</span>
                    <span
                      className={cn(
                        "text-xs px-2 py-0.5 rounded-full",
                        envCheck.llm_provider === "openai"
                          ? "bg-blue-50 text-blue-600 dark:bg-blue-950/30"
                          : "bg-gray-50 text-gray-600 dark:bg-gray-950/30",
                      )}
                    >
                      {envCheck.llm_provider === "openai" ? "云端" : "本地"}
                    </span>
                  </div>

                  <div className="flex items-center justify-between">
                    <span className="text-sm">当前模型</span>
                    <span className="text-xs text-muted-foreground font-mono">
                      {envCheck.llm_model}
                    </span>
                  </div>

                  {envCheck.llm_provider === "openai" ? (
                    <>
                      <div className="flex items-center justify-between">
                        <span className="text-sm">API 地址</span>
                        <span className="text-xs text-muted-foreground font-mono">
                          {envCheck.llm_base_url}
                        </span>
                      </div>
                      <div className="flex items-center justify-between">
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
                    </>
                  ) : (
                    <>
                      <div className="flex items-center justify-between">
                        <span className="text-sm">Ollama 状态</span>
                        <span
                          className={cn(
                            "text-xs px-2 py-0.5 rounded-full",
                            envCheck.ollama_running
                              ? "bg-green-50 text-green-600 dark:bg-green-950/30"
                              : "bg-red-50 text-red-600 dark:bg-red-950/30",
                          )}
                        >
                          {envCheck.ollama_running ? "运行中" : "未运行"}
                        </span>
                      </div>

                      <div className="flex items-center justify-between">
                        <span className="text-sm">API 地址</span>
                        <span className="text-xs text-muted-foreground font-mono">
                          {envCheck.ollama_url}
                        </span>
                      </div>

                      <div className="flex items-center justify-between">
                        <span className="text-sm">所需模型</span>
                        <span
                          className={cn(
                            "text-xs px-2 py-0.5 rounded-full",
                            envCheck.model_available
                              ? "bg-green-50 text-green-600 dark:bg-green-950/30"
                              : "bg-yellow-50 text-yellow-600 dark:bg-yellow-950/30",
                          )}
                        >
                          {envCheck.required_model}{" "}
                          {envCheck.model_available ? "已安装" : "未安装"}
                        </span>
                      </div>

                      {(envCheck.available_models?.length ?? 0) > 0 && (
                        <div>
                          <span className="text-sm block mb-1.5">可用模型</span>
                          <div className="flex flex-wrap gap-1.5">
                            {envCheck.available_models!.map((m) => (
                              <span
                                key={m}
                                className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground"
                              >
                                {m}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </>
                  )}

                  <div className="pt-2">
                    <Button variant="outline" size="xs" onClick={refreshEnv}>
                      刷新状态
                    </Button>
                  </div>
                </>
              ) : (
                <p className="text-sm text-red-600">无法获取 LLM 状态</p>
              )}
            </div>
          </section>

          {/* Reading Preferences */}
          <section>
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
                  当前: {LINE_HEIGHT_MAP[lineHeight]}
                </p>
              </div>
            </div>
          </section>

          {/* Data Management */}
          <section>
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

          {/* Version info */}
          <section className="text-center text-[10px] text-muted-foreground pb-8">
            AI Reader v{__APP_VERSION__} · 本地运行 · 完全隐私
          </section>
        </div>
      </div>
    </div>
  )
}
