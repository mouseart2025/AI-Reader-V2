import { useCallback, useRef, useState } from "react"
import {
  AlertTriangle,
  ArrowRightLeft,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileText,
  Info,
  Loader2,
  RefreshCw,
  Sparkles,
  Upload,
} from "lucide-react"
import {
  uploadNovel,
  confirmImport,
  fetchNovel,
  deleteNovel,
  fetchSplitModes,
  reSplitChapters,
  cleanAndResplit,
} from "@/api/client"
import type { Novel, UploadPreviewResponse } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
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

const ALLOWED_EXTENSIONS = [".txt", ".md"]

const MODE_LABELS: Record<string, string> = {
  chapter_zh: "第X章 / 番外",
  section_zh: "第X回/节/卷/幕/场",
  numbered: "数字编号 (1. / 001)",
  chapter_en: "英文章节 (Chapter / Part)",
  markdown: "Markdown 标题",
  separator: "分隔线 (--- / ===)",
  heuristic_title: "启发式标题检测",
  fixed_size: "按字数切分 (~8000字/段)",
}

const CATEGORY_LABELS: Record<string, string> = {
  url: "链接",
  promo: "推广",
  template: "站点模板",
  decoration: "装饰线",
  repeated: "重复尾注",
}

type Stage = "select" | "uploading" | "preview" | "duplicate" | "confirming"

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
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onImported: () => void
}) {
  const fileInputRef = useRef<HTMLInputElement>(null)
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
    if (fileInputRef.current) fileInputRef.current.value = ""
  }, [])

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) reset()
    onOpenChange(nextOpen)
  }

  const handleFileSelect = async (file: File) => {
    setError(null)

    // Validate extension
    const ext = file.name.includes(".")
      ? "." + file.name.split(".").pop()!.toLowerCase()
      : ""
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      setError("仅支持 .txt / .md 格式")
      return
    }

    // Upload
    setStage("uploading")
    try {
      const data = await uploadNovel(file)
      setPreview(data)
      setTitle(data.title)
      setAuthor(data.author ?? "")
      // Initialize exclusion set from auto-detected suspects
      setExcludedNums(new Set(
        data.chapters.filter((ch) => ch.is_suspect).map((ch) => ch.chapter_num),
      ))

      // If duplicate detected, fetch existing novel details for comparison
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
    setStage("confirming")
    try {
      await confirmImport({
        file_hash: preview.file_hash,
        title: title.trim() || preview.title,
        author: author.trim() || null,
        excluded_chapters: excludedNums.size > 0 ? [...excludedNums] : undefined,
      })
      onImported()
      handleOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "导入失败")
      setStage("preview")
    }
  }

  const handleOverwrite = async () => {
    if (!preview || !existingNovel) return
    setStage("confirming")
    try {
      // Delete existing first, then import new
      await deleteNovel(existingNovel.id)
      await confirmImport({
        file_hash: preview.file_hash,
        title: title.trim() || preview.title,
        author: author.trim() || null,
        excluded_chapters: excludedNums.size > 0 ? [...excludedNums] : undefined,
      })
      onImported()
      handleOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "覆盖导入失败")
      setStage("duplicate")
    }
  }

  const handleImportAsNew = () => {
    setExistingNovel(null)
    setStage("preview")
  }

  const handleToggleSplit = async () => {
    if (!splitOpen && splitModes.length === 0) {
      try {
        const data = await fetchSplitModes()
        setSplitModes(data.modes)
      } catch {
        // Ignore, will show without modes
      }
    }
    setSplitOpen(!splitOpen)
  }

  const handleReSplit = async () => {
    if (!preview) return
    setReSplitting(true)
    setError(null)
    try {
      const mode = selectedMode === "auto" ? null : selectedMode === "custom" ? null : selectedMode
      const regex = selectedMode === "custom" ? customRegex.trim() || null : null
      const data = await reSplitChapters({
        file_hash: preview.file_hash,
        mode,
        custom_regex: regex,
      })
      setPreview(data)
      setExcludedNums(new Set(
        data.chapters.filter((ch) => ch.is_suspect).map((ch) => ch.chapter_num),
      ))
    } catch (err) {
      setError(err instanceof Error ? err.message : "重新切分失败")
    } finally {
      setReSplitting(false)
    }
  }

  const handleFixedSizeSplit = async () => {
    if (!preview) return
    setReSplitting(true)
    setError(null)
    try {
      const data = await reSplitChapters({
        file_hash: preview.file_hash,
        mode: "fixed_size",
      })
      setPreview(data)
      setExcludedNums(new Set(
        data.chapters.filter((ch) => ch.is_suspect).map((ch) => ch.chapter_num),
      ))
    } catch (err) {
      setError(err instanceof Error ? err.message : "重新切分失败")
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
      setExcludedNums(new Set(
        data.chapters.filter((ch) => ch.is_suspect).map((ch) => ch.chapter_num),
      ))
    } catch (err) {
      setError(err instanceof Error ? err.message : "清理失败")
    } finally {
      setCleaning(false)
    }
  }

  const isWorking = stage === "confirming"
  const diagnosis = preview?.diagnosis
  const hygieneReport = preview?.hygiene_report

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {stage === "select" || stage === "uploading"
              ? "上传小说"
              : stage === "duplicate"
                ? "检测到重复文件"
                : "确认导入"}
          </DialogTitle>
          <DialogDescription>
            {stage === "select" || stage === "uploading"
              ? "选择 .txt 或 .md 文件"
              : stage === "duplicate"
                ? "该文件已导入过，请选择处理方式"
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
              支持 .txt / .md 格式
            </p>
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.md"
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
          <div className="flex flex-col items-center justify-center py-12">
            <Loader2 className="text-primary mb-3 h-10 w-10 animate-spin" />
            <p className="text-muted-foreground text-sm">正在解析文件...</p>
          </div>
        )}

        {/* Stage: Duplicate Comparison */}
        {stage === "duplicate" && preview && existingNovel && (
          <div className="space-y-4">
            <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
              {/* Existing version */}
              <div className="rounded-lg border p-4">
                <p className="text-muted-foreground mb-2 text-xs font-medium uppercase">
                  已有版本
                </p>
                <p className="font-semibold">{existingNovel.title}</p>
                {existingNovel.author && (
                  <p className="text-muted-foreground text-sm">
                    {existingNovel.author}
                  </p>
                )}
                <div className="text-muted-foreground mt-3 space-y-1 text-sm">
                  <p>{existingNovel.total_chapters} 章</p>
                  <p>{formatWordCount(existingNovel.total_words)}</p>
                  <p>导入于 {formatDate(existingNovel.created_at)}</p>
                </div>
              </div>

              {/* Arrow */}
              <ArrowRightLeft className="text-muted-foreground h-5 w-5" />

              {/* New version */}
              <div className="border-primary/30 rounded-lg border p-4">
                <p className="text-muted-foreground mb-2 text-xs font-medium uppercase">
                  新上传版本
                </p>
                <p className="font-semibold">{preview.title}</p>
                {preview.author && (
                  <p className="text-muted-foreground text-sm">
                    {preview.author}
                  </p>
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

        {/* Stage: Preview */}
        {(stage === "preview" || stage === "confirming") && preview && (
          <div className="space-y-4">
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
            <div className="text-muted-foreground flex items-center gap-4 text-sm">
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
            </div>

            {/* Diagnosis banner */}
            {diagnosis && diagnosis.tag !== "OK" && (
              <div className={`flex items-start gap-2 rounded-md px-3 py-2 text-sm ${
                diagnosis.tag === "FALLBACK_USED"
                  ? "bg-blue-50 text-blue-800 dark:bg-blue-950 dark:text-blue-200"
                  : diagnosis.tag === "SINGLE_HUGE_CHAPTER" || diagnosis.tag === "NO_HEADING_MATCH"
                    ? "bg-orange-50 text-orange-800 dark:bg-orange-950 dark:text-orange-200"
                    : "bg-yellow-50 text-yellow-800 dark:bg-yellow-950 dark:text-yellow-200"
              }`}>
                <Info className="h-4 w-4 shrink-0 mt-0.5" />
                <div className="flex-1 space-y-1">
                  <p>{diagnosis.message}</p>
                  {diagnosis.suggestion && (
                    <p className="text-xs opacity-75">{diagnosis.suggestion}</p>
                  )}
                  {(diagnosis.tag === "SINGLE_HUGE_CHAPTER" || diagnosis.tag === "NO_HEADING_MATCH") && (
                    <Button
                      variant="outline"
                      size="xs"
                      className="mt-1"
                      onClick={handleFixedSizeSplit}
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
                    <div className="max-h-32 overflow-y-auto space-y-1">
                      {hygieneReport.samples.map((s, i) => (
                        <div key={i} className="flex items-baseline gap-2 text-xs text-orange-700 dark:text-orange-300 min-w-0">
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

            {/* Split adjustment */}
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

            {/* Exclusion summary */}
            {excludedNums.size > 0 && (
              <div className="flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                <span>
                  {excludedNums.size} 个章节将被排除（不参与分析）
                </span>
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
            <div className="max-h-64 overflow-y-auto rounded-md border">
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
                    <th className="px-3 py-2 text-left font-medium">
                      章节标题
                    </th>
                    <th className="px-3 py-2 text-right font-medium">字数</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.chapters.map((ch) => {
                    const isExcluded = excludedNums.has(ch.chapter_num)
                    return (
                      <tr
                        key={ch.chapter_num}
                        className={`border-t ${isExcluded ? "bg-amber-50/60 dark:bg-amber-950/30" : ""}`}
                      >
                        <td className="px-2 py-1.5 text-center">
                          <input
                            type="checkbox"
                            className="h-3.5 w-3.5 rounded"
                            checked={isExcluded}
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
                        <td className={`px-3 py-1.5 truncate ${isExcluded ? "text-muted-foreground line-through" : ""}`} title={ch.title}>
                          {ch.title}
                          {ch.is_suspect && !isExcluded && (
                            <span className="ml-1.5 text-xs text-amber-600 dark:text-amber-400">
                              (疑似非正文)
                            </span>
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
            <Button
              variant="outline"
              onClick={() => reset()}
              disabled={isWorking}
            >
              重新选择
            </Button>
            <Button onClick={handleConfirm} disabled={isWorking}>
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
  )
}
