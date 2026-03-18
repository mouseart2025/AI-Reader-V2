import { useCallback, useEffect, useRef, useState } from "react"
import {
  AlertTriangle,
  ArrowRightLeft,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Eye,
  FileText,
  Info,
  Loader2,
  RefreshCw,
  Sparkles,
  Upload,
} from "lucide-react"
import {
  uploadNovelWithProgress,
  confirmImport,
  confirmDataImport,
  previewImport as previewDataImport,
  fetchNovel,
  deleteNovel,
  fetchSplitModes,
  reSplitChapters,
  cleanAndResplit,
  fetchRawText,
} from "@/api/client"
import type { ImportPreview, Novel, UploadPreviewResponse } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { TextPreviewPanel, type TextPreviewPanelHandle } from "./TextPreviewPanel"
import { RegexTemplateSelector } from "./RegexTemplateSelector"

const ALLOWED_EXTENSIONS = [".txt", ".md", ".air", ".json"]
const DATA_IMPORT_EXTENSIONS = [".air", ".json"]

const MODE_LABELS: Record<string, string> = {
  chapter_zh: "第X章 / 番外",
  section_zh: "第X回/节/卷/幕/场/部",
  numbered: "数字编号 (1. / 001)",
  chapter_en: "英文章节 (Chapter / Part)",
  markdown: "Markdown 标题",
  separator: "分隔线 (--- / ===)",
  heuristic_title: "启发式标题检测",
  fixed_size: "按字数切分 (~8000字/段)",
}

const GENRE_LABELS: Record<string, string> = {
  novel: "长篇小说",
  short_collection: "短篇集",
  essay: "散文",
  poetry: "诗集",
}

const CATEGORY_LABELS: Record<string, string> = {
  url: "链接",
  promo: "推广",
  template: "站点模板",
  decoration: "装饰线",
  repeated: "重复尾注",
}

/** Diagnosis tags that trigger automatic expansion to split-pane mode */
const EXPAND_TAGS = new Set(["FALLBACK_USED", "NO_HEADING_MATCH", "SINGLE_HUGE_CHAPTER"])

/** Friendly user-facing messages for each diagnosis tag (fallback when backend doesn't provide user_message) */
const DIAGNOSIS_USER_MESSAGES: Record<string, string> = {
  NO_HEADING_MATCH: "未能自动识别章节格式，请尝试选择切分模式或手动标记",
  FALLBACK_USED: "已使用备选方式切分，建议确认章节划分是否正确",
  SINGLE_HUGE_CHAPTER: "整本书被识别为一个章节，可能需要选择其他切分方式",
  HEADING_TOO_SPARSE: "检测到的章节较少，可能遗漏了部分章节",
  HEADING_TOO_DENSE: "检测到的章节过多，可能有内容被误识别为标题",
  MODE_MISMATCH: "检测到两种可能的切分方式，请确认",
}

function DiagnosisBanner({
  diagnosis,
  reSplitting,
  onFixedSizeSplit,
}: {
  diagnosis: import("@/api/types").SplitDiagnosis
  reSplitting: boolean
  onFixedSizeSplit: () => void
}) {
  const [showDetail, setShowDetail] = useState(false)

  const userMessage =
    diagnosis.user_message || DIAGNOSIS_USER_MESSAGES[diagnosis.tag] || diagnosis.message
  const technicalDetail = diagnosis.technical_detail || diagnosis.message
  // Only show expand toggle when technical detail differs from user message
  const hasTechnicalDetail = technicalDetail !== userMessage

  // Auto-optimized: show green success banner
  if (diagnosis.auto_optimized) {
    return (
      <div className="flex items-start gap-2 rounded-md bg-green-50 px-3 py-2 text-sm text-green-800 dark:bg-green-950 dark:text-green-200">
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
        <div className="flex-1 space-y-1">
          <p>{userMessage}</p>
          {hasTechnicalDetail && (
            <button
              type="button"
              className="text-xs underline opacity-60 hover:opacity-100"
              onClick={() => setShowDetail(!showDetail)}
            >
              {showDetail ? "收起详情" : "技术详情"}
            </button>
          )}
          {showDetail && hasTechnicalDetail && (
            <div className="rounded bg-black/5 px-2 py-1.5 font-mono text-xs dark:bg-white/5">
              <p>{technicalDetail}</p>
              {diagnosis.original_mode && (
                <p className="mt-1 opacity-75">原始模式: {diagnosis.original_mode}</p>
              )}
            </div>
          )}
        </div>
      </div>
    )
  }

  const bgClass =
    diagnosis.tag === "FALLBACK_USED"
      ? "bg-blue-50 text-blue-800 dark:bg-blue-950 dark:text-blue-200"
      : diagnosis.tag === "SINGLE_HUGE_CHAPTER" || diagnosis.tag === "NO_HEADING_MATCH"
        ? "bg-orange-50 text-orange-800 dark:bg-orange-950 dark:text-orange-200"
        : "bg-yellow-50 text-yellow-800 dark:bg-yellow-950 dark:text-yellow-200"

  return (
    <div className={`flex items-start gap-2 rounded-md px-3 py-2 text-sm ${bgClass}`}>
      <Info className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="flex-1 space-y-1">
        <p>{userMessage}</p>
        {hasTechnicalDetail && (
          <button
            type="button"
            className="text-xs underline opacity-60 hover:opacity-100"
            onClick={() => setShowDetail(!showDetail)}
          >
            {showDetail ? "收起详情" : "技术详情"}
          </button>
        )}
        {showDetail && hasTechnicalDetail && (
          <div className="rounded bg-black/5 px-2 py-1.5 font-mono text-xs dark:bg-white/5">
            <p>{technicalDetail}</p>
            {diagnosis.suggestion && (
              <p className="mt-1 opacity-75">{diagnosis.suggestion}</p>
            )}
          </div>
        )}
        {!showDetail && !hasTechnicalDetail && diagnosis.suggestion && (
          <p className="text-xs opacity-75">{diagnosis.suggestion}</p>
        )}
        {(diagnosis.tag === "SINGLE_HUGE_CHAPTER" || diagnosis.tag === "NO_HEADING_MATCH") && (
          <Button
            variant="outline"
            size="xs"
            className="mt-1"
            onClick={onFixedSizeSplit}
            disabled={reSplitting}
          >
            {reSplitting ? (
              <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
            ) : (
              <Sparkles className="mr-1.5 h-3 w-3" />
            )}
            一键按字数切分
          </Button>
        )}
      </div>
    </div>
  )
}

