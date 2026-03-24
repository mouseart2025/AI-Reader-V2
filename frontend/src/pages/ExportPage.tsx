import { useCallback, useEffect, useMemo, useState } from "react"
import { useParams } from "react-router-dom"
import {
  exportNovelAirUrl,
  exportSeriesBible,
  fetchChapters,
  fetchEntities,
  fetchGraphData,
  fetchNovel,
} from "@/api/client"
import type { Chapter, EntitySummary } from "@/api/types"
import { SERIES_BIBLE_MODULES, SERIES_BIBLE_TEMPLATES } from "@/api/types"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const FORMATS = [
  { id: "markdown", label: "Markdown", available: true, ext: ".md" },
  { id: "word", label: "Word", available: true, ext: ".docx" },
  { id: "excel", label: "Excel", available: true, ext: ".xlsx" },
  { id: "pdf", label: "PDF", available: true, ext: ".pdf" },
] as const

type ExportTab = "bible" | "data"

export default function ExportPage() {
  const { novelId } = useParams<{ novelId: string }>()

  const [activeTab, setActiveTab] = useState<ExportTab>("bible")

  // ── Series Bible state ──
  const [format, setFormat] = useState("markdown")
  const [template, setTemplate] = useState("complete")
  const [selectedModules, setSelectedModules] = useState<string[]>(
    SERIES_BIBLE_MODULES.map((m) => m.id),
  )
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [totalChapters, setTotalChapters] = useState(0)
  const [chapterStart, setChapterStart] = useState<number | null>(null)
  const [chapterEnd, setChapterEnd] = useState<number | null>(null)
  const [progress, setProgress] = useState<string | null>(null)

  // ── Analysis Data state ──
  const [chapters, setChapters] = useState<Chapter[]>([])
  const [entities, setEntities] = useState<EntitySummary[]>([])
  const [relationCount, setRelationCount] = useState(0)
  const [airExporting, setAirExporting] = useState(false)
  const [statsLoading, setStatsLoading] = useState(false)
  const [novelTitle, setNovelTitle] = useState("")

  useEffect(() => {
    if (!novelId) return
    fetchNovel(novelId)
      .then((novel) => {
        setTotalChapters(novel.total_chapters)
        setNovelTitle(novel.title || "")
      })
      .catch(() => {})
  }, [novelId])

  // Load stats when switching to data tab
  useEffect(() => {
    if (activeTab !== "data" || !novelId || chapters.length > 0) return
    setStatsLoading(true)
    Promise.all([
      fetchChapters(novelId).then((r) => setChapters(r.chapters)),
      fetchEntities(novelId).then((r) => setEntities(r.entities)),
      fetchGraphData(novelId).then((r) => {
        const edges = r.edges as unknown[]
        setRelationCount(edges?.length ?? 0)
      }),
    ])
      .catch(() => {})
      .finally(() => setStatsLoading(false))
  }, [activeTab, novelId, chapters.length])

  // Computed stats
  const analyzedChapters = useMemo(
    () => chapters.filter((c) => !c.is_excluded && c.analysis_status === "completed").length,
    [chapters],
  )
  const activeChapters = useMemo(
    () => chapters.filter((c) => !c.is_excluded).length,
    [chapters],
  )

  const entityCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const e of entities) {
      counts[e.type] = (counts[e.type] || 0) + 1
    }
    return counts
  }, [entities])

  const estimatedSizeMB = useMemo(() => {
    // Rough estimate: ~600 bytes/chapter + ~200 bytes/entity + ~150 bytes/relation
    const bytes = chapters.length * 600 + entities.length * 200 + relationCount * 150
    // gzip typically achieves 70-80% compression
    return ((bytes * 0.25) / 1024 / 1024).toFixed(1)
  }, [chapters.length, entities.length, relationCount])

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
    setProgress("正在收集数据...")
    try {
      await exportSeriesBible(novelId, {
        template,
        modules: selectedModules,
        format: format === "word" ? "docx" : format === "excel" ? "xlsx" : format === "pdf" ? "pdf" : undefined,
        chapter_start: chapterStart || undefined,
        chapter_end: chapterEnd || undefined,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : "导出失败")
    } finally {
      setExporting(false)
      setProgress(null)
    }
  }, [novelId, format, template, selectedModules, chapterStart, chapterEnd])

  const handleAirExport = useCallback(async () => {
    if (!novelId) return
    setAirExporting(true)
    try {
      const resp = await fetch(exportNovelAirUrl(novelId))
      if (!resp.ok) throw new Error(`Export failed: ${resp.status}`)
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      // Use novel title + date as filename
      const dateStr = new Date().toISOString().slice(0, 10).replace(/-/g, "")
      a.download = novelTitle ? `${novelTitle}_${dateStr}.air` : `export_${dateStr}.air`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (e) {
      console.error("AIR export failed:", e)
    } finally {
      setAirExporting(false)
    }
  }, [novelId])

  return (
    <div className="flex-1 overflow-auto">
      <div className="max-w-2xl mx-auto p-6 space-y-6">
        <h1 className="text-lg font-medium">导出</h1>

        {/* Tab bar */}
        <div className="flex gap-2 border-b pb-0">
          <button
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px",
              activeTab === "bible"
                ? "border-blue-500 text-blue-600 dark:text-blue-400"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setActiveTab("bible")}
          >
            设定集
          </button>
          <button
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px",
              activeTab === "data"
                ? "border-blue-500 text-blue-600 dark:text-blue-400"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setActiveTab("data")}
          >
            分析数据
          </button>
        </div>

        {/* ── Series Bible Tab ── */}
        {activeTab === "bible" && (
          <div className="space-y-6">
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

            {/* Chapter range */}
            {totalChapters > 0 && (
              <section>
                <h2 className="text-sm font-medium mb-3">章节范围</h2>
                <div className="flex items-center gap-3">
                  <input
                    type="number"
                    min={1}
                    max={totalChapters}
                    placeholder="起始章"
                    value={chapterStart ?? ""}
                    onChange={(e) => setChapterStart(e.target.value ? Number(e.target.value) : null)}
                    className="w-24 border rounded-md px-2 py-1.5 text-sm"
                  />
                  <span className="text-muted-foreground">~</span>
                  <input
                    type="number"
                    min={1}
                    max={totalChapters}
                    placeholder="结束章"
                    value={chapterEnd ?? ""}
                    onChange={(e) => setChapterEnd(e.target.value ? Number(e.target.value) : null)}
                    className="w-24 border rounded-md px-2 py-1.5 text-sm"
                  />
                  <span className="text-xs text-muted-foreground">
                    共 {totalChapters} 章（留空导出全部）
                  </span>
                </div>
              </section>
            )}

            {/* Export button */}
            <section className="pt-2">
              {progress && (
                <div className="mb-3">
                  <p className="text-xs text-muted-foreground mb-1">{progress}</p>
                  <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                    <div className="h-full bg-blue-500 rounded-full animate-pulse w-2/3" />
                  </div>
                </div>
              )}
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
        )}

        {/* ── Analysis Data Tab ── */}
        {activeTab === "data" && (
          <div className="space-y-6">
            <section className="rounded-lg border p-5 space-y-4">
              <div>
                <h2 className="text-sm font-semibold">导出分析数据</h2>
                <p className="text-xs text-muted-foreground mt-1">
                  导出完整分析数据包（.air），可分享给其他 AI Reader 用户直接导入使用。
                </p>
              </div>

              {statsLoading ? (
                <div className="space-y-2">
                  {Array.from({ length: 5 }).map((_, i) => (
                    <div key={i} className="h-5 animate-pulse rounded bg-muted w-2/3" />
                  ))}
                </div>
              ) : (
                <div className="rounded-md border bg-muted/30 p-4 space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">章节</span>
                    <span>
                      {activeChapters} 章
                      {analyzedChapters > 0 && (
                        <span className="text-muted-foreground ml-1">
                          （已分析 {analyzedChapters} 章）
                        </span>
                      )}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">人物</span>
                    <span>{entityCounts["person"] ?? 0} 个</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">关系</span>
                    <span>{relationCount} 条</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">地点</span>
                    <span>{entityCounts["location"] ?? 0} 个</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">物品</span>
                    <span>{entityCounts["item"] ?? 0} 个</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">组织</span>
                    <span>{entityCounts["org"] ?? 0} 个</span>
                  </div>
                  <div className="flex justify-between border-t pt-2 mt-2">
                    <span className="text-muted-foreground">预估大小</span>
                    <span>~{estimatedSizeMB} MB</span>
                  </div>
                </div>
              )}

              <Button
                onClick={handleAirExport}
                disabled={airExporting || analyzedChapters === 0}
                className="w-full"
              >
                {airExporting ? "正在导出..." : "导出 .air 文件"}
              </Button>

              {analyzedChapters === 0 && !statsLoading && (
                <p className="text-xs text-amber-500">
                  尚无已分析章节，请先完成分析后再导出。
                </p>
              )}
            </section>
          </div>
        )}
      </div>
    </div>
  )
}
