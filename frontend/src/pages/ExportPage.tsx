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
import { useI18n } from "@/i18n"
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
  const { t } = useI18n()

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
    // Rough estimate: ~8KB/chapter (fact_json avg) + ~500 bytes/entity + ~200 bytes/relation
    // Plus chapter content ~3KB/chapter avg, scenes ~2KB/chapter
    const bytes = chapters.length * 13000 + entities.length * 500 + relationCount * 200
    // gzip typically achieves 70-80% compression on JSON
    const mb = (bytes * 0.25) / 1024 / 1024
    return mb < 0.1 ? mb.toFixed(2) : mb.toFixed(1)
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
    setProgress(t("export.collectingData"))
    try {
      await exportSeriesBible(novelId, {
        template,
        modules: selectedModules,
        format: format === "word" ? "docx" : format === "excel" ? "xlsx" : format === "pdf" ? "pdf" : undefined,
        chapter_start: chapterStart || undefined,
        chapter_end: chapterEnd || undefined,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : t("export.failed"))
    } finally {
      setExporting(false)
      setProgress(null)
    }
  }, [novelId, format, template, selectedModules, chapterStart, chapterEnd, t])

  const handleAirExport = useCallback(async () => {
    if (!novelId) return
    setAirExporting(true)
    try {
      // Fetch title fresh if not yet loaded
      let title = novelTitle
      if (!title) {
        try {
          const n = await fetchNovel(novelId)
          title = n.title || ""
        } catch { /* ignore */ }
      }
      const resp = await fetch(exportNovelAirUrl(novelId))
      if (!resp.ok) throw new Error(`Export failed: ${resp.status}`)
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      const dateStr = new Date().toISOString().slice(0, 10).replace(/-/g, "")
      a.download = title ? `${title}_${dateStr}.air` : `export_${dateStr}.air`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (e) {
      console.error("AIR export failed:", e)
    } finally {
      setAirExporting(false)
    }
  }, [novelId, novelTitle])

  return (
    <div className="flex-1 overflow-auto">
      <div className="max-w-2xl mx-auto p-6 space-y-6">
        <h1 className="text-lg font-medium">{t("nav.export")}</h1>

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
            {t("export.tab.seriesBible")}
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
            {t("export.tab.analysisData")}
          </button>
        </div>

        {/* ── Series Bible Tab ── */}
        {activeTab === "bible" && (
          <div className="space-y-6">
            {/* Format selection */}
            <section>
              <h2 className="text-sm font-medium mb-3">{t("export.formatTitle")}</h2>
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
                      {f.available ? f.ext : t("export.comingSoon")}
                    </span>
                  </button>
                ))}
              </div>
            </section>

            {/* Template selection */}
            <section>
              <h2 className="text-sm font-medium mb-3">{t("export.templateTitle")}</h2>
              <div className="space-y-2">
                {SERIES_BIBLE_TEMPLATES.map((templateOption) => (
                  <button
                    key={templateOption.id}
                    className={cn(
                      "w-full text-left border rounded-lg p-3 transition-colors",
                      template === templateOption.id
                        ? "border-blue-500 bg-blue-50 dark:bg-blue-950/20"
                        : "hover:border-blue-300",
                    )}
                    onClick={() => setTemplate(templateOption.id)}
                  >
                    <span className="text-sm font-medium">{t(templateOption.nameKey)}</span>
                    <span className="text-xs text-muted-foreground ml-2">
                      {t(templateOption.descriptionKey)}
                    </span>
                  </button>
                ))}
              </div>
            </section>

            {/* Module selection */}
            {(format === "markdown" || format === "word" || format === "excel" || format === "pdf") && (
              <section>
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-sm font-medium">{t("export.modulesTitle")}</h2>
                  <button
                    className="text-xs text-blue-500 hover:underline"
                    onClick={selectAllModules}
                  >
                    {t("export.selectAll")}
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
                      <span className="text-sm">{t(m.labelKey)}</span>
                    </label>
                  ))}
                </div>
              </section>
            )}

            {/* Chapter range */}
            {totalChapters > 0 && (
              <section>
                <h2 className="text-sm font-medium mb-3">{t("export.chapterRange")}</h2>
                <div className="flex items-center gap-3">
                  <input
                    type="number"
                    min={1}
                    max={totalChapters}
                    placeholder={t("export.chapterStart")}
                    value={chapterStart ?? ""}
                    onChange={(e) => setChapterStart(e.target.value ? Number(e.target.value) : null)}
                    className="w-24 border rounded-md px-2 py-1.5 text-sm"
                  />
                  <span className="text-muted-foreground">~</span>
                  <input
                    type="number"
                    min={1}
                    max={totalChapters}
                    placeholder={t("export.chapterEnd")}
                    value={chapterEnd ?? ""}
                    onChange={(e) => setChapterEnd(e.target.value ? Number(e.target.value) : null)}
                    className="w-24 border rounded-md px-2 py-1.5 text-sm"
                  />
                  <span className="text-xs text-muted-foreground">
                    {t("export.chapterRangeHint", { count: totalChapters })}
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
                  ? t("export.exporting")
                  : format === "word"
                    ? t("export.exportWord")
                    : format === "excel"
                      ? t("export.exportExcel")
                      : format === "pdf"
                        ? t("export.exportPdf")
                        : t("export.exportMarkdown")}
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
                <h2 className="text-sm font-semibold">{t("export.dataTitle")}</h2>
                <p className="text-xs text-muted-foreground mt-1">
                  {t("export.dataDescription")}
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
                    <span className="text-muted-foreground">{t("export.stat.chapters")}</span>
                    <span>
                      {t("common.chapterCount", { count: activeChapters })}
                      {analyzedChapters > 0 && (
                        <span className="text-muted-foreground ml-1">
                          {t("export.analyzedChaptersSuffix", { count: analyzedChapters })}
                        </span>
                      )}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">{t("export.stat.people")}</span>
                    <span>{t("export.countItems", { count: entityCounts["person"] ?? 0 })}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">{t("export.stat.relations")}</span>
                    <span>{t("export.countRelations", { count: relationCount })}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">{t("export.stat.locations")}</span>
                    <span>{t("export.countItems", { count: entityCounts["location"] ?? 0 })}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">{t("export.stat.items")}</span>
                    <span>{t("export.countItems", { count: entityCounts["item"] ?? 0 })}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">{t("export.stat.orgs")}</span>
                    <span>{t("export.countItems", { count: entityCounts["org"] ?? 0 })}</span>
                  </div>
                  <div className="flex justify-between border-t pt-2 mt-2">
                    <span className="text-muted-foreground">{t("export.estimatedSize")}</span>
                    <span>~{estimatedSizeMB} MB</span>
                  </div>
                </div>
              )}

              <Button
                onClick={handleAirExport}
                disabled={airExporting || analyzedChapters === 0}
                className="w-full"
              >
                {airExporting ? t("export.exportingAir") : t("export.exportAir")}
              </Button>

              {analyzedChapters === 0 && !statsLoading && (
                <p className="text-xs text-amber-500">
                  {t("export.noAnalyzedChapters")}
                </p>
              )}
            </section>
          </div>
        )}
      </div>
    </div>
  )
}
