/**
 * DesktopBookshelfPage — 桌面版全屏书架首页
 * 当 sidecar 运行时从 REST API 加载小说列表
 * 支持 TXT 上传（通过后端 API）和 .air 文件导入（通过 Tauri IPC）
 */

import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { ensureSidecar } from "@/api/sidecarBridge"
import { fetchNovels, fetchActiveAnalyses, uploadNovelWithProgress, confirmImport } from "@/api/client"
import type { Novel, UploadPreviewResponse } from "@/api/types"
import { DragDropOverlay } from "./DragDropOverlay"
import { SecurityGuide } from "./SecurityGuide"
import { HelpCircle, Upload, Settings, FileUp, BookOpen } from "lucide-react"

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
  const [novels, setNovels] = useState<Novel[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeAnalysisMap, setActiveAnalysisMap] = useState<Map<string, "running" | "paused">>(new Map())
  const [sidecarReady, setSidecarReady] = useState(false)
  const [sidecarError, setSidecarError] = useState<string | null>(null)
  const [showGuide, setShowGuide] = useState(false)
  const [importing, setImporting] = useState(false)
  const importingRef = useRef(false)
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null)
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

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
      setError(err instanceof Error ? err.message : "加载小说列表失败")
    } finally {
      setLoading(false)
    }
  }, [sidecarReady])

  useEffect(() => {
    loadNovels()
  }, [loadNovels])

  /** TXT upload via REST API */
  const handleUploadClick = useCallback(() => {
    fileInputRef.current?.click()
  }, [])

  const handleFileSelect = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    // Reset input so same file can be selected again
    e.target.value = ""

    try {
      setUploadProgress(0)
      const preview: UploadPreviewResponse = await uploadNovelWithProgress(file, (pct) => {
        setUploadProgress(pct)
      })
      setUploadProgress(null)

      // Auto-confirm with default settings
      await confirmImport({
        file_hash: preview.file_hash,
        title: preview.title,
        author: preview.author,
      })

      await loadNovels()
      setToast({ message: `「${preview.title}」上传成功`, type: "success" })
    } catch (err) {
      setUploadProgress(null)
      const msg = err instanceof Error ? err.message : String(err)
      setToast({ message: msg, type: "error" })
    }
  }, [loadNovels])

  /** .air file import via Tauri IPC */
  const handleAirImport = useCallback(async (filePath: string) => {
    if (importingRef.current) return
    importingRef.current = true
    setImporting(true)

    try {
      const { invoke } = await import("@tauri-apps/api/core")

      const preview = await invoke<PreviewResult>("preview_air_file", { path: filePath })

      if (!preview.has_precomputed) {
        setToast({ message: "此 .air 文件版本过旧，请使用最新版 AI Reader 重新导出", type: "error" })
        return
      }

      let overwrite = false
      if (preview.is_duplicate) {
        const { confirm } = await import("@tauri-apps/plugin-dialog")
        const confirmed = await confirm(
          `「${preview.title}」已存在，导入将覆盖现有数据`,
          { title: "覆盖已有数据？", kind: "warning" }
        )
        if (!confirmed) return
        overwrite = true
      }

      await invoke<ImportResult>("import_air_file", { path: filePath, overwrite })
      await loadNovels()
      setToast({ message: `「${preview.title}」导入成功`, type: "success" })
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setToast({ message: msg, type: "error" })
    } finally {
      importingRef.current = false
      setImporting(false)
    }
  }, [loadNovels])

  /** Delete novel via REST API */
  const handleDelete = useCallback(async (novelId: string, title: string) => {
    try {
      const { confirm } = await import("@tauri-apps/plugin-dialog")
      const confirmed = await confirm(
        `确定删除「${title}」？删除后数据将无法恢复`,
        { title: "删除小说", kind: "warning" }
      )
      if (!confirmed) return

      const { deleteNovel } = await import("@/api/client")
      await deleteNovel(novelId)
      await loadNovels()
      setToast({ message: `「${title}」已删除`, type: "success" })
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setToast({ message: msg, type: "error" })
    }
  }, [loadNovels])

  /** .air import button click */
  const handleImportClick = useCallback(async () => {
    try {
      const { open } = await import("@tauri-apps/plugin-dialog")
      const path = await open({
        title: "选择 .air 分析数据文件",
        filters: [{ name: "AIR 分析数据", extensions: ["air"] }],
        multiple: false,
      })
      if (path) {
        await handleAirImport(path as string)
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setToast({ message: msg, type: "error" })
    }
  }, [handleAirImport])

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
      <div className="dark flex min-h-screen flex-col items-center justify-center bg-background text-foreground">
        {sidecarError ? (
          <div className="text-center">
            <p className="text-lg font-semibold text-red-400">后端启动失败</p>
            <p className="mt-2 text-sm text-muted-foreground">{sidecarError}</p>
            <button
              onClick={() => window.location.reload()}
              className="mt-4 rounded-md bg-blue-500 px-4 py-2 text-sm font-medium text-white hover:bg-blue-600 transition"
            >
              重试
            </button>
          </div>
        ) : (
          <div className="text-center">
            <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-4 border-blue-500 border-t-transparent" />
            <p className="text-sm text-muted-foreground">正在启动分析引擎...</p>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="dark min-h-screen bg-background px-6 py-8 text-foreground">
      {/* Hidden file input for TXT upload */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".txt"
        className="hidden"
        onChange={handleFileSelect}
      />

      {/* Header */}
      <div className="mx-auto mb-8 flex max-w-5xl items-start justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">AI Reader</h1>
            <span className="text-[10px] text-muted-foreground/50 tabular-nums self-end mb-0.5">v{__APP_VERSION__}</span>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">中文小说智能分析平台</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleUploadClick}
            disabled={uploadProgress !== null}
            className="flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-blue-500 disabled:opacity-50"
          >
            <FileUp className="size-4" />
            {uploadProgress !== null ? `上传 ${uploadProgress}%` : "上传小说"}
          </button>
          <button
            onClick={handleImportClick}
            disabled={importing}
            className="flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm font-medium text-foreground transition hover:bg-muted disabled:opacity-50"
          >
            <Upload className="size-4" />
            {importing ? "导入中..." : "导入 .air"}
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
            title="使用文档"
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
              重试
            </button>
          </div>
        )}

        {!loading && !error && novels.length === 0 && (
          <div className="flex flex-col items-center justify-center py-20">
            <p className="mb-2 text-lg font-medium text-muted-foreground">暂无小说</p>
            <p className="text-sm text-muted-foreground">点击上方「上传小说」上传 TXT 文件开始分析</p>
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
                    <span className="text-[10px] font-medium text-green-400">分析中</span>
                  </div>
                )}
                {activeAnalysisMap.get(String(novel.id)) === "paused" && (
                  <div className="absolute top-2 right-2 flex items-center gap-1.5 rounded-full bg-yellow-500/20 px-2 py-0.5">
                    <span className="inline-block size-1.5 rounded-full bg-yellow-400" />
                    <span className="text-[10px] font-medium text-yellow-400">已暂停</span>
                  </div>
                )}
                <h3 className="font-semibold text-foreground group-hover:text-blue-400 transition">
                  {novel.title}
                </h3>
                {novel.author && (
                  <p className="mt-1 text-xs text-muted-foreground">{novel.author}</p>
                )}
                <p className="mt-2 text-xs text-muted-foreground">
                  {novel.total_chapters} 章
                </p>
                <div className="mt-3 flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">
                    {novel.analysis_progress >= 1 && !novel.failed_count ? "分析完成" :
                     novel.analysis_progress >= 1 && novel.failed_count > 0
                       ? <span className="text-yellow-400">分析完成（{novel.failed_count} 章失败）</span> :
                     novel.analysis_progress > 0
                       ? <>
                           分析 {Math.round(novel.analysis_progress * 100)}%
                           {novel.failed_count > 0 && (
                             <span className="text-yellow-400 ml-1">({novel.failed_count} 章失败)</span>
                           )}
                         </> :
                     "未分析"}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      handleDelete(String(novel.id), novel.title)
                    }}
                    className="text-xs text-muted-foreground hover:text-red-400 transition opacity-0 group-hover:opacity-100"
                  >
                    删除
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
