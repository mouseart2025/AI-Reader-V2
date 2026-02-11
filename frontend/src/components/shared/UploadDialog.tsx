import { useCallback, useRef, useState } from "react"
import {
  AlertTriangle,
  ArrowRightLeft,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileText,
  Loader2,
  RefreshCw,
  Upload,
} from "lucide-react"
import {
  uploadNovel,
  confirmImport,
  fetchNovel,
  deleteNovel,
  fetchSplitModes,
  reSplitChapters,
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
  chapter_zh: "第X章",
  section_zh: "第X回/节/卷",
  numbered: "数字编号 (1. / 001)",
  markdown: "Markdown 标题",
  separator: "分隔线 (--- / ===)",
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

  // Split adjustment state
  const [splitOpen, setSplitOpen] = useState(false)
  const [splitModes, setSplitModes] = useState<string[]>([])
  const [selectedMode, setSelectedMode] = useState<string>("auto")
  const [customRegex, setCustomRegex] = useState("")
  const [reSplitting, setReSplitting] = useState(false)

  const reset = useCallback(() => {
    setStage("select")
    setError(null)
    setPreview(null)
    setExistingNovel(null)
    setTitle("")
    setAuthor("")
    setSplitOpen(false)
    setSplitModes([])
    setSelectedMode("auto")
    setCustomRegex("")
    setReSplitting(false)
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
    } catch (err) {
      setError(err instanceof Error ? err.message : "重新切分失败")
    } finally {
      setReSplitting(false)
    }
  }

  const isWorking = stage === "confirming"

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
            </div>

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

            {/* Chapter list */}
            <div className="max-h-64 overflow-y-auto rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-muted/50 sticky top-0">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">#</th>
                    <th className="px-3 py-2 text-left font-medium">
                      章节标题
                    </th>
                    <th className="px-3 py-2 text-right font-medium">字数</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.chapters.map((ch) => (
                    <tr key={ch.chapter_num} className="border-t">
                      <td className="text-muted-foreground px-3 py-1.5">
                        {ch.chapter_num}
                      </td>
                      <td className="px-3 py-1.5">{ch.title}</td>
                      <td className="text-muted-foreground px-3 py-1.5 text-right">
                        {formatWordCount(ch.word_count)}
                      </td>
                    </tr>
                  ))}
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
