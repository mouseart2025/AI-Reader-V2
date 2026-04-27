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
  inferPattern,
  cleanAndResplit,
  fetchRawText,
} from "@/api/client"
import type { ImportPreview, Novel, UploadPreviewResponse } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import { useI18n, type TranslationKey } from "@/i18n"
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
import type { SourceLanguage } from "@/api/types"

const ALLOWED_EXTENSIONS = [".txt", ".md", ".air", ".json"]
const DATA_IMPORT_EXTENSIONS = [".air", ".json"]

const MODE_LABEL_KEYS: Record<string, TranslationKey> = {
  chapter_zh: "shared.upload.mode.chapterZh",
  section_zh: "shared.upload.mode.sectionZh",
  chapter_vi: "shared.upload.mode.chapterVi",
  numbered: "shared.upload.mode.numbered",
  chapter_en: "shared.upload.mode.chapterEn",
  markdown: "shared.upload.mode.markdown",
  separator: "shared.upload.mode.separator",
  heuristic_title: "shared.upload.mode.heuristicTitle",
  fixed_size: "shared.upload.mode.fixedSize",
}

const GENRE_LABEL_KEYS: Record<string, TranslationKey> = {
  novel: "shared.upload.genre.novel",
  short_collection: "shared.upload.genre.shortCollection",
  essay: "shared.upload.genre.essay",
  poetry: "shared.upload.genre.poetry",
}

const CATEGORY_LABEL_KEYS: Record<string, TranslationKey> = {
  url: "shared.upload.category.url",
  promo: "shared.upload.category.promo",
  template: "shared.upload.category.template",
  decoration: "shared.upload.category.decoration",
  repeated: "shared.upload.category.repeated",
}

const SOURCE_LANGUAGE_LABEL_KEYS: Record<SourceLanguage, TranslationKey> = {
  auto: "shared.upload.sourceLanguage.auto",
  "zh-CN": "shared.upload.sourceLanguage.zhCN",
  vi: "shared.upload.sourceLanguage.vi",
  en: "shared.upload.sourceLanguage.en",
}

/** Diagnosis tags that trigger automatic expansion to split-pane mode */
const EXPAND_TAGS = new Set(["FALLBACK_USED", "NO_HEADING_MATCH", "SINGLE_HUGE_CHAPTER"])
const EXPIRED_ERROR_MARKERS = new Set(["过期", "expired"])

/** Friendly user-facing messages for each diagnosis tag (fallback when backend doesn't provide user_message) */
const DIAGNOSIS_USER_MESSAGE_KEYS: Record<string, TranslationKey> = {
  NO_HEADING_MATCH: "shared.upload.diagnosis.noHeadingMatch",
  FALLBACK_USED: "shared.upload.diagnosis.fallbackUsed",
  SINGLE_HUGE_CHAPTER: "shared.upload.diagnosis.singleHugeChapter",
  HEADING_TOO_SPARSE: "shared.upload.diagnosis.headingTooSparse",
  HEADING_TOO_DENSE: "shared.upload.diagnosis.headingTooDense",
  MODE_MISMATCH: "shared.upload.diagnosis.modeMismatch",
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
  const { t } = useI18n()
  const [showDetail, setShowDetail] = useState(false)

  const userMessage =
    diagnosis.user_message ||
    (DIAGNOSIS_USER_MESSAGE_KEYS[diagnosis.tag]
      ? t(DIAGNOSIS_USER_MESSAGE_KEYS[diagnosis.tag])
      : diagnosis.message)
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
              {showDetail ? t("shared.upload.hideDetails") : t("shared.upload.technicalDetails")}
            </button>
          )}
          {showDetail && hasTechnicalDetail && (
            <div className="rounded bg-black/5 px-2 py-1.5 font-mono text-xs dark:bg-white/5">
              <p>{technicalDetail}</p>
              {diagnosis.original_mode && (
                <p className="mt-1 opacity-75">
                  {t("shared.upload.originalMode", { mode: diagnosis.original_mode })}
                </p>
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
            {showDetail ? t("shared.upload.hideDetails") : t("shared.upload.technicalDetails")}
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
          <>
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
              {t("shared.upload.fixedSizeSplit")}
            </Button>
            <p className="text-xs opacity-60">
              {t("shared.upload.manualMarkerHint")}
            </p>
          </>
        )}
      </div>
    </div>
  )
}

