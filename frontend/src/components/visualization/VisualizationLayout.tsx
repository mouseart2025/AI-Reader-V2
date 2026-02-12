import { useCallback, useEffect, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { fetchNovel } from "@/api/client"
import type { Novel } from "@/api/types"
import { useChapterRangeStore } from "@/stores/chapterRangeStore"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const TABS = [
  { key: "graph", label: "关系图", path: "/graph" },
  { key: "map", label: "世界地图", path: "/map" },
  { key: "timeline", label: "时间线", path: "/timeline" },
  { key: "factions", label: "势力图", path: "/factions" },
] as const

interface VisualizationLayoutProps {
  activeTab: string
  children: React.ReactNode
}

export function VisualizationLayout({
  activeTab,
  children,
}: VisualizationLayoutProps) {
  const { novelId } = useParams<{ novelId: string }>()
  const navigate = useNavigate()
  const [novel, setNovel] = useState<Novel | null>(null)

  const {
    chapterStart,
    chapterEnd,
    analyzedFirst,
    analyzedLast,
    totalChapters,
    setRange,
    setTotalChapters,
  } = useChapterRangeStore()

  useEffect(() => {
    if (!novelId) return
    fetchNovel(novelId).then((n) => {
      setNovel(n)
      setTotalChapters(n.total_chapters)
    })
  }, [novelId, setTotalChapters])

  const handleRangeChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>, which: "start" | "end") => {
      const val = Number(e.target.value)
      if (which === "start") {
        setRange(Math.min(val, chapterEnd), chapterEnd)
      } else {
        setRange(chapterStart, Math.max(val, chapterStart))
      }
    },
    [chapterStart, chapterEnd, setRange],
  )

  return (
    <div className="flex h-screen flex-col">
      {/* Top bar */}
      <header className="flex items-center gap-4 border-b px-4 py-2">
        <button
          className="text-muted-foreground text-sm hover:underline"
          onClick={() => navigate("/")}
        >
          &larr; {novel?.title ?? "..."}
        </button>

        <div className="flex-1" />

        {/* Tab navigation */}
        <div className="flex gap-1">
          {TABS.map((tab) => (
            <Button
              key={tab.key}
              variant={activeTab === tab.key ? "default" : "ghost"}
              size="xs"
              onClick={() => navigate(`${tab.path}/${novelId}`)}
            >
              {tab.label}
            </Button>
          ))}
        </div>
      </header>

      {/* Chapter range slider */}
      <div className="flex items-center gap-4 border-b bg-muted/30 px-4 py-2">
        <span className="text-muted-foreground text-xs">章节范围</span>

        <div className="flex items-center gap-2">
          <input
            type="range"
            min={analyzedFirst || 1}
            max={analyzedLast || totalChapters || 1}
            value={chapterStart}
            onChange={(e) => handleRangeChange(e, "start")}
            className="h-1.5 w-32 accent-primary"
          />
          <span className="text-xs font-mono w-8 text-center">{chapterStart}</span>
          <span className="text-muted-foreground text-xs">-</span>
          <span className="text-xs font-mono w-8 text-center">{chapterEnd}</span>
          <input
            type="range"
            min={analyzedFirst || 1}
            max={analyzedLast || totalChapters || 1}
            value={chapterEnd}
            onChange={(e) => handleRangeChange(e, "end")}
            className="h-1.5 w-32 accent-primary"
          />
        </div>

        {analyzedFirst > 0 && totalChapters > 0 && analyzedLast < totalChapters && (
          <span className="text-muted-foreground text-xs">
            已分析 {analyzedFirst}-{analyzedLast} / {totalChapters} 章
          </span>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">{children}</div>
    </div>
  )
}
