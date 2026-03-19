/**
 * NovelOverviewCard — collapsible overview card showing novel synopsis
 * and analysis statistics at the top of ReadingPage.
 */
import { useCallback, useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import {
  fetchNovelStats,
  generateSynopsis,
  updateSynopsis,
  type NovelStats,
} from "@/api/client"
import type { Novel } from "@/api/types"
import { isTauri } from "@/api/sidecarBridge"

const ENTITY_STAT_ITEMS = [
  { key: "person", label: "人物", color: "text-blue-500", page: "encyclopedia" },
  { key: "location", label: "地点", color: "text-green-500", page: "encyclopedia" },
  { key: "item", label: "物品", color: "text-orange-500", page: "encyclopedia" },
  { key: "org", label: "组织", color: "text-purple-500", page: "factions" },
  { key: "concept", label: "概念", color: "text-gray-500", page: "encyclopedia" },
] as const

function formatDuration(ms: number): string {
  if (!ms) return "—"
  const seconds = Math.floor(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}min`
  const hours = Math.floor(minutes / 60)
  const remainMin = minutes % 60
  return remainMin > 0 ? `${hours}h ${remainMin}min` : `${hours}h`
}

export function NovelOverviewCard({
  novel,
  novelId,
}: {
  novel: Novel
  novelId: string
}) {
  const navigate = useNavigate()
  const [stats, setStats] = useState<NovelStats | null>(null)
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(`overview-collapsed-${novelId}`) === "1"
    } catch {
      return false
    }
  })
  const [loading, setLoading] = useState(true)
  const [synopsisEditing, setSynopsisEditing] = useState(false)
  const [synopsisText, setSynopsisText] = useState("")
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState("")

  useEffect(() => {
    setLoading(true)
    fetchNovelStats(novelId)
      .then((s) => {
        setStats(s)
        setSynopsisText(s.synopsis ?? "")
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [novelId])

  const toggleCollapse = useCallback(() => {
    setCollapsed((v) => {
      const next = !v
      try {
        localStorage.setItem(`overview-collapsed-${novelId}`, next ? "1" : "0")
      } catch { /* ignore */ }
      return next
    })
  }, [novelId])

  const handleGenerate = useCallback(async () => {
    setGenerating(true)
    setGenerateError("")
    try {
      const { synopsis } = await generateSynopsis(novelId)
      if (synopsis) {
        setSynopsisText(synopsis)
        setStats((prev) => prev ? { ...prev, synopsis } : prev)
      } else {
        setGenerateError("生成失败，请稍后重试或手动输入")
      }
    } catch (e) {
      setGenerateError(`生成失败：${e instanceof Error ? e.message : "网络错误"}`)
    } finally {
      setGenerating(false)
    }
  }, [novelId])

  const handleSaveSynopsis = useCallback(async () => {
    try {
      await updateSynopsis(novelId, synopsisText)
      setStats((prev) => prev ? { ...prev, synopsis: synopsisText } : prev)
      setSynopsisEditing(false)
    } catch { /* ignore */ }
  }, [novelId, synopsisText])

  const isAnalyzed = stats && stats.chapters.analyzed > 0

  if (loading) {
    return (
      <div className="mb-6 rounded-lg border bg-card p-4 animate-pulse">
        <div className="h-5 w-1/3 rounded bg-muted" />
        <div className="mt-3 h-4 w-2/3 rounded bg-muted" />
        <div className="mt-2 h-4 w-1/2 rounded bg-muted" />
      </div>
    )
  }

  if (!stats) return null

  return (
    <div className="mb-6 rounded-lg border bg-card overflow-hidden">
      {/* Header — always visible */}
      <button
        className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-accent/30 transition-colors"
        onClick={toggleCollapse}
      >
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold truncate">{novel.title}</h3>
          <p className="text-xs text-muted-foreground">
            {novel.author && <span>{novel.author} · </span>}
            {stats.chapters.total - stats.chapters.excluded} 章 ·{" "}
            {novel.total_words >= 10000
              ? `${(novel.total_words / 10000).toFixed(1)}万字`
              : `${novel.total_words}字`}
            {stats.chapters.analyzed > 0 && (
              <span>
                {" "}· 分析进度{" "}
                {Math.round(
                  (stats.chapters.analyzed /
                    Math.max(stats.chapters.total - stats.chapters.excluded, 1)) *
                    100,
                )}
                %
              </span>
            )}
          </p>
        </div>
        <span className="text-xs text-muted-foreground shrink-0">
          {collapsed ? "展开" : "收起"}
        </span>
      </button>

      {/* Expandable content */}
      {!collapsed && (
        <div className="border-t px-4 pb-4 pt-3 space-y-4">
          {/* Synopsis */}
          {synopsisEditing ? (
            <div className="space-y-2">
              <textarea
                className="w-full rounded-md border bg-background px-3 py-2 text-sm resize-none focus:outline-none focus:ring-1 focus:ring-primary"
                rows={4}
                value={synopsisText}
                onChange={(e) => setSynopsisText(e.target.value)}
                placeholder="输入小说简介..."
              />
              <div className="flex gap-2 justify-end">
                <button
                  className="text-xs text-muted-foreground hover:text-foreground"
                  onClick={() => {
                    setSynopsisText(stats.synopsis ?? "")
                    setSynopsisEditing(false)
                  }}
                >
                  取消
                </button>
                <button
                  className="text-xs text-primary hover:underline"
                  onClick={handleSaveSynopsis}
                >
                  保存
                </button>
              </div>
            </div>
          ) : synopsisText ? (
            <div
              className="text-sm text-muted-foreground leading-relaxed cursor-pointer hover:text-foreground transition-colors"
              title="点击编辑"
              onClick={() => setSynopsisEditing(true)}
            >
              {synopsisText}
            </div>
          ) : isAnalyzed ? (
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <button
                  className="text-xs text-primary hover:underline disabled:opacity-50"
                  onClick={handleGenerate}
                  disabled={generating}
                >
                  {generating ? "AI 生成中，请稍候..." : "生成小说简介"}
                </button>
                <span className="text-xs text-muted-foreground">
                  或
                </span>
                <button
                  className="text-xs text-muted-foreground hover:text-foreground"
                  onClick={() => setSynopsisEditing(true)}
                >
                  手动输入
                </button>
              </div>
              {generateError && (
                <p className="text-xs text-red-500">{generateError}</p>
              )}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              分析完成后可自动生成简介
            </p>
          )}

          {/* Entity stats grid */}
          {isAnalyzed && (
            <div className="grid grid-cols-5 gap-2">
              {ENTITY_STAT_ITEMS.map(({ key, label, color, page }) => {
                const count = stats.entities[key as keyof typeof stats.entities] ?? 0
                return (
                  <button
                    key={key}
                    className="rounded-md border bg-muted/30 px-2 py-2 text-center hover:bg-accent/50 transition-colors"
                    onClick={() => navigate(isTauri ? `/novel/${novelId}/${page}` : `/${page}/${novelId}`)}
                    title={`查看${label}`}
                  >
                    <div className={`text-lg font-bold ${color}`}>{count}</div>
                    <div className="text-[10px] text-muted-foreground">{label}</div>
                  </button>
                )
              })}
            </div>
          )}

          {/* Analysis meta */}
          {isAnalyzed && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              {stats.llm_models.length > 0 && (
                <span>分析模型：{stats.llm_models.join(", ")}</span>
              )}
              {stats.total_extraction_ms > 0 && (
                <span>分析耗时：{formatDuration(stats.total_extraction_ms)}</span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
