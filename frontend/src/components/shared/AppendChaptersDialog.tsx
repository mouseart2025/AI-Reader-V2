import { useCallback, useRef, useState } from "react"
import {
  AlertTriangle,
  CheckCircle2,
  FileText,
  Loader2,
  Plus,
  Upload,
} from "lucide-react"
import { appendChapters } from "@/api/client"
import type { AppendChaptersResponse, Novel } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

type InputMode = "file" | "text"

export function AppendChaptersDialog({
  open,
  onOpenChange,
  novel,
  onAppended,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Target novel; null while none selected */
  novel: Novel | null
  onAppended: () => void
}) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [mode, setMode] = useState<InputMode>("file")
  const [file, setFile] = useState<File | null>(null)
  const [text, setText] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AppendChaptersResponse | null>(null)

  const reset = useCallback(() => {
    setMode("file")
    setFile(null)
    setText("")
    setSubmitting(false)
    setError(null)
    setResult(null)
    if (fileInputRef.current) fileInputRef.current.value = ""
  }, [])

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) reset()
    onOpenChange(nextOpen)
  }

  const handleFileSelect = (f: File) => {
    setError(null)
    const ext = f.name.includes(".")
      ? "." + f.name.split(".").pop()!.toLowerCase()
      : ""
    if (ext !== ".txt" && ext !== ".md") {
      setError("仅支持 .txt / .md 格式")
      return
    }
    setFile(f)
  }

  const handleSubmit = async () => {
    if (!novel) return
    setError(null)
    if (mode === "file" && !file) {
      setError("请先选择文件")
      return
    }
    if (mode === "text" && !text.trim()) {
      setError("请先粘贴要追加的文本")
      return
    }
    setSubmitting(true)
    try {
      const res = await appendChapters(novel.id, {
        file: mode === "file" ? (file ?? undefined) : undefined,
        text: mode === "text" ? text : undefined,
      })
      setResult(res)
      if (res.added > 0) onAppended()
    } catch (err) {
      setError(err instanceof Error ? err.message : "追加失败")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>追加章节{novel ? ` - ${novel.title}` : ""}</DialogTitle>
          <DialogDescription>
            当前共 {novel?.total_chapters ?? 0} 章。新章节将从第{" "}
            {(novel?.total_chapters ?? 0) + 1} 章开始编号，与已有章节重复的内容会自动跳过；已有章节的分析数据不受影响。
          </DialogDescription>
        </DialogHeader>

        {error && (
          <div className="flex items-center gap-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            {error}
          </div>
        )}

        {result ? (
          <div className="space-y-3">
            <div
              className={`flex items-start gap-2 rounded-md px-3 py-2 text-sm ${
                result.added > 0
                  ? "bg-green-50 text-green-800 dark:bg-green-950 dark:text-green-200"
                  : "bg-yellow-50 text-yellow-800 dark:bg-yellow-950 dark:text-yellow-200"
              }`}
            >
              {result.added > 0 ? (
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
              ) : (
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              )}
              <div>
                <p>{result.message}</p>
                {result.added > 0 && (
                  <p className="mt-1 text-xs opacity-75">
                    新增章节已标记为未分析，可前往分析页继续分析（已完成章节会自动跳过）。
                  </p>
                )}
              </div>
            </div>
            {result.chapters.length > 0 && (
              <div className="max-h-48 overflow-y-auto rounded-md border">
                <table className="w-full table-fixed text-sm">
                  <tbody>
                    {result.chapters.map((ch) => (
                      <tr key={ch.chapter_num} className="border-t first:border-t-0">
                        <td className="text-muted-foreground w-12 px-3 py-1.5">
                          {ch.chapter_num}
                        </td>
                        <td className="truncate px-3 py-1.5">{ch.title}</td>
                        <td className="text-muted-foreground w-20 px-3 py-1.5 text-right">
                          {ch.word_count}字
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {/* Mode toggle */}
            <div className="flex gap-1 rounded-md border p-1">
              {(
                [
                  { key: "file", label: "上传文件", icon: FileText },
                  { key: "text", label: "粘贴文本", icon: Plus },
                ] as const
              ).map((m) => (
                <button
                  key={m.key}
                  type="button"
                  className={`flex flex-1 items-center justify-center gap-1.5 rounded px-3 py-1.5 text-sm transition-colors ${
                    mode === m.key
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                  onClick={() => setMode(m.key)}
                  disabled={submitting}
                >
                  <m.icon className="h-3.5 w-3.5" />
                  {m.label}
                </button>
              ))}
            </div>

            {mode === "file" ? (
              <div
                className="border-muted-foreground/25 hover:border-primary/50 flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed py-8 transition-colors"
                onClick={() => fileInputRef.current?.click()}
                onDragOver={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                }}
                onDrop={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  const f = e.dataTransfer.files[0]
                  if (f) handleFileSelect(f)
                }}
              >
                <Upload className="text-muted-foreground mb-2 h-8 w-8" />
                <p className="text-muted-foreground text-sm">
                  {file ? file.name : "点击或拖拽文件到此处"}
                </p>
                <p className="text-muted-foreground/60 mt-1 text-xs">
                  支持 .txt / .md，章节切分规则与导入新小说相同
                </p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".txt,.md"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (f) handleFileSelect(f)
                  }}
                />
              </div>
            ) : (
              <div className="space-y-1.5">
                <Label htmlFor="append-text" className="text-xs">
                  追加文本（按章节标题自动切分）
                </Label>
                <textarea
                  id="append-text"
                  className="border-input bg-background ring-offset-background placeholder:text-muted-foreground focus-visible:ring-ring min-h-40 w-full rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50"
                  placeholder={"第一百零一章 标题\n正文……\n第一百零二章 标题\n正文……"}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  disabled={submitting}
                />
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          {result ? (
            <Button onClick={() => handleOpenChange(false)}>完成</Button>
          ) : (
            <>
              <Button
                variant="outline"
                onClick={() => handleOpenChange(false)}
                disabled={submitting}
              >
                取消
              </Button>
              <Button onClick={handleSubmit} disabled={submitting}>
                {submitting ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    追加中...
                  </>
                ) : (
                  "确认追加"
                )}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
