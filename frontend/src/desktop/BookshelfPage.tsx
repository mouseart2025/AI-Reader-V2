/**
 * DesktopBookshelfPage — 桌面版全屏书架首页
 * 当 sidecar 运行时从 REST API 加载小说列表
 * 支持 TXT 上传（通过后端 API）和 .air 文件导入（通过 Tauri IPC）
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { ensureSidecar } from "@/api/sidecarBridge"
import { fetchNovels, fetchActiveAnalyses } from "@/api/client"
import type { Novel } from "@/api/types"
import { DragDropOverlay } from "./DragDropOverlay"
import { SecurityGuide } from "./SecurityGuide"
import { UploadDialog } from "@/components/shared/UploadDialog"
import { WelcomeBanner } from "@/components/shared/WelcomeBanner"
import { HelpCircle, Upload, Settings, FileUp, BookOpen } from "lucide-react"
import { useI18n } from "@/i18n"

interface PreviewResult {
  title: string
  author: string | null
  total_chapters: number
  total_words: number
  analyzed_chapters: number
  has_precomputed: boolean
  format_version: number
  is_duplicate: boolean
  existing_slug: string | null
}

interface ImportResult {
  slug: string
  title: string
  total_chapters: number
}

export default function DesktopBookshelfPage() {
  const navigate = useNavigate()
  const { t } = useI18n()
  const [novels, setNovels] = useState<Novel[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeAnalysisMap, setActiveAnalysisMap] = useState<Map<string, "running" | "paused">>(new Map())
  const [sidecarReady, setSidecarReady] = useState(false)
  const [sidecarError, setSidecarError] = useState<string | null>(null)
  const [sidecarElapsed, setSidecarElapsed] = useState(0)
  const [showGuide, setShowGuide] = useState(false)
  const [importing, setImporting] = useState(false)
  const importingRef = useRef(false)
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [dragTxtFile, setDragTxtFile] = useState<File | null>(null)
  const [newVersion, setNewVersion] = useState<string | null>(null)

  // Auto-dismiss toast
  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 3000)
    return () => clearTimeout(timer)
  }, [toast])

  // Escape key to close SecurityGuide
  useEffect(() => {
    if (!showGuide) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setShowGuide(false)
    }
    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [showGuide])

  // Start sidecar on mount
  useEffect(() => {
    ensureSidecar()
      .then(() => setSidecarReady(true))
      .catch((err) => setSidecarError(err instanceof Error ? err.message : String(err)))
    const timer = setInterval(() => setSidecarElapsed((t) => t + 1), 1000)
    return () => clearInterval(timer)
  }, [])

  // Check for new version on GitHub (silent, non-blocking)
  useEffect(() => {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 5000)
    fetch("https://api.github.com/repos/mouseart2025/AI-Reader-V2/releases/latest", {
      signal: controller.signal,
      headers: { Accept: "application/vnd.github.v3+json" },
    })
      .then((r) => r.json())
      .then((data: { tag_name?: string }) => {
        clearTimeout(timer)
        const remote = data.tag_name?.replace(/^v/, "")
        if (remote && remote !== __APP_VERSION__) setNewVersion(remote)
      })
      .catch(() => { clearTimeout(timer) })
    return () => { controller.abort(); clearTimeout(timer) }
  }, [])

  const loadNovels = useCallback(async () => {
    if (!sidecarReady) return
    setLoading(true)
    setError(null)
    try {
      await Promise.all([
        fetchNovels().then((res) => setNovels(res.novels)),
        fetchActiveAnalyses()
          .then((active) => setActiveAnalysisMap(new Map(active.items.map((a) => [a.novel_id, a.status]))))
          .catch(() => {}),
      ])
    } catch (err) {
      setError(err instanceof Error ? err.message : t("desktop.loadNovelsFailed"))
    } finally {
      setLoading(false)
    }
  }, [sidecarReady, t])

  useEffect(() => {
    loadNovels()
  }, [loadNovels])

  const sampleNovels = useMemo(
    () => novels.filter((n) => n.is_sample),
    [novels]
  )

  /** TXT upload via UploadDialog */
  const handleUploadClick = useCallback(() => {
    setUploadOpen(true)
  }, [])

  /** .air file import via Tauri IPC */
  const handleAirImport = useCallback(async (filePath: string) => {
    if (importingRef.current) return
    importingRef.current = true
    setImporting(true)

    try {
      const { invoke } = await import("@tauri-apps/api/core")

      const preview = await invoke<PreviewResult>("preview_air_file", { path: filePath })

      if (!preview.has_precomputed) {
        setToast({ message: t("desktop.airOutdated"), type: "error" })
        return
      }

      let overwrite = false
      if (preview.is_duplicate) {
        const { confirm } = await import("@tauri-apps/plugin-dialog")
        const confirmed = await confirm(
          t("desktop.overwriteExistingMessage", { title: preview.title }),
          { title: t("desktop.overwriteExistingTitle"), kind: "warning" }
        )
        if (!confirmed) return
        overwrite = true
      }

      await invoke<ImportResult>("import_air_file", { path: filePath, overwrite })

      // Also import into SQLite via sidecar REST API so the novel appears in the bookshelf
      try {
        const jsonStr = await invoke<string>("load_file_absolute", { path: filePath })
        const blob = new Blob([jsonStr], { type: "application/json" })
        const file = new File([blob], "import.json")
        const { confirmDataImport } = await import("@/api/client")
        await confirmDataImport(file, overwrite)
      } catch (dbErr) {
        console.warn("SQLite import fallback failed:", dbErr)
      }

      await loadNovels()
      setToast({ message: t("desktop.importSuccess", { title: preview.title }), type: "success" })
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setToast({ message: msg, type: "error" })
    } finally {
      importingRef.current = false
      setImporting(false)
    }
  }, [loadNovels, t])

  /** Delete novel via REST API */
  const handleDelete = useCallback(async (novelId: string, title: string) => {
    try {
      const { confirm } = await import("@tauri-apps/plugin-dialog")
      const confirmed = await confirm(
        t("desktop.deleteConfirmMessage", { title }),
        { title: t("desktop.deleteNovelTitle"), kind: "warning" }
      )
      if (!confirmed) return

      const { deleteNovel } = await import("@/api/client")
      await deleteNovel(novelId)
      await loadNovels()
      setToast({ message: t("desktop.deletedSuccess", { title }), type: "success" })
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setToast({ message: msg, type: "error" })
    }
  }, [loadNovels, t])

  /** .air import button click */
  const handleImportClick = useCallback(async () => {
    try {
      const { open } = await import("@tauri-apps/plugin-dialog")
      const path = await open({
        title: t("desktop.selectAirFile"),
        filters: [{ name: t("desktop.airAnalysisData"), extensions: ["air"] }],
        multiple: false,
      })
      if (path) {
        await handleAirImport(path as string)
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setToast({ message: msg, type: "error" })
    }
  }, [handleAirImport, t])

  /** HTML5 drag-and-drop .txt/.md files → auto-open UploadDialog */
  const handleNativeDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    if (uploadOpen) {
      setToast({ message: t("desktop.processingPreviousFile"), type: "error" })
      return
    }
    const files = Array.from(e.dataTransfer.files)
    // Filter: only .txt/.md, skip .air (handled by DragDropOverlay)
    const txtFiles = files.filter((f) => {
      const ext = f.name.split(".").pop()?.toLowerCase()
      return ext === "txt" || ext === "md"
    })
    if (txtFiles.length === 0) return
    if (txtFiles.length > 1) {
      setToast({ message: t("desktop.singleNovelOnly"), type: "error" })
    }
    setDragTxtFile(txtFiles[0])
    setUploadOpen(true)
  }, [uploadOpen, t])

  // Listen for file association: .air file opened via double-click
  useEffect(() => {
    let unlisten: (() => void) | undefined

    async function setup() {
      const { listen } = await import("@tauri-apps/api/event")
      unlisten = await listen<string>("novel:file-open", (event) => {
        const filePath = event.payload
        if (filePath && filePath.endsWith(".air")) {
          handleAirImport(filePath)
        }
      })
    }

    setup().catch(() => {})
    return () => { unlisten?.() }
  }, [handleAirImport])

  // Sidecar loading screen
  if (!sidecarReady) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-background text-foreground">
        {sidecarError ? (
          <div className="text-center">
            <p className="text-lg font-semibold text-red-400">{t("desktop.backendStartFailed")}</p>
            <p className="mt-2 text-sm text-muted-foreground">{sidecarError}</p>
            <button
              onClick={() => window.location.reload()}
              className="mt-4 rounded-md bg-blue-500 px-4 py-2 text-sm font-medium text-white hover:bg-blue-600 transition"
            >
              {t("common.retry")}
            </button>
          </div>
        ) : (
          <div className="text-center">
            <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-4 border-blue-500 border-t-transparent" />
            <p className="text-sm text-muted-foreground">{t("desktop.startingAnalysisEngine")}</p>
            {sidecarElapsed > 5 && (
              <p className="mt-2 text-xs text-muted-foreground/60">
                {t("desktop.firstLaunchHint", { seconds: sidecarElapsed })}
              </p>
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <div
      className="min-h-screen bg-background px-6 py-8 text-foreground"
      onDragOver={(e) => e.preventDefault()}
      onDrop={handleNativeDrop}
    >
      {/* Header */}
      <div className="mx-auto mb-8 flex max-w-5xl items-start justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">AI Reader</h1>
            <span className="text-[10px] text-muted-foreground/50 tabular-nums self-end mb-0.5">v{__APP_VERSION__}</span>
            {newVersion && (
              <a
                href={`https://github.com/mouseart2025/AI-Reader-V2/releases/tag/v${newVersion}`}
                target="_blank"
                rel="noopener"
                className="self-end mb-0.5 rounded-full bg-blue-500/20 px-2 py-0.5 text-[10px] text-blue-400 hover:bg-blue-500/30 transition"
              >
                {t("desktop.newVersionAvailable", { version: newVersion })}
              </a>
            )}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{t("desktop.productTagline")}</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleUploadClick}
            className="flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-blue-500"
          >
            <FileUp className="size-4" />
            {t("bookshelf.uploadNovel")}
          </button>
          <button
            onClick={handleImportClick}
            disabled={importing}
            className="flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm font-medium text-foreground transition hover:bg-muted disabled:opacity-50"
          >
            <Upload className="size-4" />
            {importing ? t("desktop.importing") : t("desktop.importAir")}
          </button>
          <button
            onClick={() => navigate("/settings")}
            className="text-muted-foreground hover:text-foreground transition"
          >
            <Settings className="size-5" />
          </button>
          <a
            href="https://ai-reader.cc/docs/"
            target="_blank"
            rel="noopener"
            className="text-muted-foreground hover:text-foreground transition"
            title={t("desktop.docs")}
          >
            <BookOpen className="size-4" />
          </a>
          <button
            onClick={() => setShowGuide(true)}
            className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition"
          >
            <HelpCircle className="size-4" />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="mx-auto max-w-5xl">
        {/* Welcome Banner for first-time users */}
        {!loading && sampleNovels.length > 0 && (
          <WelcomeBanner sampleNovels={sampleNovels} />
        )}

        {loading && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className="h-36 animate-pulse rounded-lg border border-border bg-card"
              />
            ))}
          </div>
        )}

        {!loading && error && (
          <div className="flex flex-col items-center justify-center py-20">
            <p className="mb-4 text-sm text-red-400">{error}</p>
            <button
              onClick={() => loadNovels()}
              className="rounded-md bg-blue-500 px-4 py-2 text-sm font-medium text-white hover:bg-blue-600 transition"
            >
              {t("common.retry")}
            </button>
          </div>
        )}

        {!loading && !error && novels.length === 0 && (
          <div className="flex flex-col items-center justify-center py-20">
            <p className="mb-2 text-lg font-medium text-muted-foreground">{t("desktop.noNovels")}</p>
            <p className="text-sm text-muted-foreground">{t("desktop.noNovelsDescription")}</p>
          </div>
        )}

        {!loading && !error && novels.length > 0 && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {novels.map((novel) => (
              <button
                key={novel.id}
                onClick={() => navigate(`/novel/${novel.id}/reading`)}
                className="group relative rounded-lg border border-border bg-card p-4 text-left transition hover:border-blue-500/50 hover:shadow-lg"
              >
                {activeAnalysisMap.get(String(novel.id)) === "running" && (
                  <div className="absolute top-2 right-2 flex items-center gap-1.5 rounded-full bg-green-500/20 px-2 py-0.5">
                    <span className="inline-block size-1.5 animate-pulse rounded-full bg-green-400" />
                    <span className="text-[10px] font-medium text-green-400">{t("analysis.running")}</span>
                  </div>
                )}
                {activeAnalysisMap.get(String(novel.id)) === "paused" && (
                  <div className="absolute top-2 right-2 flex items-center gap-1.5 rounded-full bg-yellow-500/20 px-2 py-0.5">
                    <span className="inline-block size-1.5 rounded-full bg-yellow-400" />
                    <span className="text-[10px] font-medium text-yellow-400">{t("analysis.paused")}</span>
                  </div>
                )}
                <h3 className="font-semibold text-foreground group-hover:text-blue-400 transition">
                  {novel.title}
                </h3>
                {novel.author && (
                  <p className="mt-1 text-xs text-muted-foreground">{novel.author}</p>
                )}
                <p className="mt-2 text-xs text-muted-foreground">
                  {t("common.chapterCount", { count: novel.total_chapters })}
                </p>
                <div className="mt-3 flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">
                    {novel.analysis_progress >= 1 && !novel.failed_count ? t("analysis.completed") :
                     novel.analysis_progress >= 1 && novel.failed_count > 0
                       ? <span className="text-yellow-400">{t("analysis.completedWithFailures", { count: novel.failed_count })}</span> :
                     novel.analysis_progress > 0
                       ? <>
                           {t("analysis.progressPercent", { percent: Math.round(novel.analysis_progress * 100) })}
                           {novel.failed_count > 0 && (
                             <span className="text-yellow-400 ml-1">{t("analysis.failedChapters", { count: novel.failed_count })}</span>
                           )}
                         </> :
                     t("analysis.notAnalyzed")}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      handleDelete(String(novel.id), novel.title)
                    }}
                    className="text-xs text-muted-foreground hover:text-red-400 transition opacity-0 group-hover:opacity-100"
                  >
                    {t("common.delete")}
                  </button>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* DragDropOverlay for .air files */}
      <DragDropOverlay onFileDrop={handleAirImport} />

      {/* Toast notification */}
      {toast && (
        <div
          className={`fixed bottom-6 right-6 z-50 rounded-lg px-4 py-3 text-sm font-medium shadow-lg transition-all ${
            toast.type === "success"
              ? "bg-green-600 text-white"
              : "bg-red-600 text-white"
          }`}
        >
          {toast.message}
        </div>
      )}

      {/* Upload Dialog (chapter split preview) */}
      <UploadDialog
        open={uploadOpen}
        onOpenChange={(open) => {
          setUploadOpen(open)
          if (!open) setDragTxtFile(null)
        }}
        onImported={loadNovels}
        initialFile={dragTxtFile ?? undefined}
      />

      {/* SecurityGuide Dialog */}
      {showGuide && (
        <>
          <div className="fixed inset-0 z-40 bg-black/50" onClick={() => setShowGuide(false)} />
          <div className="fixed inset-0 z-50 flex items-center justify-center" role="dialog" aria-modal="true">
            <div className="w-full max-w-lg rounded-xl border border-border bg-card shadow-2xl">
              <SecurityGuide onDone={() => setShowGuide(false)} />
            </div>
          </div>
        </>
      )}
    </div>
  )
}