type Stage = "select" | "uploading" | "preview" | "duplicate" | "confirming" | "import-preview" | "import-confirming"

function formatWordCount(
  count: number,
  t: (key: TranslationKey, params?: Record<string, number | string>) => string,
): string {
  if (count >= 10000) return t("bookshelf.wordCountWan", { count: (count / 10000).toFixed(1) })
  return t("bookshelf.wordCount", { count })
}

function formatDate(dateStr: string, locale: string): string {
  const d = new Date(dateStr + "Z")
  return d.toLocaleString(locale)
}

function isExpiredError(message: string): boolean {
  return Array.from(EXPIRED_ERROR_MARKERS).some((marker) => message.includes(marker))
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
  const { locale, t } = useI18n()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const textPreviewRef = useRef<TextPreviewPanelHandle>(null)
  const [stage, setStage] = useState<Stage>("select")
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<UploadPreviewResponse | null>(null)
  const [existingNovel, setExistingNovel] = useState<Novel | null>(null)
  const [title, setTitle] = useState("")
  const [author, setAuthor] = useState("")
  const [sourceLanguage, setSourceLanguage] = useState<SourceLanguage>("auto")

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
  // Advanced split options — auto-expand when diagnosis is not OK
  const [advancedOpen, setAdvancedOpen] = useState(false)
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
    setSourceLanguage("auto")
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
      setError(t("shared.upload.error.rawPreviewFailed"))
    } finally {
      setRawTextLoading(false)
    }
  }, [rawText, t])

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
      setError(t("shared.upload.error.unsupportedFormat"))
      return
    }

    // Data import flow (.air / .json)
    if (DATA_IMPORT_EXTENSIONS.includes(ext)) {
      if (ext === ".json") {
        try {
          const text = await file.slice(0, 200).text()
          if (!text.includes("format_version")) {
            setError(t("shared.upload.error.invalidJsonData"))
            return
          }
        } catch {
          setError(t("shared.upload.error.readFileFailed"))
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
        setError(err instanceof Error ? err.message : t("shared.upload.error.previewFailed"))
        setStage("select")
      }
      return
    }

    // Novel upload flow (.txt / .md)
    setStage("uploading")
    setUploadProgress(0)
    try {
      const data = await uploadNovelWithProgress(file, setUploadProgress, sourceLanguage)
      setPreview(data)
      setSourceLanguage(data.source_language)
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
      setError(err instanceof Error ? err.message : t("shared.upload.error.uploadFailed"))
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
          source_language: sourceLanguage,
        })
        setPreview(data)
        setSourceLanguage(data.source_language)
      } catch (err) {
        const msg = err instanceof Error ? err.message : t("shared.upload.error.applyManualMarksFailed")
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
        source_language: sourceLanguage,
      })
      setSimulatedProgress(100)
      setTimeout(() => {
        onImported()
        handleOpenChange(false)
      }, 300)
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("shared.upload.error.importFailed")
      if (isExpiredError(msg)) {
        setStage("select")
        setError(t("shared.upload.error.uploadExpired"))
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
        source_language: sourceLanguage,
      })
      setSimulatedProgress(100)
      setTimeout(() => {
        onImported()
        handleOpenChange(false)
      }, 300)
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("shared.upload.error.overwriteImportFailed")
      if (isExpiredError(msg)) {
        setStage("select")
        setError(t("shared.upload.error.uploadExpired"))
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
      setError(err instanceof Error ? err.message : t("shared.upload.error.importFailed"))
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
        source_language: sourceLanguage,
      })
      setPreview(data)
      setSourceLanguage(data.source_language)
      setExcludedNums(new Set(
        data.chapters.filter((ch) => ch.is_suspect).map((ch) => ch.chapter_num),
      ))
      setSplitPoints([]) // Clear manual marks after re-split
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("shared.upload.error.resplitFailed")
      if (isExpiredError(msg)) {
        setStage("select")
        setError(t("shared.upload.error.uploadExpired"))
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

  const handleInferPattern = async () => {
    if (!preview || splitPoints.length < 2) return
    setReSplitting(true)
    setError(null)
    try {
      const result = await inferPattern({
        file_hash: preview.file_hash,
        split_points: splitPoints,
      })
      setPreview(result.preview)
      setSourceLanguage(result.preview.source_language)
      if (result.inferred_regex) {
        setSplitPoints([])
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("shared.upload.error.inferFailed")
      setError(msg)
    } finally {
      setReSplitting(false)
    }
  }

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
      setSourceLanguage(data.source_language)
      setExcludedNums(new Set(
        data.chapters.filter((ch) => ch.is_suspect).map((ch) => ch.chapter_num),
      ))
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("shared.upload.error.cleanFailed")
      if (isExpiredError(msg)) {
        setStage("select")
        setError(t("shared.upload.error.uploadExpired"))
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
  const modeLabel = (mode: string) => MODE_LABEL_KEYS[mode] ? t(MODE_LABEL_KEYS[mode]) : mode
  const genreLabel = (genre: string) => GENRE_LABEL_KEYS[genre] ? t(GENRE_LABEL_KEYS[genre]) : genre
  const categoryLabel = (category: string) => CATEGORY_LABEL_KEYS[category]
    ? t(CATEGORY_LABEL_KEYS[category])
    : category
  const sourceLanguageLabel = (language: SourceLanguage) => t(SOURCE_LANGUAGE_LABEL_KEYS[language])

  // Auto-expand advanced options when diagnosis indicates a problem
  useEffect(() => {
    if (diagnosis && diagnosis.tag !== "OK" && !diagnosis.auto_optimized) {
      setAdvancedOpen(true)
    }
  }, [diagnosis])

  // Determine dialog width class
  const dialogWidthClass = isExpanded ? "sm:max-w-7xl" : "sm:max-w-2xl"

  return (
    <>
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className={`${dialogWidthClass} transition-all duration-300`}>
          <DialogHeader>
            <DialogTitle>
              {stage === "select" || stage === "uploading"
                ? t("shared.upload.title.uploadNovel")
                : stage === "duplicate"
                  ? t("shared.upload.title.duplicate")
                  : stage === "import-preview" || stage === "import-confirming"
                    ? t("shared.upload.title.importData")
                    : t("shared.upload.title.confirmImport")}
            </DialogTitle>
            <DialogDescription>
              {stage === "select" || stage === "uploading"
                ? t("shared.upload.description.select")
                : stage === "duplicate"
                  ? t("shared.upload.description.duplicate")
                  : stage === "import-preview" || stage === "import-confirming"
                    ? t("shared.upload.description.importData")
                    : t("shared.upload.description.preview")}
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
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>{t("shared.upload.sourceLanguage.label")}</Label>
                <Select
                  value={sourceLanguage}
                  onValueChange={(value) => setSourceLanguage(value as SourceLanguage)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(["auto", "zh-CN", "vi", "en"] as const).map((language) => (
                      <SelectItem key={language} value={language}>
                        {sourceLanguageLabel(language)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  {t("shared.upload.sourceLanguage.hint")}
                </p>
              </div>
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
                  {t("shared.upload.dropTitle")}
                </p>
                <p className="text-muted-foreground/60 mt-1 text-xs">
                  {t("shared.upload.dropHint")}
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
            </div>
          )}

          {/* Stage: Uploading */}
          {stage === "uploading" && (
            <div className="flex flex-col items-center justify-center gap-3 py-12">
              <Loader2 className="text-primary h-10 w-10 animate-spin" />
              <p className="text-muted-foreground text-sm">
                {uploadProgress < 100 ? t("shared.upload.uploading") : t("shared.upload.parsing")}
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
                  <p className="text-muted-foreground mb-2 text-xs font-medium uppercase">{t("shared.upload.existingVersion")}</p>
                  <p className="font-semibold">{existingNovel.title}</p>
                  {existingNovel.author && (
                    <p className="text-muted-foreground text-sm">{existingNovel.author}</p>
                  )}
                  <div className="text-muted-foreground mt-3 space-y-1 text-sm">
                    <p>{t("common.chapterCount", { count: existingNovel.total_chapters })}</p>
                    <p>{formatWordCount(existingNovel.total_words, t)}</p>
                    <p>{t("shared.upload.importedAt", { date: formatDate(existingNovel.created_at, locale) })}</p>
                  </div>
                </div>
                <ArrowRightLeft className="text-muted-foreground h-5 w-5" />
                <div className="border-primary/30 rounded-lg border p-4">
                  <p className="text-muted-foreground mb-2 text-xs font-medium uppercase">{t("shared.upload.newVersion")}</p>
                  <p className="font-semibold">{preview.title}</p>
                  {preview.author && (
                    <p className="text-muted-foreground text-sm">{preview.author}</p>
                  )}
                  <div className="text-muted-foreground mt-3 space-y-1 text-sm">
                    <p>{t("common.chapterCount", { count: preview.total_chapters })}</p>
                    <p>{formatWordCount(preview.total_words, t)}</p>
                    <p>{t("shared.upload.justUploaded")}</p>
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2 rounded-md bg-yellow-50 px-3 py-2 text-sm text-yellow-800 dark:bg-yellow-950 dark:text-yellow-200">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                {t("shared.upload.overwriteWarning")}
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
                    <Label htmlFor="novel-title">{t("shared.upload.field.title")}</Label>
                    <Input
                      id="novel-title"
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      disabled={isWorking}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="novel-author">{t("shared.upload.field.author")}</Label>
                    <Input
                      id="novel-author"
                      value={author}
                      placeholder={t("entity.stat.unknown")}
                      onChange={(e) => setAuthor(e.target.value)}
                      disabled={isWorking}
                    />
                  </div>
                </div>

                {/* Summary */}
                <div className="text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                  <span className="flex items-center gap-1">
                    <FileText className="h-4 w-4" />
                    {t("shared.upload.totalChapters", { count: preview.total_chapters })}
                  </span>
                  <span>{formatWordCount(preview.total_words, t)}</span>
                  <span className="text-xs opacity-60">
                    {t("shared.upload.sourceLanguage.summary", { language: sourceLanguageLabel(sourceLanguage) })}
                  </span>
                  {preview.matched_mode && (
                    <span className="text-xs opacity-60">
                      {t("shared.upload.modeLabel", { mode: modeLabel(preview.matched_mode) })}
                    </span>
                  )}
                  {diagnosis?.detected_genre && diagnosis.detected_genre !== "unknown" && (
                    <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs font-medium">
                      {genreLabel(diagnosis.detected_genre)}
                    </span>
                  )}
                  {splitPoints.length > 0 && (
                    <span className="text-xs text-red-500">
                      {t("shared.upload.manualMarks", { count: splitPoints.length })}
                    </span>
                  )}
                  {splitPoints.length >= 2 && (
                    <Button
                      variant="outline"
                      size="xs"
                      onClick={handleInferPattern}
                      disabled={reSplitting}
                    >
                      {reSplitting ? (
                        <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                      ) : (
                        <Sparkles className="mr-1 h-3 w-3" />
                      )}
                      {t("shared.upload.inferMode")}
                    </Button>
                  )}
                  {splitPoints.length === 1 && (
                    <span className="text-xs text-muted-foreground">
                      {t("shared.upload.needOneMoreMark")}
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
                        {t("shared.upload.suspectLines", { count: hygieneReport.total_suspect_lines })}
                        {Object.entries(hygieneReport.by_category).length > 0 && (
                          <span className="ml-1 opacity-75">
                            （{Object.entries(hygieneReport.by_category)
                              .map(([cat, count]) => t("shared.upload.categoryCount", {
                                category: categoryLabel(cat),
                                count,
                              }))
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
                        {t("shared.upload.cleanAndResplit")}
                      </Button>
                    </button>
                    {hygieneOpen && hygieneReport.samples.length > 0 && (
                      <div className="border-t border-orange-200 px-3 py-2 dark:border-orange-900">
                        <div className="max-h-32 space-y-1 overflow-y-auto">
                          {hygieneReport.samples.map((s, i) => (
                            <div key={i} className="flex min-w-0 items-baseline gap-2 text-xs text-orange-700 dark:text-orange-300">
                              <span className="shrink-0 tabular-nums opacity-60">L{s.line_num}</span>
                              <span className="shrink-0 rounded bg-orange-200/60 px-1 py-0.5 text-[10px] font-medium dark:bg-orange-800/40">
                                {categoryLabel(s.category)}
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
                  <div className="rounded-md border">
                    <button
                      type="button"
                      className="text-muted-foreground hover:text-foreground flex w-full items-center gap-2 px-3 py-2 text-sm transition-colors"
                      onClick={() => setAdvancedOpen((v) => !v)}
                    >
                      {advancedOpen ? (
                        <ChevronDown className="h-4 w-4" />
                      ) : (
                        <ChevronRight className="h-4 w-4" />
                      )}
                      {t("shared.upload.advancedSplitOptions")}
                    </button>
                    {advancedOpen && (
                      <div className="border-t px-3 py-3">
                        <RegexTemplateSelector
                          onApply={handleRegexTemplateApply}
                          disabled={reSplitting || isWorking}
                        />
                        {reSplitting && (
                          <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
                            <Loader2 className="h-3 w-3 animate-spin" />
                            {t("shared.upload.resplitting")}
                          </div>
                        )}
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
                      {t("shared.upload.adjustSplitMethod")}
                    </button>
                    {splitOpen && (
                      <div className="space-y-3 border-t px-3 pb-3 pt-2">
                        <div className="flex items-end gap-3">
                          <div className="flex-1 space-y-1.5">
                            <Label className="text-xs">{t("shared.upload.splitMode")}</Label>
                            <Select
                              value={selectedMode}
                              onValueChange={setSelectedMode}
                              disabled={reSplitting}
                            >
                              <SelectTrigger className="h-8 text-sm">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="auto">{t("shared.upload.autoDetect")}</SelectItem>
                                {splitModes.map((m) => (
                                  <SelectItem key={m} value={m}>
                                    {modeLabel(m)}
                                  </SelectItem>
                                ))}
                                <SelectItem value="custom">{t("shared.regexTemplate.custom")}</SelectItem>
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
                            {t("shared.upload.resplit")}
                          </Button>
                        </div>
                        {selectedMode === "custom" && (
                          <div className="space-y-1.5">
                            <Label className="text-xs">{t("shared.upload.customRegex")}</Label>
                            <Input
                              value={customRegex}
                              onChange={(e) => setCustomRegex(e.target.value)}
                              placeholder={t("shared.regexTemplate.examplePlaceholder")}
                              className="h-8 font-mono text-sm"
                              disabled={reSplitting}
                            />
                            <p className="text-muted-foreground text-xs">
                              {t("shared.regexTemplate.multilineHint")}
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
                    <span>{t("shared.upload.excludedChapters", { count: excludedNums.size })}</span>
                    <button
                      type="button"
                      className="ml-auto text-amber-600 underline hover:no-underline dark:text-amber-400"
                      onClick={() => setExcludedNums(new Set())}
                    >
                      {t("shared.upload.clearAll")}
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
                            title={t("shared.upload.toggleExcludeAll")}
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
                        <th className="px-3 py-2 text-left font-medium">{t("shared.upload.chapterTitle")}</th>
                        <th className="px-3 py-2 text-right font-medium">{t("shared.upload.wordCount")}</th>
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
                                    {t("shared.upload.suspectNonContent")}
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
                              {formatWordCount(ch.word_count, t)}
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
                      <span className="ml-2 text-sm text-muted-foreground">{t("shared.upload.loadingRawText")}</span>
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
                      {t("shared.upload.rawTextUnavailable")}
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
                    {t("shared.upload.authorLine", { author: dataImportPreview.author })}
                  </p>
                )}
              </div>

              <div className="rounded-md border bg-muted/30 p-4 space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">{t("export.stat.chapters")}</span>
                  <span>
                    {t("common.chapterCount", { count: dataImportPreview.total_chapters })}
                    <span className="text-muted-foreground ml-1">
                      · {t("shared.upload.analyzedChapters", { count: dataImportPreview.analyzed_chapters })}
                    </span>
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">{t("shared.upload.wordCount")}</span>
                  <span>{formatWordCount(dataImportPreview.total_words, t)}</span>
                </div>
                {(dataImportPreview.entity_dict_count ?? 0) > 0 && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">{t("shared.upload.entityDictionary")}</span>
                    <span>{t("export.countItems", { count: dataImportPreview.entity_dict_count ?? 0 })}</span>
                  </div>
                )}
                <div className="flex justify-between">
                  <span className="text-muted-foreground">{t("shared.upload.formatVersion")}</span>
                  <span>v{dataImportPreview.format_version}</span>
                </div>
                {dataImportPreview.llm_models && dataImportPreview.llm_models.length > 0 && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">{t("shared.upload.analysisModel")}</span>
                    <span>{dataImportPreview.llm_models.join(", ")}</span>
                  </div>
                )}
                {(dataImportPreview.bookmarks_count ?? 0) > 0 && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">{t("shared.upload.bookmarks")}</span>
                    <span>{t("export.countItems", { count: dataImportPreview.bookmarks_count ?? 0 })}</span>
                  </div>
                )}
                {(dataImportPreview.map_overrides_count ?? 0) > 0 && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">{t("shared.upload.mapOverrides")}</span>
                    <span>{t("shared.upload.placeCount", { count: dataImportPreview.map_overrides_count ?? 0 })}</span>
                  </div>
                )}
                {(dataImportPreview.conversations_count ?? 0) > 0 && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">{t("shared.upload.conversations")}</span>
                    <span>{t("export.countItems", { count: dataImportPreview.conversations_count ?? 0 })}</span>
                  </div>
                )}
                <div className="mt-2 flex justify-between border-t pt-2">
                  <span className="text-muted-foreground">{t("shared.upload.dataSize")}</span>
                  <span>{(dataImportPreview.data_size_bytes / 1024 / 1024).toFixed(1)} MB</span>
                </div>
              </div>

              {dataImportPreview.existing_novel_id && (
                <div className="flex items-center gap-2 rounded-md bg-yellow-50 px-3 py-2 text-sm text-yellow-800 dark:bg-yellow-950 dark:text-yellow-200">
                  <AlertTriangle className="h-4 w-4 shrink-0" />
                  {t("shared.upload.existingNameOverwrite")}
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
                {t("common.cancel")}
              </Button>
              <Button
                onClick={handleDataImportConfirm}
                disabled={stage === "import-confirming"}
              >
                {stage === "import-confirming" ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    {t("shared.upload.importing")}
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="mr-2 h-4 w-4" />
                    {t("shared.upload.confirmImport")}
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
                {t("common.cancel")}
              </Button>
              <div className="flex gap-2">
                <Button variant="outline" onClick={handleImportAsNew}>
                  {t("shared.upload.importAsNew")}
                </Button>
                <Button variant="destructive" onClick={handleOverwrite}>
                  {t("shared.upload.overwriteExisting")}
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
                {t("shared.upload.reselect")}
              </Button>
              {!isExpanded && (
                <Button
                  variant="outline"
                  onClick={handleExpand}
                  disabled={isWorking}
                >
                  <Eye className="mr-1.5 h-4 w-4" />
                  {t("shared.upload.viewRawText")}
                </Button>
              )}
              <Button onClick={handleConfirm} disabled={isWorking || reSplitting}>
                {isWorking ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    {t("shared.upload.importing")}
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="mr-2 h-4 w-4" />
                    {t("shared.upload.confirmImport")}
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
            <AlertDialogTitle>{t("shared.upload.overwriteMarksTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("shared.upload.overwriteMarksDescription", { count: splitPoints.length })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={handleOverwriteConfirmed}>
              {t("common.continue")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
