/**
 * StorylineView — 故事线视图组件
 * 水平泳道：X 轴=章节，Y 轴=角色，事件节点按类型着色、按重要度调整大小
 * 多角色交汇点用垂直连接线标注
 *
 * Zoom strategy: React state-driven. D3 zoom updates (k, tx) state,
 * React re-renders with transformed x coordinates. Node shapes stay fixed.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { novelPath } from "@/lib/novelPaths"
import { useNavigate } from "react-router-dom"
import { cn } from "@/lib/utils"
import { X } from "lucide-react"

// ── Types ──────────────────────────────────────────────────

interface TimelineEvent {
  id: string
  chapter: number
  summary: string
  type: string
  importance: string
  participants: string[]
  location: string | null
  is_major?: boolean
  emotional_tone?: string | null
}

type FilterType = "all" | "战斗" | "成长" | "社交" | "旅行" | "角色登场" | "物品交接" | "组织变动" | "关系变化" | "其他"

interface StorylineViewProps {
  events: TimelineEvent[]
  swimlanes: Record<string, string[]>
  novelId: string
  filterTypes: Set<FilterType>
  onToggleType: (type: FilterType) => void
}

// ── Constants ──────────────────────────────────────────────

const LANE_HEIGHT = 36
const LABEL_WIDTH = 72
const AXIS_HEIGHT = 24
const MINI_NAV_HEIGHT = 16
const DETAIL_HEIGHT = 130
const MAX_CHARACTERS = 20
const DEFAULT_TOP = 5

const CHARACTER_PALETTE = [
  "#3b82f6", "#ef4444", "#10b981", "#f97316", "#8b5cf6", "#ec4899",
  "#06b6d4", "#eab308", "#14b8a6", "#f43f5e", "#6366f1", "#84cc16",
]

function eventColor(type: string): string {
  switch (type) {
    case "战斗": return "#ef4444"
    case "成长": return "#3b82f6"
    case "社交": return "#10b981"
    case "旅行": return "#f97316"
    case "角色登场": return "#8b5cf6"
    case "物品交接": return "#eab308"
    case "组织变动": return "#ec4899"
    case "关系变化": return "#06b6d4"
    default: return "#6b7280"
  }
}

function eventRadius(importance: string, isMajor?: boolean): number {
  if (isMajor) return 5
  switch (importance) {
    case "high": return 4
    case "medium": return 3
    case "low": return 2
    default: return 2
  }
}

// ── Component ──────────────────────────────────────────────

export default function StorylineView({
  events,
  swimlanes,
  novelId,
  filterTypes,
}: StorylineViewProps) {
  const navigate = useNavigate()
  const openEntityCard = useEntityCardStore((s) => s.openCard)

  // State
  const [selectedChars, setSelectedChars] = useState<string[]>([])
  const [search, setSearch] = useState("")
  const [selectedEvent, setSelectedEvent] = useState<TimelineEvent | null>(null)
  const [tooltip, setTooltip] = useState<{ event: TimelineEvent; x: number; y: number } | null>(null)
  const [hoverX, setHoverX] = useState<number | null>(null)
  const [initialized, setInitialized] = useState(false)
  const [toast, setToast] = useState("")

  // Zoom state — React-driven, no SVG <g> transform
  const [zoomK, setZoomK] = useState(1)
  const [zoomTx, setZoomTx] = useState(0)
  // Keep refs in sync for event handlers (avoids re-attaching listeners)
  const zoomKRef = useRef(1)
  const zoomTxRef = useRef(0)
  zoomKRef.current = zoomK
  zoomTxRef.current = zoomTx

  // Refs
  const svgRef = useRef<SVGSVGElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  // Sorted character list by event count
  const sortedCharacters = useMemo(() => {
    return Object.entries(swimlanes)
      .map(([name, ids]) => ({ name, count: ids.length }))
      .sort((a, b) => b.count - a.count)
  }, [swimlanes])

  // Auto-dismiss toast
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(""), 2000)
    return () => clearTimeout(t)
  }, [toast])

  // Auto-select top N on first load
  useEffect(() => {
    if (initialized || sortedCharacters.length === 0) return
    setSelectedChars(sortedCharacters.slice(0, DEFAULT_TOP).map((c) => c.name))
    setInitialized(true)
  }, [sortedCharacters, initialized])

  // Character color map
  const charColorMap = useMemo(() => {
    const map = new Map<string, string>()
    sortedCharacters.forEach((c, i) => {
      map.set(c.name, CHARACTER_PALETTE[i % CHARACTER_PALETTE.length])
    })
    return map
  }, [sortedCharacters])

  // Filtered character list for panel
  const filteredCharList = useMemo(() => {
    if (!search.trim()) return sortedCharacters
    const q = search.trim().toLowerCase()
    return sortedCharacters.filter((c) => c.name.toLowerCase().includes(q))
  }, [sortedCharacters, search])

  // Build per-character event arrays
  const charEvents = useMemo(() => {
    const map = new Map<string, TimelineEvent[]>()
    const eventById = new Map<string, TimelineEvent>()
    for (const e of events) eventById.set(e.id, e)

    for (const char of selectedChars) {
      const ids = swimlanes[char] ?? []
      const evts = ids
        .map((id) => eventById.get(id))
        .filter((e): e is TimelineEvent => e != null && filterTypes.has(e.type as FilterType))
      map.set(char, evts)
    }
    return map
  }, [events, swimlanes, selectedChars, filterTypes])

  // Chapter range
  const chapterRange = useMemo(() => {
    if (events.length === 0) return { min: 1, max: 1 }
    let min = Infinity, max = -Infinity
    for (const e of events) {
      if (e.chapter < min) min = e.chapter
      if (e.chapter > max) max = e.chapter
    }
    return { min, max }
  }, [events])

  // Find convergence events (same event ID, multiple selected chars)
  const convergences = useMemo(() => {
    const eventParticipants = new Map<string, string[]>()
    for (const [char, evts] of charEvents) {
      for (const e of evts) {
        if (!eventParticipants.has(e.id)) eventParticipants.set(e.id, [])
        eventParticipants.get(e.id)!.push(char)
      }
    }
    const result: { event: TimelineEvent; chars: string[] }[] = []
    const eventById = new Map(events.map((e) => [e.id, e]))
    for (const [eid, chars] of eventParticipants) {
      if (chars.length >= 2) {
        const e = eventById.get(eid)
        if (e) result.push({ event: e, chars })
      }
    }
    return result
  }, [charEvents, events])

  // Toggle character selection
  const toggleChar = useCallback((name: string) => {
    setSelectedChars((prev) => {
      if (prev.includes(name)) {
        if (prev.length <= 1) { setToast("至少保留一个角色"); return prev }
        return prev.filter((c) => c !== name)
      }
      if (prev.length >= MAX_CHARACTERS) { setToast(`最多选择 ${MAX_CHARACTERS} 个角色`); return prev }
      return [...prev, name]
    })
  }, [])

  // SVG dimensions
  const svgHeight = useMemo(() => {
    return AXIS_HEIGHT + selectedChars.length * LANE_HEIGHT + MINI_NAV_HEIGHT
  }, [selectedChars])

  // Base X scale (before zoom): chapter → pixel position within content area
  const baseXScale = useCallback(
    (chapter: number) => {
      const { min, max } = chapterRange
      const range = max - min || 1
      const containerW = containerRef.current?.clientWidth ?? 800
      const contentW = containerW - LABEL_WIDTH
      return LABEL_WIDTH + ((chapter - min) / range) * contentW
    },
    [chapterRange],
  )

  // Zoomed X scale: applies zoom transform to base position
  const zoomedX = useCallback(
    (chapter: number) => {
      const baseX = baseXScale(chapter)
      return (baseX - LABEL_WIDTH) * zoomK + zoomTx + LABEL_WIDTH
    },
    [baseXScale, zoomK, zoomTx],
  )

  // Zoom helpers
  const clampTx = useCallback((newTx: number, newK: number) => {
    const containerW = containerRef.current?.clientWidth ?? 800
    const contentW = containerW - LABEL_WIDTH
    const minTx = -(contentW * newK - contentW)
    return Math.max(minTx, Math.min(0, newTx))
  }, [])

  const applyZoom = useCallback((newK: number, newTx: number) => {
    const clamped = clampTx(newTx, newK)
    setZoomK(newK)
    setZoomTx(clamped)
  }, [clampTx])

  const handleZoomIn = useCallback(() => {
    const k = zoomKRef.current
    const tx = zoomTxRef.current
    const newK = Math.min(20, k * 1.5)
    const containerW = containerRef.current?.clientWidth ?? 800
    const center = (containerW / 2 - LABEL_WIDTH - tx) / k
    applyZoom(newK, (containerW / 2 - LABEL_WIDTH) - center * newK)
  }, [applyZoom])

  const handleZoomOut = useCallback(() => {
    const k = zoomKRef.current
    const tx = zoomTxRef.current
    const newK = Math.max(1, k / 1.5)
    const containerW = containerRef.current?.clientWidth ?? 800
    const center = (containerW / 2 - LABEL_WIDTH - tx) / k
    applyZoom(newK, (containerW / 2 - LABEL_WIDTH) - center * newK)
  }, [applyZoom])

  const handleZoomReset = useCallback(() => {
    applyZoom(1, 0)
  }, [applyZoom])

  // Drag to pan — uses refs for smooth performance (no re-attach on state change)
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return

    let dragging = false
    let startX = 0
    let startTx = 0

    const onDown = (e: MouseEvent) => {
      if (e.button !== 0) return
      const tag = (e.target as Element).tagName
      if (tag === "circle") return
      dragging = true
      startX = e.clientX
      startTx = zoomTxRef.current
      svg.style.cursor = "grabbing"
      e.preventDefault()
    }
    const onMove = (e: MouseEvent) => {
      if (!dragging) return
      const k = zoomKRef.current
      const containerW = containerRef.current?.clientWidth ?? 800
      const contentW = containerW - LABEL_WIDTH
      const minTx = -(contentW * k - contentW)
      const raw = startTx + (e.clientX - startX)
      setZoomTx(Math.max(minTx, Math.min(0, raw)))
    }
    const onUp = () => {
      if (!dragging) return
      dragging = false
      svg.style.cursor = ""
    }

    svg.addEventListener("mousedown", onDown)
    window.addEventListener("mousemove", onMove)
    window.addEventListener("mouseup", onUp)
    return () => {
      svg.removeEventListener("mousedown", onDown)
      window.removeEventListener("mousemove", onMove)
      window.removeEventListener("mouseup", onUp)
    }
  }, []) // empty deps — reads from refs

  // Mini navigation bar click-to-jump
  const handleMiniNavClick = useCallback((e: React.MouseEvent<SVGGElement>) => {
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return
    const clickX = e.clientX - rect.left
    const containerW = rect.width
    const contentW = containerW - LABEL_WIDTH
    const k = zoomKRef.current
    // Map click position to content fraction, then set tx so that fraction is centered
    const frac = (clickX - LABEL_WIDTH) / contentW
    const newTx = -(frac * contentW * k) + contentW / 2
    applyZoom(k, newTx)
  }, [applyZoom])

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSelectedEvent(null)
        setTooltip(null)
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [])

  // Dynamic chapter tick marks based on zoom level
  const ticks = useMemo(() => {
    const { min, max } = chapterRange
    const range = max - min
    // At higher zoom, show more granular ticks
    let step: number
    const effectiveRange = range / zoomK
    if (effectiveRange > 500) step = 50
    else if (effectiveRange > 200) step = 20
    else if (effectiveRange > 50) step = 10
    else if (effectiveRange > 20) step = 5
    else step = 1

    const result: number[] = []
    const start = Math.ceil(min / step) * step
    for (let ch = start; ch <= max; ch += step) {
      result.push(ch)
    }
    return result
  }, [chapterRange, zoomK])

  // Visible chapter range (for culling off-screen nodes)
  const visibleRange = useMemo(() => {
    const containerW = containerRef.current?.clientWidth ?? 800
    const contentW = containerW - LABEL_WIDTH
    const { min, max } = chapterRange
    const range = max - min || 1
    // Inverse of zoomedX: pixel → chapter
    const chFromPx = (px: number) => min + ((px - LABEL_WIDTH - zoomTx) / zoomK) / contentW * range
    return {
      min: Math.floor(chFromPx(LABEL_WIDTH) - 5),
      max: Math.ceil(chFromPx(containerW) + 5),
    }
  }, [chapterRange, zoomK, zoomTx])

  // Render
  if (events.length === 0) {
    return (
      <div className="flex items-center justify-center flex-1 text-muted-foreground text-sm">
        暂无时间线数据，请先完成章节分析
      </div>
    )
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Character selection panel */}
      <div className="w-[200px] flex-shrink-0 border-r flex flex-col overflow-hidden">
        <div className="p-2 border-b">
          <input
            type="text"
            placeholder="🔍 搜索角色..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full rounded-md border bg-background px-2.5 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </div>

        <div className="flex-1 overflow-auto py-1">
          {filteredCharList.map((c) => {
            const isSelected = selectedChars.includes(c.name)
            const color = charColorMap.get(c.name) ?? "#6b7280"
            return (
              <div key={c.name} className={cn(
                "flex items-center gap-1.5 px-2 py-1 transition-colors",
                isSelected
                  ? "bg-primary/10 border-l-2 border-primary"
                  : "hover:bg-muted/50 border-l-2 border-transparent",
              )}>
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={() => toggleChar(c.name)}
                  className="h-3.5 w-3.5 rounded accent-primary"
                />
                <span className="h-2 w-2 rounded-full flex-shrink-0" style={{ backgroundColor: color }} />
                <button
                  className={cn(
                    "flex-1 text-left text-xs truncate hover:underline",
                    isSelected ? "text-foreground font-semibold" : "text-muted-foreground",
                  )}
                  onClick={() => openEntityCard(c.name, "person")}
                  title={`查看 ${c.name} 档案`}
                >
                  {c.name}
                </button>
                <span className={cn("text-[10px] tabular-nums", isSelected ? "text-foreground/70" : "text-muted-foreground")}>{c.count}</span>
              </div>
            )
          })}
        </div>

        <div className="border-t px-2 py-1.5 text-[10px] text-muted-foreground">
          <p>已选 {selectedChars.length} / 共 {sortedCharacters.length}</p>
          <div className="flex gap-2 mt-1">
            <button className="text-primary hover:underline" onClick={() => setSelectedChars(sortedCharacters.slice(0, 10).map((c) => c.name))}>
              选前10
            </button>
            <button className="text-primary hover:underline" onClick={() => {
              const first = sortedCharacters[0]?.name
              if (first) setSelectedChars([first])
            }}>
              清空
            </button>
          </div>
        </div>
      </div>

      {/* SVG storyline area */}
      <div className="flex-1 flex flex-col overflow-hidden" ref={containerRef}>
        {selectedChars.length === 0 ? (
          <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
            请选择至少一个角色查看其故事线
          </div>
        ) : (
          <>
            {/* Zoom controls */}
            <div className="flex items-center gap-1 border-b px-3 py-1 flex-shrink-0">
              <button onClick={handleZoomIn} className="px-2 py-0.5 rounded text-xs border hover:bg-muted transition" title="放大">＋</button>
              <button onClick={handleZoomOut} className="px-2 py-0.5 rounded text-xs border hover:bg-muted transition" title="缩小">－</button>
              <button onClick={handleZoomReset} className="px-2 py-0.5 rounded text-xs border hover:bg-muted transition" title="重置">1:1</button>
              <span className="text-[10px] text-muted-foreground ml-1">{Math.round(zoomK * 100)}%</span>
              {/* Legend: event type colors */}
              <div className="w-px h-4 bg-border mx-1" />
              {[["战斗","#ef4444"],["成长","#3b82f6"],["社交","#10b981"],["旅行","#f97316"],["关系","#06b6d4"],["其他","#6b7280"]].map(([label, color]) => (
                <span key={label} className="flex items-center gap-0.5">
                  <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
                  <span className="text-[9px] text-muted-foreground">{label}</span>
                </span>
              ))}
              <span className="text-[10px] text-muted-foreground ml-auto">左侧颜色=角色 · 节点颜色=事件类型</span>
            </div>

            <div className="flex-1 overflow-y-auto relative">
              <svg
                ref={svgRef}
                width="100%"
                height={svgHeight}
                className="block cursor-grab"
                onClick={(e) => {
                  const tag = (e.target as SVGElement).tagName
                  if (tag === "svg" || tag === "rect") {
                    setSelectedEvent(null)
                    setTooltip(null)
                  }
                }}
                onMouseMove={(e) => {
                  const svg = svgRef.current
                  if (!svg) return
                  const rect = svg.getBoundingClientRect()
                  const x = e.clientX - rect.left
                  if (x > LABEL_WIDTH) setHoverX(x)
                  else setHoverX(null)
                }}
                onMouseLeave={() => setHoverX(null)}
              >
                {/* Background */}
                <rect width="100%" height={svgHeight} className="fill-background" />

                {/* Chapter axis ticks */}
                {ticks.map((ch) => {
                  const x = zoomedX(ch)
                  if (x < LABEL_WIDTH - 10 || x > (containerRef.current?.clientWidth ?? 2000) + 50) return null
                  return (
                    <g key={`tick-${ch}`}>
                      <line x1={x} y1={AXIS_HEIGHT - 4} x2={x} y2={AXIS_HEIGHT} stroke="currentColor" className="text-muted-foreground" strokeWidth={0.5} />
                      <text x={x} y={AXIS_HEIGHT - 8} textAnchor="middle" className="fill-muted-foreground" fontSize={9}>{ch}</text>
                      <line x1={x} y1={AXIS_HEIGHT} x2={x} y2={AXIS_HEIGHT + selectedChars.length * LANE_HEIGHT} stroke="currentColor" className="text-border" strokeWidth={0.5} opacity={0.15} />
                    </g>
                  )
                })}

                {/* Swimlane rows */}
                {selectedChars.map((char, i) => {
                  const y = AXIS_HEIGHT + i * LANE_HEIGHT
                  const color = charColorMap.get(char) ?? "#6b7280"
                  const midY = y + LANE_HEIGHT / 2
                  const evts = charEvents.get(char) ?? []

                  return (
                    <g key={char}>
                      {/* Alternating row bg */}
                      {i % 2 === 0 && (
                        <rect x={LABEL_WIDTH} y={y} width="100%" height={LANE_HEIGHT} fill="currentColor" className="text-muted-foreground" opacity={0.02} />
                      )}
                      {/* Row divider */}
                      {i > 0 && (
                        <line x1={LABEL_WIDTH} y1={y} x2="100%" y2={y} stroke="currentColor" className="text-border" strokeWidth={0.5} opacity={0.1} />
                      )}
                      {/* Lane baseline */}
                      <line x1={LABEL_WIDTH} y1={midY} x2="100%" y2={midY} stroke={color} strokeWidth={0.5} opacity={0.15} strokeDasharray="6,4" />

                      {/* Event nodes — aggregated per chapter, only visible range */}
                      {(() => {
                        // Group events by chapter for this character
                        const byChapter = new Map<number, TimelineEvent[]>()
                        for (const evt of evts) {
                          if (evt.chapter < visibleRange.min || evt.chapter > visibleRange.max) continue
                          if (!byChapter.has(evt.chapter)) byChapter.set(evt.chapter, [])
                          byChapter.get(evt.chapter)!.push(evt)
                        }

                        const nodes: React.ReactNode[] = []
                        const impRank = (e: TimelineEvent) => (e.is_major ? 4 : e.importance === "high" ? 3 : e.importance === "medium" ? 2 : 1)

                        for (const [ch, chEvts] of byChapter) {
                          const cx = zoomedX(ch)
                          if (cx < LABEL_WIDTH - 5 || cx > (containerRef.current?.clientWidth ?? 2000) + 20) continue

                          // Show best event per chapter (highest importance)
                          const best = chEvts.reduce((a, b) => impRank(a) >= impRank(b) ? a : b)
                          const count = chEvts.length
                          const r = eventRadius(best.importance, best.is_major)
                          const isEvtSelected = selectedEvent?.id === best.id
                          const dimmed = selectedEvent != null && !isEvtSelected

                          nodes.push(
                            <g key={`${ch}-${char}`}>
                              {best.is_major && (
                                <circle cx={cx} cy={midY} r={r + 3} fill="none" stroke={color} strokeWidth={1} opacity={0.4} />
                              )}
                              <circle
                                cx={cx} cy={midY} r={r}
                                fill={eventColor(best.type)}
                                stroke={isEvtSelected ? "#3b82f6" : "none"}
                                strokeWidth={isEvtSelected ? 2 : 0}
                                opacity={dimmed ? 0.2 : 1}
                                className="cursor-pointer"
                                onMouseEnter={(e) => {
                                  setTooltip({
                                    event: { ...best, summary: count > 1 ? `[${count}个事件] ${best.summary}` : best.summary },
                                    x: e.clientX, y: e.clientY,
                                  })
                                }}
                                onMouseLeave={() => setTooltip(null)}
                                onClick={(e) => { e.stopPropagation(); setSelectedEvent(best); setTooltip(null) }}
                              />
                              {/* Count badge for multi-event chapters */}
                              {count > 1 && zoomK >= 3 && (
                                <text x={cx + r + 2} y={midY + 3} fontSize={7} className="fill-muted-foreground pointer-events-none">
                                  {count}
                                </text>
                              )}
                            </g>
                          )
                        }
                        return nodes
                      })()}

                      {/* Fixed label */}
                      <rect x={0} y={y} width={LABEL_WIDTH} height={LANE_HEIGHT} fill={color} opacity={0.05} />
                      <rect x={0} y={y} width={LABEL_WIDTH} height={LANE_HEIGHT} className="fill-background" opacity={0.85} />
                      <text x={4} y={midY} dominantBaseline="central" className="fill-foreground" fontSize={10} fontWeight={500}>
                        {char.length > 4 ? char.slice(0, 4) + "…" : char}
                      </text>
                    </g>
                  )
                })}

                {/* Convergence lines */}
                {convergences.map(({ event: evt, chars: convChars }) => {
                  if (evt.chapter < visibleRange.min || evt.chapter > visibleRange.max) return null
                  const cx = zoomedX(evt.chapter)
                  if (cx < LABEL_WIDTH || cx > (containerRef.current?.clientWidth ?? 2000)) return null

                  const indices = convChars.map((c) => selectedChars.indexOf(c)).filter((i) => i >= 0).sort((a, b) => a - b)
                  if (indices.length < 2) return null
                  const y1 = AXIS_HEIGHT + indices[0] * LANE_HEIGHT + LANE_HEIGHT / 2
                  const y2 = AXIS_HEIGHT + indices[indices.length - 1] * LANE_HEIGHT + LANE_HEIGHT / 2
                  return (
                    <line
                      key={`conv-${evt.id}`}
                      x1={cx} y1={y1} x2={cx} y2={y2}
                      stroke={eventColor(evt.type)} strokeWidth={1} strokeDasharray="3,3" opacity={0.3}
                      className="pointer-events-none"
                    />
                  )
                })}

                {/* Hover crosshair guide */}
                {hoverX != null && (
                  <line
                    x1={hoverX} y1={AXIS_HEIGHT}
                    x2={hoverX} y2={AXIS_HEIGHT + selectedChars.length * LANE_HEIGHT}
                    stroke="currentColor" className="text-muted-foreground pointer-events-none"
                    strokeWidth={0.5} strokeDasharray="4,3" opacity={0.35}
                  />
                )}

                {/* Mini navigation bar — clickable to jump */}
                <g
                  transform={`translate(0,${AXIS_HEIGHT + selectedChars.length * LANE_HEIGHT})`}
                  className="cursor-pointer"
                  onClick={handleMiniNavClick}
                >
                  <rect x={LABEL_WIDTH} y={0} width="100%" height={MINI_NAV_HEIGHT} className="fill-muted" opacity={0.08} />
                  {/* Viewport indicator only (no density bars — too noisy) */}
                  {(() => {
                    const containerW = containerRef.current?.clientWidth ?? 800
                    const contentW = containerW - LABEL_WIDTH
                    const vpFracLeft = Math.max(0, -zoomTx / (contentW * zoomK))
                    const vpFracWidth = Math.min(1, 1 / zoomK)
                    const vpLeft = LABEL_WIDTH + vpFracLeft * contentW
                    const vpWidth = Math.max(8, vpFracWidth * contentW)
                    return <rect x={vpLeft} y={2} width={vpWidth} height={MINI_NAV_HEIGHT - 4} fill="currentColor" className="text-primary" opacity={0.15} rx={2} />
                  })()}
                </g>
              </svg>

              {/* Tooltip — fixed position to avoid clipping */}
              {tooltip && (
                <div
                  className="fixed z-[100] max-w-xs rounded-lg border bg-popover px-3 py-2 text-xs shadow-lg pointer-events-none"
                  style={{
                    left: tooltip.x,
                    top: tooltip.y < 120 ? tooltip.y + 20 : tooltip.y - 10,
                    transform: tooltip.y < 120 ? "translate(-50%, 0)" : "translate(-50%, -100%)",
                  }}
                >
                  <div className="font-medium">
                    第{tooltip.event.chapter}回 · <span style={{ color: eventColor(tooltip.event.type) }}>{tooltip.event.type}</span>
                  </div>
                  <div className="mt-1 text-muted-foreground line-clamp-2">{tooltip.event.summary}</div>
                  <div className="mt-1 text-muted-foreground">
                    👤 {tooltip.event.participants.slice(0, 5).join("、")}
                    {tooltip.event.participants.length > 5 && ` +${tooltip.event.participants.length - 5}`}
                  </div>
                  {tooltip.event.location && <div className="text-muted-foreground">📍 {tooltip.event.location}</div>}
                </div>
              )}
            </div>

            {/* Detail panel */}
            {selectedEvent && (
              <div className="detail-panel flex-shrink-0 border-t bg-card px-4 py-3 animate-in slide-in-from-bottom duration-200" style={{ height: DETAIL_HEIGHT }}>
                <div className="flex items-start justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">📖 第{selectedEvent.chapter}回</span>
                    <span className="text-xs px-1.5 py-0.5 rounded" style={{ backgroundColor: eventColor(selectedEvent.type) + "20", color: eventColor(selectedEvent.type) }}>
                      {selectedEvent.type}
                    </span>
                  </div>
                  <button className="text-muted-foreground hover:text-foreground" onClick={() => setSelectedEvent(null)}>
                    <X className="h-4 w-4" />
                  </button>
                </div>
                <p className="text-sm text-muted-foreground line-clamp-2 mb-2">{selectedEvent.summary}</p>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-xs text-muted-foreground">参与者:</span>
                    {selectedEvent.participants.slice(0, 6).map((p) => (
                      <button
                        key={p}
                        className="text-xs px-1.5 py-0.5 rounded hover:opacity-80 transition"
                        style={{ backgroundColor: (charColorMap.get(p) ?? "#6b7280") + "20", color: charColorMap.get(p) ?? "#6b7280" }}
                        onClick={() => openEntityCard(p, "person")}
                      >{p}</button>
                    ))}
                    {selectedEvent.location && <span className="text-xs text-muted-foreground ml-2">📍 {selectedEvent.location}</span>}
                  </div>
                  <button className="text-xs text-primary hover:underline flex-shrink-0" onClick={() => navigate(novelPath(novelId, "read", `chapter=${selectedEvent.chapter}`))}>
                    前往阅读 →
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 rounded-lg bg-foreground/90 text-background px-4 py-2 text-sm shadow-lg">
          {toast}
        </div>
      )}
    </div>
  )
}
