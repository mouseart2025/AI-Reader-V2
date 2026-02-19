import { useCallback, useState } from "react"
import { useParams } from "react-router-dom"
import { exportSeriesBible } from "@/api/client"
import { SERIES_BIBLE_MODULES, SERIES_BIBLE_TEMPLATES } from "@/api/types"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const FORMATS = [
  { id: "markdown", label: "Markdown", available: true, ext: ".md" },
  { id: "word", label: "Word", available: true, ext: ".docx" },
  { id: "excel", label: "Excel", available: true, ext: ".xlsx" },
  { id: "pdf", label: "PDF", available: true, ext: ".pdf" },
] as const

export default function ExportPage() {
  const { novelId } = useParams<{ novelId: string }>()

  const [format, setFormat] = useState("markdown")
  const [template, setTemplate] = useState("complete")
  const [selectedModules, setSelectedModules] = useState<string[]>(
    SERIES_BIBLE_MODULES.map((m) => m.id),
  )
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const toggleModule = useCallback((id: string) => {
    setSelectedModules((prev) =>
      prev.includes(id) ? prev.filter((m) => m !== id) : [...prev, id],
    )
  }, [])

  const selectAllModules = useCallback(() => {
    setSelectedModules(SERIES_BIBLE_MODULES.map((m) => m.id))
  }, [])

  const handleExport = useCallback(async () => {
    if (!novelId || selectedModules.length === 0) return
    setExporting(true)
    setError(null)
    try {
      await exportSeriesBible(novelId, {
        template: format === "markdown" ? template : undefined,
        modules: selectedModules,
        format: format === "word" ? "docx" : format === "excel" ? "xlsx" : format === "pdf" ? "pdf" : undefined,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : "导出失败")
    } finally {
      setExporting(false)
    }
  }, [novelId, format, template, selectedModules])

  return (
    <div className="flex-1 overflow-auto">
      <div className="max-w-2xl mx-auto p-6 space-y-6">
        <h1 className="text-lg font-medium">导出设定集</h1>

        {/* Format selection */}
        <section>
          <h2 className="text-sm font-medium mb-3">导出格式</h2>
          <div className="grid grid-cols-4 gap-3">
            {FORMATS.map((f) => (
              <button
                key={f.id}
                className={cn(
                  "border rounded-lg p-3 text-center transition-colors",
                  f.available
                    ? format === f.id
                      ? "border-blue-500 bg-blue-50 dark:bg-blue-950/20"
                      : "hover:border-blue-300 cursor-pointer"
                    : "opacity-40 cursor-not-allowed",
                )}
                onClick={() => f.available && setFormat(f.id)}
                disabled={!f.available}
              >
                <span className="text-sm font-medium block">{f.label}</span>
                <span className="text-[10px] text-muted-foreground">
                  {f.available ? f.ext : "即将推出"}
                </span>
              </button>
            ))}
          </div>
        </section>

        {/* Template selection */}
        {format === "markdown" && (
          <section>
            <h2 className="text-sm font-medium mb-3">选择模板</h2>
            <div className="space-y-2">
              {SERIES_BIBLE_TEMPLATES.map((t) => (
                <button
                  key={t.id}
                  className={cn(
                    "w-full text-left border rounded-lg p-3 transition-colors",
                    template === t.id
                      ? "border-blue-500 bg-blue-50 dark:bg-blue-950/20"
                      : "hover:border-blue-300",
                  )}
                  onClick={() => setTemplate(t.id)}
                >
                  <span className="text-sm font-medium">{t.name}</span>
                  <span className="text-xs text-muted-foreground ml-2">
                    {t.description}
                  </span>
                </button>
              ))}
            </div>
          </section>
        )}

        {/* Module selection */}
        {(format === "markdown" || format === "word" || format === "excel" || format === "pdf") && (
          <section>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-medium">导出模块</h2>
              <button
                className="text-xs text-blue-500 hover:underline"
                onClick={selectAllModules}
              >
                全选
              </button>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {SERIES_BIBLE_MODULES.map((m) => (
                <label
                  key={m.id}
                  className={cn(
                    "flex items-center gap-2 border rounded-lg p-2.5 cursor-pointer transition-colors",
                    selectedModules.includes(m.id)
                      ? "border-blue-400 bg-blue-50/50 dark:bg-blue-950/10"
                      : "hover:border-gray-400",
                  )}
                >
                  <input
                    type="checkbox"
                    className="rounded"
                    checked={selectedModules.includes(m.id)}
                    onChange={() => toggleModule(m.id)}
                  />
                  <span className="text-sm">{m.label}</span>
                </label>
              ))}
            </div>
          </section>
        )}

        {/* Export button */}
        <section className="pt-2">
          <Button
            onClick={handleExport}
            disabled={exporting || selectedModules.length === 0}
            className="w-full"
          >
            {exporting
              ? "导出中..."
              : format === "word"
                ? "导出 Word"
                : format === "excel"
                  ? "导出 Excel"
                  : format === "pdf"
                    ? "导出 PDF"
                    : "导出 Markdown"}
          </Button>
          {error && (
            <p className="text-xs text-red-500 mt-2">{error}</p>
          )}
        </section>
      </div>
    </div>
  )
}