type Stage = "select" | "uploading" | "preview" | "duplicate" | "confirming" | "import-preview" | "import-confirming"

function formatWordCount(count: number): string {
  if (count >= 10000) return `${(count / 10000).toFixed(1)}万字`
  return `${count}字`
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr + "Z")
  return d.toLocaleString("zh-CN")
}

export function UploadDialog({
  open,
  onOpenChange,
  onImported,
  initialFile,
  /** External import preview data (from BookshelfPage .air import) */
  externalImportPreview,
  externalImportFile,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onImported: () => void
  initialFile?: File
  externalImportPreview?: ImportPreview | null
  externalImportFile?: File | null
}) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const textPreviewRef = useRef<TextPreviewPanelHandle>(null)
  const [stage, setStage] = useState<Stage>("select")
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<UploadPreviewResponse | null>(null)
  const [existingNovel, setExistingNovel] = useState<Novel | null>(null)
  const [title, setTitle] = useState("")
  const [author, setAuthor] = useState("")

  // Chapter exclusion state (chapter_nums to exclude)
  const [excludedNums, setExcludedNums] = useState<Set<number>>(new Set())

  // Split adjustment state
  const [splitOpen, setSplitOpen] = useState(false)
  const [splitModes, setSplitModes] = useState<string[]>([])
  const [selectedMode, setSelectedMode] = useState<string>("auto")
  const [customRegex, setCustomRegex] = useState("")
  const [reSplitting, setReSplitting] = useState(false)

  // Hygiene state
  const [hygieneOpen, setHygieneOpen] = useState(false)
  const [cleaning, setCleaning] = useState(false)

  // Upload progress
  const [uploadProgress, setUploadProgress] = useState(0)

  // Data import state (.air / .json)
  const [dataImportPreview, setDataImportPreview] = useState<ImportPreview | null>(null)
  const [dataImportFile, setDataImportFile] = useState<File | null>(null)

  // Expanded mode (split-pane with text preview)
  const [isExpanded, setIsExpanded] = useState(false)
  const [rawText, setRawText] = useState<string | null>(null)
  const [rawTextLoading, setRawTextLoading] = useState(false)
  const [splitPoints, setSplitPoints] = useState<number[]>([])

  // Confirmation dialog for overwriting manual marks
  const [showOverwriteConfirm, setShowOverwriteConfirm] = useState(false)
  const pendingRegexRef = useRef<string | null>(null)

  // Simulated progress bar
  const [simulatedProgress, setSimulatedProgress] = useState(0)

  const reset = useCallback(() => {
    setStage("select")
    setError(null)
    setPreview(null)
    setExistingNovel(null)
    setTitle("")
    setAuthor("")
    setExcludedNums(new Set())
    setSplitOpen(false)
    setSplitModes([])
    setSelectedMode("auto")
    setCustomRegex("")
    setReSplitting(false)
    setHygieneOpen(false)
    setCleaning(false)
    setUploadProgress(0)
    setDataImportPreview(null)
    setDataImportFile(null)
    setIsExpanded(false)
    setRawText(null)
    setRawTextLoading(false)
    setSplitPoints([])
    setSimulatedProgress(0)
    if (fileInputRef.current) fileInputRef.current.value = ""
  }, [])

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) reset()
    onOpenChange(nextOpen)
  }

  // Auto-trigger upload when initialFile is provided (drag-to-upload)
  useEffect(() => {
    if (open && initialFile && stage === "select") {
      handleFileSelect(initialFile)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialFile])

  // Handle external import preview (from BookshelfPage)
  useEffect(() => {
    if (open && externalImportPreview && externalImportFile && stage === "select") {
      setDataImportPreview(externalImportPreview)
      setDataImportFile(externalImportFile)
      setStage("import-preview")
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, externalImportPreview, externalImportFile])

  // Fetch raw text when expanding
  const loadRawText = useCallback(async (fileHash: string) => {
    if (rawText !== null) return // already loaded
    setRawTextLoading(true)
    try {
      const { text } = await fetchRawText(fileHash)
      setRawText(text)
    } catch {
      setError("无法加载原文预览")
    } finally {
      setRawTextLoading(false)
    }
  }, [rawText])

  const handleExpand = useCallback(() => {
    setIsExpanded(true)
    if (preview?.file_hash) {
      loadRawText(preview.file_hash)
    }
  }, [preview, loadRawText])

  // Auto-expand when diagnosis indicates failure
  useEffect(() => {
    if (stage === "preview" && preview?.diagnosis && EXPAND_TAGS.has(preview.diagnosis.tag)) {
      handleExpand()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, preview?.diagnosis?.tag])

  const handleFileSelect = async (file: File) => {
    setError(null)

    const ext = file.name.includes(".")
      ? "." + file.name.split(".").pop()!.toLowerCase()
      : ""
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      setError("仅支持 .txt / .md / .air / .json 格式")
      return
    }

    // Data import flow (.air / .json)
    if (DATA_IMPORT_EXTENSIONS.includes(ext)) {
      if (ext === ".json") {
        try {
          const text = await file.slice(0, 200).text()
          if (!text.includes("format_version")) {
            setError("该 .json 文件不是 AI Reader 导出的分析数据")
            return
          }
        } catch {
          setError("无法读取文件")
          return
        }
      }

      setStage("uploading")
      setUploadProgress(50)
      try {
        const previewData = await previewDataImport(file)
        setDataImportPreview(previewData)
        setDataImportFile(file)
        setStage("import-preview")
      } catch (err) {
        setError(err instanceof Error ? err.message : "预览失败")
        setStage("select")
      }
      return
    }

    // Novel upload flow (.txt / .md)
    setStage("uploading")
    setUploadProgress(0)
    try {
      const data = await uploadNovelWithProgress(file, setUploadProgress)
      setPreview(data)
      setTitle(data.title)
      setAuthor(data.author ?? "")
      setExcludedNums(new Set(
        data.chapters.filter((ch) => ch.is_suspect).map((ch) => ch.chapter_num),
      ))

      if (data.duplicate_novel_id) {
        try {
          const existing = await fetchNovel(data.duplicate_novel_id)
          setExistingNovel(existing)
          setStage("duplicate")
          return
        } catch {
          // If fetching existing fails, just show normal preview
        }
      }

      setStage("preview")
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败")
      setStage("select")
    }
  }

  const handleConfirm = async () => {
    if (!preview) return

    // If user has manual split points, apply them first
    if (splitPoints.length > 0) {
      setReSplitting(true)
      try {
        const data = await reSplitChapters({
          file_hash: preview.file_hash,
          split_points: splitPoints,
        })
        setPreview(data)
      } catch (err) {
        const msg = err instanceof Error ? err.message : "应用手动标记失败"
        setError(msg)
        setReSplitting(false)
        return
      }
      setReSplitting(false)
    }

    setStage("confirming")
    setSimulatedProgress(0)
    try {
      await confirmImport({
        file_hash: preview.file_hash,
        title: title.trim() || preview.title,
        author: author.trim() || null,
        excluded_chapters: excludedNums.size > 0 ? [...excludedNums] : undefined,
      })
      setSimulatedProgress(100)
      setTimeout(() => {
        onImported()
        handleOpenChange(false)
      }, 300)
    } catch (err) {
      const msg = err instanceof Error ? err.message : "导入失败"
      if (msg.includes("过期") || msg.includes("expired")) {
        setStage("select")
        setError("上传数据已过期（超过30分钟），请重新选择文件")
      } else {
        setError(msg)
        setStage("preview")
      }
    }
  }

  const handleOverwrite = async () => {
    if (!preview || !existingNovel) return
    setStage("confirming")
    setSimulatedProgress(0)
    try {
      await deleteNovel(existingNovel.id)
      await confirmImport({
        file_hash: preview.file_hash,
        title: title.trim() || preview.title,
        author: author.trim() || null,
        excluded_chapters: excludedNums.size > 0 ? [...excludedNums] : undefined,
      })
      setSimulatedProgress(100)
      setTimeout(() => {
        onImported()
        handleOpenChange(false)
      }, 300)
    } catch (err) {
      const msg = err instanceof Error ? err.message : "覆盖导入失败"
      if (msg.includes("过期") || msg.includes("expired")) {
        setStage("select")
        setError("上传数据已过期（超过30分钟），请重新选择文件")
      } else {
        setError(msg)
        setStage("duplicate")
      }
    }
  }

  const handleImportAsNew = () => {
    setExistingNovel(null)
    setStage("preview")
  }

  const handleDataImportConfirm = async () => {
    if (!dataImportFile || !dataImportPreview) return
    setStage("import-confirming")
    setSimulatedProgress(0)
    try {
      const overwrite = !!dataImportPreview.existing_novel_id
      await confirmDataImport(dataImportFile, overwrite)
      setSimulatedProgress(100)
      setTimeout(() => {
        onImported()
        handleOpenChange(false)
      }, 300)
    } catch (err) {
      setError(err instanceof Error ? err.message : "导入失败")
      setStage("import-preview")
    }
  }

  const handleToggleSplit = async () => {
    if (!splitOpen && splitModes.length === 0) {
      try {
        const data = await fetchSplitModes()
        setSplitModes(data.modes)
      } catch {
        // Ignore
      }
    }
    setSplitOpen(!splitOpen)
  }

  const doReSplit = async (mode?: string | null, regex?: string | null) => {
    if (!preview) return
    setReSplitting(true)
    setError(null)
    try {
      const data = await reSplitChapters({
        file_hash: preview.file_hash,
        mode: mode ?? undefined,
        custom_regex: regex ?? undefined,
      })
      setPreview(data)
      setExcludedNums(new Set(
        data.chapters.filter((ch) => ch.is_suspect).map((ch) => ch.chapter_num),
      ))
      setSplitPoints([]) // Clear manual marks after re-split
    } catch (err) {
      const msg = err instanceof Error ? err.message : "重新切分失败"
      if (msg.includes("过期") || msg.includes("expired")) {
        setStage("select")
        setError("上传数据已过期（超过30分钟），请重新选择文件")
      } else {
        setError(msg)
      }
    } finally {
      setReSplitting(false)
    }
  }

  const handleReSplit = async () => {
    const mode = selectedMode === "auto" ? null : selectedMode === "custom" ? null : selectedMode
    const regex = selectedMode === "custom" ? customRegex.trim() || null : null
    await doReSplit(mode, regex)
  }

  const handleRegexTemplateApply = (regex: string) => {
    if (splitPoints.length > 0) {
      pendingRegexRef.current = regex
      setShowOverwriteConfirm(true)
      return
    }
    doReSplit(null, regex)
  }

  const handleOverwriteConfirmed = () => {
    setShowOverwriteConfirm(false)
    const regex = pendingRegexRef.current
    pendingRegexRef.current = null
    if (regex) doReSplit(null, regex)
  }

  const handleFixedSizeSplit = () => doReSplit("fixed_size", null)

  const handleCleanAndResplit = async () => {
    if (!preview) return
    setCleaning(true)
    setError(null)
    try {
      const data = await cleanAndResplit({
        file_hash: preview.file_hash,
        clean_mode: "conservative",
      })
      setPreview(data)
      setExcludedNums(new Set(
        data.chapters.filter((ch) => ch.is_suspect).map((ch) => ch.chapter_num),
      ))
    } catch (err) {
      const msg = err instanceof Error ? err.message : "清理失败"
      if (msg.includes("过期") || msg.includes("expired")) {
        setStage("select")
        setError("上传数据已过期（超过30分钟），请重新选择文件")
      } else {
        setError(msg)
      }
    } finally {
      setCleaning(false)
    }
  }

  // Simulated progress animation for confirming stages
  useEffect(() => {
    if (stage !== "confirming" && stage !== "import-confirming") return
    if (simulatedProgress >= 90) return
    const targets = [30, 50, 70, 85, 90]
    const delays = [200, 400, 800, 1500, 3000]
    let idx = targets.findIndex((t) => t > simulatedProgress)
    if (idx === -1) return
    const timer = setTimeout(() => {
      setSimulatedProgress(targets[idx])
    }, delays[idx])
    return () => clearTimeout(timer)
  }, [stage, simulatedProgress])

  // Build chapter boundaries for TextPreviewPanel using backend-computed offsets
  const chapterBoundaries = preview
    ? preview.chapters.map((ch) => ({
        charOffset: ch.start_offset ?? 0,
        title: ch.title,
      }))
    : []

  const isWorking = stage === "confirming" || stage === "import-confirming"
  const diagnosis = preview?.diagnosis
  const hygieneReport = preview?.hygiene_report

  // Determine dialog width class
  const dialogWidthClass = isExpanded ? "sm:max-w-7xl" : "sm:max-w-2xl"

  return (
    <>
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className={`${dialogWidthClass} transition-all duration-300`}>
          <DialogHeader>
            <DialogTitle>
              {stage === "select" || stage === "uploading"
                ? "上传小说"
                : stage === "duplicate"
                  ? "检测到重复文件"
                  : stage === "import-preview" || stage === "import-confirming"
                    ? "导入分析数据"
                    : "确认导入"}
            </DialogTitle>
            <DialogDescription>
              {stage === "select" || stage === "uploading"
                ? "选择 .txt / .md 小说文件，或 .air 分析数据包"
                : stage === "duplicate"
                  ? "该文件已导入过，请选择处理方式"
                  : stage === "import-preview" || stage === "import-confirming"
                    ? "确认导入分析数据，将包含完整的章节、实体和分析结果"
                    : "检查章节切分结果，编辑书名和作者后确认导入"}
            </DialogDescription>
          </DialogHeader>

          {/* Error message */}
          {error && (
            <div className="flex items-center gap-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              {error}
            </div>
          )}

          {/* Stage: File Select */}
          {stage === "select" && (
            <div
              className="border-muted-foreground/25 hover:border-primary/50 flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed py-12 transition-colors"
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault()
                e.stopPropagation()
              }}
              onDrop={(e) => {
                e.preventDefault()
                e.stopPropagation()
                const file = e.dataTransfer.files[0]
                if (file) handleFileSelect(file)
              }}
            >
              <Upload className="text-muted-foreground mb-3 h-10 w-10" />
              <p className="text-muted-foreground text-sm">
                点击或拖拽文件到此处
              </p>
              <p className="text-muted-foreground/60 mt-1 text-xs">
                支持 .txt / .md 小说文件，或 .air 分析数据包
              </p>
              <input
                ref={fileInputRef}
                type="file"
                accept=".txt,.md,.air,.json"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) handleFileSelect(file)
                }}
              />
            </div>
          )}

          {/* Stage: Uploading */}
          {stage === "uploading" && (
            <div className="flex flex-col items-center justify-center gap-3 py-12">
              <Loader2 className="text-primary h-10 w-10 animate-spin" />
              <p className="text-muted-foreground text-sm">
                {uploadProgress < 100 ? "正在上传文件..." : "正在解析文件..."}
              </p>
              <div className="w-48 space-y-1">
                <Progress value={uploadProgress} className="h-2" />
                <p className="text-center text-xs text-muted-foreground">{uploadProgress}%</p>
              </div>
            </div>
          )}

          {/* Stage: Duplicate Comparison */}
          {stage === "duplicate" && preview && existingNovel && (
            <div className="space-y-4">
              <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
                <div className="rounded-lg border p-4">
                  <p className="text-muted-foreground mb-2 text-xs font-medium uppercase">已有版本</p>
                  <p className="font-semibold">{existingNovel.title}</p>
                  {existingNovel.author && (
                    <p className="text-muted-foreground text-sm">{existingNovel.author}</p>
                  )}
                  <div className="text-muted-foreground mt-3 space-y-1 text-sm">
                    <p>{existingNovel.total_chapters} 章</p>
                    <p>{formatWordCount(existingNovel.total_words)}</p>
                    <p>导入于 {formatDate(existingNovel.created_at)}</p>
                  </div>
                </div>
                <ArrowRightLeft className="text-muted-foreground h-5 w-5" />
                <div className="border-primary/30 rounded-lg border p-4">
                  <p className="text-muted-foreground mb-2 text-xs font-medium uppercase">新上传版本</p>
                  <p className="font-semibold">{preview.title}</p>
                  {preview.author && (
                    <p className="text-muted-foreground text-sm">{preview.author}</p>
                  )}
                  <div className="text-muted-foreground mt-3 space-y-1 text-sm">
                    <p>{preview.total_chapters} 章</p>
                    <p>{formatWordCount(preview.total_words)}</p>
                    <p>刚刚上传</p>
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2 rounded-md bg-yellow-50 px-3 py-2 text-sm text-yellow-800 dark:bg-yellow-950 dark:text-yellow-200">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                选择"覆盖已有版本"将删除旧版本的全部分析数据
              </div>
            </div>
          )}

          {/* Stage: Preview (default or expanded) */}
          {(stage === "preview" || stage === "confirming") && preview && (
            <div className={isExpanded ? "grid grid-cols-[400px_1fr] gap-4" : ""} style={isExpanded ? { height: "calc(80vh - 10rem)" } : undefined}>
              {/* Left column: metadata + controls + chapter list */}
              <div className={`space-y-4 overflow-y-auto pr-1 ${isExpanded ? "" : "max-h-[calc(80vh-10rem)]"}`}>
                {/* Editable metadata */}
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="novel-title">书名</Label>
                    <Input
                      id="novel-title"
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      disabled={isWorking}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="novel-author">作者</Label>
                    <Input
                      id="novel-author"
                      value={author}
                      placeholder="未知"
                      onChange={(e) => setAuthor(e.target.value)}
                      disabled={isWorking}
                    />
                  </div>
                </div>

                {/* Summary */}
                <div className="text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                  <span className="flex items-center gap-1">
                    <FileText className="h-4 w-4" />
                    共 {preview.total_chapters} 章
                  </span>
                  <span>{formatWordCount(preview.total_words)}</span>
                  {preview.matched_mode && (
                    <span className="text-xs opacity-60">
                      模式: {MODE_LABELS[preview.matched_mode] ?? preview.matched_mode}
                    </span>
                  )}
                  {diagnosis?.detected_genre && diagnosis.detected_genre !== "unknown" && (
                    <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs font-medium">
                      {GENRE_LABELS[diagnosis.detected_genre] ?? diagnosis.detected_genre}
                    </span>
                  )}
                  {splitPoints.length > 0 && (
                    <span className="text-xs text-red-500">
                      + {splitPoints.length} 个手动标记
                    </span>
                  )}
                </div>

                {/* Diagnosis banner — two-layer display */}
                {diagnosis && diagnosis.tag !== "OK" && (
                  <DiagnosisBanner
                    diagnosis={diagnosis}
                    reSplitting={reSplitting}
                    onFixedSizeSplit={handleFixedSizeSplit}
                  />
                )}

                {/* Hygiene report banner */}
                {hygieneReport && hygieneReport.total_suspect_lines > 0 && (
                  <div className="rounded-md border border-orange-200 bg-orange-50 dark:border-orange-900 dark:bg-orange-950">
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 px-3 py-2 text-sm text-orange-800 dark:text-orange-200"
                      onClick={() => setHygieneOpen(!hygieneOpen)}
                    >
                      {hygieneOpen ? (
                        <ChevronDown className="h-4 w-4 shrink-0" />
                      ) : (
                        <ChevronRight className="h-4 w-4 shrink-0" />
                      )}
                      <AlertTriangle className="h-4 w-4 shrink-0" />
                      <span className="flex-1 text-left">
                        检测到 {hygieneReport.total_suspect_lines} 行疑似非正文内容
                        {Object.entries(hygieneReport.by_category).length > 0 && (
                          <span className="ml-1 opacity-75">
                            （{Object.entries(hygieneReport.by_category)
                              .map(([cat, count]) => `${CATEGORY_LABELS[cat] ?? cat} ${count}`)
                              .join(" / ")}）
                          </span>
                        )}
                      </span>
                      <Button
                        variant="outline"
                        size="xs"
                        className="shrink-0"
                        onClick={(e) => {
                          e.stopPropagation()
                          handleCleanAndResplit()
                        }}
                        disabled={cleaning}
                      >
                        {cleaning ? (
                          <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                        ) : (
                          <Sparkles className="mr-1.5 h-3 w-3" />
                        )}
                        清理并重新切分
                      </Button>
                    </button>
                    {hygieneOpen && hygieneReport.samples.length > 0 && (
                      <div className="border-t border-orange-200 px-3 py-2 dark:border-orange-900">
                        <div className="max-h-32 space-y-1 overflow-y-auto">
                          {hygieneReport.samples.map((s, i) => (
                            <div key={i} className="flex min-w-0 items-baseline gap-2 text-xs text-orange-700 dark:text-orange-300">
                              <span className="shrink-0 tabular-nums opacity-60">L{s.line_num}</span>
                              <span className="shrink-0 rounded bg-orange-200/60 px-1 py-0.5 text-[10px] font-medium dark:bg-orange-800/40">
                                {CATEGORY_LABELS[s.category] ?? s.category}
                              </span>
                              <span className="min-w-0 truncate">{s.content}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* Warnings */}
                {preview.warnings.length > 0 && (
                  <div className="space-y-1">
                    {preview.warnings.map((w, i) => (
                      <div
                        key={i}
                        className="flex items-center gap-2 rounded-md bg-yellow-50 px-3 py-2 text-sm text-yellow-800 dark:bg-yellow-950 dark:text-yellow-200"
                      >
                        <AlertTriangle className="h-4 w-4 shrink-0" />
                        {w}
                      </div>
                    ))}
                  </div>
                )}

                {/* Split adjustment — expanded mode uses RegexTemplateSelector, default uses legacy controls */}
                {isExpanded ? (
                  <div className="rounded-md border px-3 py-3">
                    <RegexTemplateSelector
                      onApply={handleRegexTemplateApply}
                      disabled={reSplitting || isWorking}
                    />
                    {reSplitting && (
                      <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
                        <Loader2 className="h-3 w-3 animate-spin" />
                        正在重新切分...
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="rounded-md border">
                    <button
                      type="button"
                      className="text-muted-foreground hover:text-foreground flex w-full items-center gap-2 px-3 py-2 text-sm transition-colors"
                      onClick={handleToggleSplit}
                      disabled={isWorking}
                    >
                      {splitOpen ? (
                        <ChevronDown className="h-4 w-4" />
                      ) : (
                        <ChevronRight className="h-4 w-4" />
                      )}
                      调整切分方式
                    </button>
                    {splitOpen && (
                      <div className="space-y-3 border-t px-3 pb-3 pt-2">
                        <div className="flex items-end gap-3">
                          <div className="flex-1 space-y-1.5">
                            <Label className="text-xs">切分模式</Label>
                            <Select
                              value={selectedMode}
                              onValueChange={setSelectedMode}
                              disabled={reSplitting}
                            >
                              <SelectTrigger className="h-8 text-sm">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="auto">自动检测</SelectItem>
                                {splitModes.map((m) => (
                                  <SelectItem key={m} value={m}>
                                    {MODE_LABELS[m] ?? m}
                                  </SelectItem>
                                ))}
                                <SelectItem value="custom">自定义正则</SelectItem>
                              </SelectContent>
                            </Select>
                          </div>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={handleReSplit}
                            disabled={reSplitting || (selectedMode === "custom" && !customRegex.trim())}
                          >
                            {reSplitting ? (
                              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                            )}
                            重新切分
                          </Button>
                        </div>
                        {selectedMode === "custom" && (
                          <div className="space-y-1.5">
                            <Label className="text-xs">自定义正则表达式</Label>
                            <Input
                              value={customRegex}
                              onChange={(e) => setCustomRegex(e.target.value)}
                              placeholder="例如: ^第[\d]+章\s*(.*)"
                              className="h-8 font-mono text-sm"
                              disabled={reSplitting}
                            />
                            <p className="text-muted-foreground text-xs">
                              正则将以 MULTILINE 模式逐行匹配，匹配位置作为章节分割点
                            </p>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {/* Exclusion summary */}
                {excludedNums.size > 0 && (
                  <div className="flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
                    <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                    <span>{excludedNums.size} 个章节将被排除（不参与分析）</span>
                    <button
                      type="button"
                      className="ml-auto text-amber-600 underline hover:no-underline dark:text-amber-400"
                      onClick={() => setExcludedNums(new Set())}
                    >
                      全部取消
                    </button>
                  </div>
                )}

                {/* Chapter list */}
                <div className={`overflow-y-auto overflow-x-hidden rounded-md border ${isExpanded ? "flex-1" : "max-h-64"}`}>
                  <table className="w-full table-fixed text-sm">
                    <colgroup>
                      <col className="w-8" />
                      <col className="w-10" />
                      <col />
                      <col className="w-16" />
                    </colgroup>
                    <thead className="bg-muted/50 sticky top-0">
                      <tr>
                        <th className="px-2 py-2 text-center font-medium">
                          <input
                            type="checkbox"
                            className="h-3.5 w-3.5 rounded"
                            title="全选/取消排除"
                            checked={excludedNums.size > 0 && excludedNums.size === preview.chapters.length}
                            ref={(el) => {
                              if (el) el.indeterminate = excludedNums.size > 0 && excludedNums.size < preview.chapters.length
                            }}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setExcludedNums(new Set(preview.chapters.map((ch) => ch.chapter_num)))
                              } else {
                                setExcludedNums(new Set())
                              }
                            }}
                          />
                        </th>
                        <th className="px-3 py-2 text-left font-medium">#</th>
                        <th className="px-3 py-2 text-left font-medium">章节标题</th>
                        <th className="px-3 py-2 text-right font-medium">字数</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.chapters.map((ch, idx) => {
                        const isExcluded = excludedNums.has(ch.chapter_num)
                        return (
                          <tr
                            key={ch.chapter_num}
                            className={`cursor-pointer border-t transition-colors hover:bg-muted/30 ${isExcluded ? "bg-amber-50/60 dark:bg-amber-950/30" : ""}`}
                            onClick={() => {
                              if (isExpanded && textPreviewRef.current) {
                                textPreviewRef.current.scrollToChapter(idx)
                              }
                            }}
                          >
                            <td className="px-2 py-1.5 text-center">
                              <input
                                type="checkbox"
                                className="h-3.5 w-3.5 rounded"
                                checked={isExcluded}
                                onClick={(e) => e.stopPropagation()}
                                onChange={() => {
                                  setExcludedNums((prev) => {
                                    const next = new Set(prev)
                                    if (next.has(ch.chapter_num)) {
                                      next.delete(ch.chapter_num)
                                    } else {
                                      next.add(ch.chapter_num)
                                    }
                                    return next
                                  })
                                }}
                              />
                            </td>
                            <td className={`px-3 py-1.5 ${isExcluded ? "text-muted-foreground line-through" : "text-muted-foreground"}`}>
                              {ch.chapter_num}
                            </td>
                            <td className={`px-3 py-1.5 ${isExcluded ? "text-muted-foreground line-through" : ""}`}>
                              <div className="truncate" title={ch.title}>
                                {ch.title}
                                {ch.is_suspect && !isExcluded && (
                                  <span className="ml-1.5 text-xs text-amber-600 dark:text-amber-400">
                                    (疑似非正文)
                                  </span>
                                )}
                              </div>
                              {ch.content_preview && (
                                <div className="truncate text-xs text-muted-foreground/70" title={ch.content_preview}>
                                  {ch.content_preview}
                                </div>
                              )}
                            </td>
                            <td className={`px-3 py-1.5 text-right ${isExcluded ? "text-muted-foreground line-through" : "text-muted-foreground"}`}>
                              {formatWordCount(ch.word_count)}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Right column: Text Preview Panel (only in expanded mode) */}
              {isExpanded && (
                <div className="overflow-hidden rounded-md border">
                  {rawTextLoading ? (
                    <div className="flex h-full items-center justify-center">
                      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                      <span className="ml-2 text-sm text-muted-foreground">加载原文...</span>
                    </div>
                  ) : rawText ? (
                    <TextPreviewPanel
                      ref={textPreviewRef}
                      rawText={rawText}
                      chapterBoundaries={chapterBoundaries}
                      splitPoints={splitPoints}
                      onSplitPointsChange={setSplitPoints}
                    />
                  ) : (
                    <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                      无法加载原文
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Stage: Data Import Preview (.air / .json) */}
          {(stage === "import-preview" || stage === "import-confirming") && dataImportPreview && (
            <div className="space-y-4">
              <div className="space-y-1">
                <h3 className="text-lg font-semibold">{dataImportPreview.title}</h3>
                {dataImportPreview.author && (
                  <p className="text-sm text-muted-foreground">
                    作者：{dataImportPreview.author}
                  </p>
                )}
              </div>

              <div className="rounded-md border bg-muted/30 p-4 space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">章节</span>
                  <span>
                    {dataImportPreview.total_chapters} 章
                    <span className="text-muted-foreground ml-1">
                      · 已分析 {dataImportPreview.analyzed_chapters} 章
                    </span>
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">字数</span>
                  <span>{formatWordCount(dataImportPreview.total_words)}</span>
                </div>
                {(dataImportPreview.entity_dict_count ?? 0) > 0 && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">实体词典</span>
                    <span>{dataImportPreview.entity_dict_count} 个</span>
                  </div>
                )}
                <div className="flex justify-between">
                  <span className="text-muted-foreground">格式版本</span>
                  <span>v{dataImportPreview.format_version}</span>
                </div>
                {dataImportPreview.llm_models && dataImportPreview.llm_models.length > 0 && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">分析模型</span>
                    <span>{dataImportPreview.llm_models.join(", ")}</span>
                  </div>
                )}
                {(dataImportPreview.bookmarks_count ?? 0) > 0 && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">书签</span>
                    <span>{dataImportPreview.bookmarks_count} 个</span>
                  </div>
                )}
                {(dataImportPreview.map_overrides_count ?? 0) > 0 && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">地图自定义</span>
                    <span>{dataImportPreview.map_overrides_count} 处</span>
                  </div>
                )}
                {(dataImportPreview.conversations_count ?? 0) > 0 && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">问答对话</span>
                    <span>{dataImportPreview.conversations_count} 个</span>
                  </div>
                )}
                <div className="mt-2 flex justify-between border-t pt-2">
                  <span className="text-muted-foreground">数据大小</span>
                  <span>{(dataImportPreview.data_size_bytes / 1024 / 1024).toFixed(1)} MB</span>
                </div>
              </div>

              {dataImportPreview.existing_novel_id && (
                <div className="flex items-center gap-2 rounded-md bg-yellow-50 px-3 py-2 text-sm text-yellow-800 dark:bg-yellow-950 dark:text-yellow-200">
                  <AlertTriangle className="h-4 w-4 shrink-0" />
                  已存在同名小说，导入将覆盖现有数据
                </div>
              )}
            </div>
          )}

          {/* Footer: Data Import actions */}
          {(stage === "import-preview" || stage === "import-confirming") && (
            <DialogFooter>
              {stage === "import-confirming" && (
                <div className="mr-auto w-32">
                  <Progress value={simulatedProgress} className="h-2" />
                </div>
              )}
              <Button
                variant="outline"
                onClick={() => reset()}
                disabled={stage === "import-confirming"}
              >
                取消
              </Button>
              <Button
                onClick={handleDataImportConfirm}
                disabled={stage === "import-confirming"}
              >
                {stage === "import-confirming" ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    导入中...
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="mr-2 h-4 w-4" />
                    确认导入
                  </>
                )}
              </Button>
            </DialogFooter>
          )}

          {/* Footer: Duplicate actions */}
          {stage === "duplicate" && (
            <DialogFooter className="sm:justify-between">
              <Button
                variant="outline"
                onClick={() => handleOpenChange(false)}
              >
                取消
              </Button>
              <div className="flex gap-2">
                <Button variant="outline" onClick={handleImportAsNew}>
                  作为新书导入
                </Button>
                <Button variant="destructive" onClick={handleOverwrite}>
                  覆盖已有版本
                </Button>
              </div>
            </DialogFooter>
          )}

          {/* Footer: Preview actions */}
          {(stage === "preview" || stage === "confirming") && (
            <DialogFooter>
              {stage === "confirming" && (
                <div className="mr-auto w-32">
                  <Progress value={simulatedProgress} className="h-2" />
                </div>
              )}
              <Button
                variant="outline"
                onClick={() => reset()}
                disabled={isWorking}
              >
                重新选择
              </Button>
              {!isExpanded && (
                <Button
                  variant="outline"
                  onClick={handleExpand}
                  disabled={isWorking}
                >
                  <Eye className="mr-1.5 h-4 w-4" />
                  查看原文
                </Button>
              )}
              <Button onClick={handleConfirm} disabled={isWorking || reSplitting}>
                {isWorking ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    导入中...
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="mr-2 h-4 w-4" />
                    确认导入
                  </>
                )}
              </Button>
            </DialogFooter>
          )}
        </DialogContent>
      </Dialog>

      {/* Overwrite manual marks confirmation */}
      <AlertDialog open={showOverwriteConfirm} onOpenChange={setShowOverwriteConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>覆盖手动标记？</AlertDialogTitle>
            <AlertDialogDescription>
              应用正则模板将覆盖当前 {splitPoints.length} 个手动标记，是否继续？
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={handleOverwriteConfirmed}>
              继续
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
