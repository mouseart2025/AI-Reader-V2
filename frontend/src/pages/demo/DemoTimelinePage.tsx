/**
 * DemoTimelinePage — interactive timeline mirroring the desktop TimelinePage.
 * List view + Storyline view, swimlane sidebar, auto-collapse, importance/type
 * filters, click-through to entity card / map / reading. Data sourced from
 * static demo bundle instead of REST.
 */
import { useCallback, useEffect, useMemo, useRef, useState, lazy, Suspense } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { useVirtualizer } from "@tanstack/react-virtual"
import { useDemoData } from "@/app/DemoContext"
import { useTimelineStore, type FilterType } from "@/stores/timelineStore"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { useVisualizationFocusStore } from "@/stores/visualizationFocusStore"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const StorylineView = lazy(() => import("@/pages/StorylineView"))

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

const TONE_COLORS: Record<string, string> = {
  "紧张": "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300",
  "悲伤": "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300",
  "欢乐": "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300",
  "温馨": "bg-pink-100 text-pink-700 dark:bg-pink-900/30 dark:text-pink-300",
  "愤怒": "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300",
  "平静": "bg-gray-100 text-gray-600 dark:bg-gray-800/30 dark:text-gray-400",
  "神秘": "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300",
  "恐惧": "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300",
  "搞笑": "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300",
}

function importanceSize(importance: string, isMajor?: boolean): number {
  if (isMajor) return 10
  switch (importance) {
    case "high": return 8
    case "medium": return 5
    case "low": return 3
    default: return 4
  }
}

const ALL_CONTENT_TYPES: FilterType[] = ["战斗", "成长", "社交", "旅行", "角色登场", "物品交接", "组织变动", "关系变化", "其他"]
const SMART_DEFAULTS: FilterType[] = ["战斗", "成长", "社交", "旅行", "组织变动", "关系变化", "其他"]
const DEFAULT_HIDDEN: FilterType[] = ["角色登场", "物品交接"]
const EVENT_TYPES: string[] = ["all", ...ALL_CONTENT_TYPES]

export default function DemoTimelinePage() {
  const { novelSlug } = useParams<{ novelSlug: string }>()
  const navigate = useNavigate()
  const { data } = useDemoData()
  const openEntityCard = useEntityCardStore((s) => s.openCard)
  const setFocusLocation = useVisualizationFocusStore((s) => s.setFocusLocation)

  const timelineData = data.timeline as {
    events: TimelineEvent[]
    swimlanes?: Record<string, string[]>
    suggested_min_swimlane?: number
  }
  const events = timelineData.events ?? []
  const swimlanes = timelineData.swimlanes ?? {}

  const {
    filterTypes, setFilterTypes,
    filterImportance, setFilterImportance,
    viewMode, setViewMode,
    autoCollapseLow, setAutoCollapseLow,
    minSwimlaneEvents, setMinSwimlaneEvents,
    scrollTop: savedScrollTop, setScrollTop,
  } = useTimelineStore()

  const [selectedEvent, setSelectedEvent] = useState<TimelineEvent | null>(null)
  const [showSwimlanes, setShowSwimlanes] = useState(false)
  const [selectedPersons, setSelectedPersons] = useState<string[]>([])
  const [collapsedChapters, setCollapsedChapters] = useState<Set<number>>(new Set())

  // Seed swimlane min from backend suggestion on first mount
  useEffect(() => {
    const suggested = timelineData.suggested_min_swimlane
    if (typeof suggested === "number" && suggested > 0) {
      setMinSwimlaneEvents(suggested)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [novelSlug])

  const getNavPath = useCallback(
    (tab: string, query?: string) => {
      const t = tab === "read" ? "reading" : tab
      const base = `/demo/${novelSlug ?? ""}/${t}`
      return query ? `${base}?${query}` : base
    },
    [novelSlug],
  )

  const toggleTypeFilter = useCallback(
    (type: string) => {
      const prev = useTimelineStore.getState().filterTypes
      if (type === "all") {
        const isAll = ALL_CONTENT_TYPES.every((t) => prev.has(t))
        setFilterTypes(isAll ? new Set(SMART_DEFAULTS) : new Set<FilterType>(ALL_CONTENT_TYPES))
        return
      }
      const next = new Set(prev)
      if (next.has(type as FilterType)) {
        next.delete(type as FilterType)
        setFilterTypes(next.size === 0 ? new Set(SMART_DEFAULTS) : next)
      } else {
        next.add(type as FilterType)
        setFilterTypes(next)
      }
    },
    [setFilterTypes],
  )

  const toggleChapterCollapse = useCallback((chapter: number) => {
    setCollapsedChapters((prev) => {
      const next = new Set(prev)
      if (next.has(chapter)) next.delete(chapter)
      else next.add(chapter)
      return next
    })
  }, [])

  const expandAll = useCallback(() => setCollapsedChapters(new Set()), [])

  const filteredEvents = useMemo(() => {
    return events.filter((e) => {
      if (!filterTypes.has(e.type as FilterType)) return false
      if (filterImportance === "high" && e.importance !== "high") return false
      if (filterImportance === "medium" && e.importance === "low") return false
      if (selectedPersons.length > 0 && !e.participants.some((p) => selectedPersons.includes(p))) return false
      return true
    })
  }, [events, filterTypes, filterImportance, selectedPersons])

  const chapterGroups = useMemo(() => {
    const groups = new Map<number, TimelineEvent[]>()
    for (const evt of filteredEvents) {
      if (!groups.has(evt.chapter)) groups.set(evt.chapter, [])
      groups.get(evt.chapter)!.push(evt)
    }
    return Array.from(groups.entries()).sort((a, b) => a[0] - b[0])
  }, [filteredEvents])

  const autoCollapsedChapters = useMemo(() => {
    if (!autoCollapseLow) return new Set<number>()
    const auto = new Set<number>()
    for (const [ch, evts] of chapterGroups) {
      if (evts.every((e) => e.importance === "low" && !e.is_major)) auto.add(ch)
    }
    return auto
  }, [chapterGroups, autoCollapseLow])

  const effectiveCollapsed = useMemo(() => {
    const merged = new Set(collapsedChapters)
    for (const ch of autoCollapsedChapters) merged.add(ch)
    return merged
  }, [collapsedChapters, autoCollapsedChapters])

  const collapseAll = useCallback(
    () => setCollapsedChapters(new Set(chapterGroups.map(([ch]) => ch))),
    [chapterGroups],
  )

  type FlatItem =
    | { kind: "chapter"; chapter: number; eventCount: number; isCollapsed: boolean; isAutoCollapsed: boolean }
    | { kind: "event"; event: TimelineEvent }

  const flatItems = useMemo((): FlatItem[] => {
    const items: FlatItem[] = []
    for (const [chapter, evts] of chapterGroups) {
      const isCollapsed = effectiveCollapsed.has(chapter)
      const isAutoCollapsed = autoCollapsedChapters.has(chapter) && !collapsedChapters.has(chapter)
      items.push({ kind: "chapter", chapter, eventCount: evts.length, isCollapsed, isAutoCollapsed })
      if (!isCollapsed) {
        const sorted = [...evts].sort((a, b) => {
          const imp = { high: 3, medium: 2, low: 1 }
          return (imp[b.importance as keyof typeof imp] ?? 0) - (imp[a.importance as keyof typeof imp] ?? 0)
        })
        for (const evt of sorted) items.push({ kind: "event", event: evt })
      }
    }
    return items
  }, [chapterGroups, effectiveCollapsed, autoCollapsedChapters, collapsedChapters])

  const containerRef = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({
    count: flatItems.length,
    getScrollElement: () => containerRef.current,
    estimateSize: (index) => (flatItems[index].kind === "chapter" ? 32 : 72),
    overscan: 15,
  })

  // Restore scroll on mount, save on unmount
  useEffect(() => {
    if (savedScrollTop > 0 && containerRef.current) {
      requestAnimationFrame(() => {
        if (containerRef.current) containerRef.current.scrollTop = savedScrollTop
      })
    }
    return () => {
      if (containerRef.current) setScrollTop(containerRef.current.scrollTop)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const filteredPersons = useMemo(
    () =>
      Object.keys(swimlanes)
        .filter((p) => (swimlanes[p]?.length ?? 0) >= minSwimlaneEvents)
        .sort((a, b) => (swimlanes[b]?.length ?? 0) - (swimlanes[a]?.length ?? 0)),
    [swimlanes, minSwimlaneEvents],
  )

  const totalPersons = useMemo(() => Object.keys(swimlanes).length, [swimlanes])

  const handlePersonClick = useCallback(
    (name: string) => openEntityCard(name, "person"),
    [openEntityCard],
  )

  const togglePerson = useCallback((person: string) => {
    setSelectedPersons((prev) =>
      prev.includes(person) ? prev.filter((p) => p !== person) : [...prev, person],
    )
  }, [])

  const isAllSelected = useMemo(
    () => ALL_CONTENT_TYPES.every((t) => filterTypes.has(t)),
    [filterTypes],
  )

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-3 border-b px-4 py-2 flex-shrink-0 flex-wrap">
        {/* View mode */}
        <div className="flex items-center gap-1 mr-2">
          <button
            className={cn(
              "px-2.5 py-1 rounded text-xs font-medium transition",
              viewMode === "list"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setViewMode("list")}
          >
            ▤ 事件列表
          </button>
          <button
            className={cn(
              "px-2.5 py-1 rounded text-xs font-medium transition",
              viewMode === "storyline"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setViewMode("storyline")}
          >
            ═ 故事线
          </button>
        </div>

        <div className="w-px h-5 bg-border" />

        {/* Type filter */}
        <div className="flex items-center gap-1 flex-wrap">
          <span className="text-xs text-muted-foreground mr-1">类型</span>
          {EVENT_TYPES.map((t) => {
            const isActive = t === "all" ? isAllSelected : filterTypes.has(t as FilterType)
            const isHiddenDefault = DEFAULT_HIDDEN.includes(t as FilterType)
            return (
              <Button
                key={t}
                variant={isActive ? "default" : "outline"}
                size="xs"
                onClick={() => toggleTypeFilter(t)}
                className={cn(!isActive && isHiddenDefault && "opacity-60")}
              >
                {t === "all" ? "全部" : t}
              </Button>
            )
          })}
        </div>

        <div className="w-px h-5 bg-border" />

        {/* Importance filter */}
        <div className="flex items-center gap-1">
          <span className="text-xs text-muted-foreground mr-1">重要度</span>
          {(["all", "medium", "high"] as const).map((level) => (
            <Button
              key={level}
              variant={filterImportance === level ? "default" : "outline"}
              size="xs"
              onClick={() => setFilterImportance(level)}
            >
              {level === "all" ? "全部" : level === "medium" ? "中+" : "仅高"}
            </Button>
          ))}
        </div>

        <div className="w-px h-5 bg-border" />

        <Button
          variant={autoCollapseLow ? "default" : "outline"}
          size="xs"
          onClick={() => setAutoCollapseLow(!autoCollapseLow)}
          title="自动折叠仅有低重要度事件的章节"
        >
          自动折叠
        </Button>

        <div className="flex-1" />

        <span className="text-xs text-muted-foreground">
          {filteredEvents.length} / {events.length} 事件
        </span>

        <Button variant="outline" size="xs" onClick={collapseAll}>折叠</Button>
        <Button variant="outline" size="xs" onClick={expandAll}>展开</Button>

        <Button
          variant={showSwimlanes ? "default" : "outline"}
          size="xs"
          onClick={() => setShowSwimlanes(!showSwimlanes)}
        >
          泳道
        </Button>
      </div>

      {/* Storyline view */}
      {viewMode === "storyline" && (
        <Suspense fallback={<div className="flex items-center justify-center flex-1 text-muted-foreground text-sm">加载故事线视图...</div>}>
          <StorylineView
            events={filteredEvents}
            swimlanes={swimlanes}
            novelId={novelSlug ?? ""}
            filterTypes={filterTypes}
            onToggleType={toggleTypeFilter}
            getNavPath={getNavPath}
          />
        </Suspense>
      )}

      {/* List view */}
      {viewMode === "list" && (
        <div className="flex flex-1 overflow-hidden">
          <div ref={containerRef} className="flex-1 overflow-auto">
            {events.length === 0 && (
              <div className="flex items-center justify-center h-full">
                <p className="text-muted-foreground">暂无事件数据</p>
              </div>
            )}

            {flatItems.length > 0 && (
              <div className="p-4">
                {/* Legend */}
                <div className="flex items-center gap-3 mb-4 text-[10px] text-muted-foreground flex-wrap">
                  {[
                    { label: "战斗", color: "#ef4444" },
                    { label: "成长", color: "#3b82f6" },
                    { label: "社交", color: "#10b981" },
                    { label: "旅行", color: "#f97316" },
                    { label: "关系变化", color: "#06b6d4" },
                    { label: "角色登场", color: "#8b5cf6" },
                    { label: "物品交接", color: "#eab308" },
                    { label: "组织变动", color: "#ec4899" },
                    { label: "其他", color: "#6b7280" },
                  ].map((item) => (
                    <span key={item.label} className="flex items-center gap-1">
                      <span className="inline-block size-2 rounded-full" style={{ backgroundColor: item.color }} />
                      {item.label}
                    </span>
                  ))}
                  <span className="ml-2">●大=关键 ●中=中 ·小=低</span>
                </div>

                {/* Virtualized list */}
                <div className="relative" style={{ height: `${virtualizer.getTotalSize()}px` }}>
                  <div className="absolute left-[60px] top-0 bottom-0 w-px bg-border" />
                  {virtualizer.getVirtualItems().map((virtualRow) => {
                    const item = flatItems[virtualRow.index]
                    if (item.kind === "chapter") {
                      return (
                        <div
                          key={virtualRow.key}
                          style={{
                            position: "absolute",
                            top: 0,
                            left: 0,
                            width: "100%",
                            transform: `translateY(${virtualRow.start}px)`,
                          }}
                          className="flex items-center gap-3 py-1 cursor-pointer select-none"
                          onClick={() => toggleChapterCollapse(item.chapter)}
                        >
                          <span className="text-xs font-mono text-muted-foreground w-[52px] text-right">
                            Ch.{item.chapter}
                          </span>
                          <div className="size-2.5 rounded-full bg-border z-10" />
                          <span className="text-[10px] text-muted-foreground">
                            {item.eventCount} 事件 {item.isCollapsed ? "▸" : "▾"}
                            {item.isAutoCollapsed && (
                              <span className="ml-1 text-yellow-600 dark:text-yellow-400">(低)</span>
                            )}
                          </span>
                        </div>
                      )
                    }
                    const evt = item.event
                    return (
                      <div
                        key={virtualRow.key}
                        style={{
                          position: "absolute",
                          top: 0,
                          left: 0,
                          width: "100%",
                          transform: `translateY(${virtualRow.start}px)`,
                          paddingLeft: "72px",
                        }}
                        className="pr-4 pb-1.5"
                      >
                        <div
                          className={cn(
                            "flex items-start gap-2 p-2 rounded-md border cursor-pointer transition-colors",
                            selectedEvent?.id === evt.id
                              ? "bg-muted border-primary/50"
                              : "hover:bg-muted/50",
                          )}
                          onClick={() => setSelectedEvent(selectedEvent?.id === evt.id ? null : evt)}
                        >
                          <span
                            className={cn(
                              "rounded-full flex-shrink-0 mt-1",
                              evt.is_major && "ring-2 ring-offset-1 ring-primary/40",
                            )}
                            style={{
                              width: importanceSize(evt.importance, evt.is_major) * 2,
                              height: importanceSize(evt.importance, evt.is_major) * 2,
                              backgroundColor: eventColor(evt.type),
                            }}
                          />
                          <div className="flex-1 min-w-0">
                            <p className="text-sm leading-snug">{evt.summary}</p>
                            <div className="flex items-center gap-2 mt-1 flex-wrap">
                              <span
                                className="text-[10px] px-1.5 py-0.5 rounded"
                                style={{
                                  backgroundColor: eventColor(evt.type) + "20",
                                  color: eventColor(evt.type),
                                }}
                              >
                                {evt.type}
                              </span>
                              {evt.is_major && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-50 text-purple-600 dark:bg-purple-950/30">
                                  关键
                                </span>
                              )}
                              {!evt.is_major && evt.importance === "high" && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-50 text-red-600 dark:bg-red-950/30">
                                  重要
                                </span>
                              )}
                              {evt.emotional_tone && (
                                <span className={cn(
                                  "text-[10px] px-1.5 py-0.5 rounded",
                                  TONE_COLORS[evt.emotional_tone] ?? "bg-muted text-muted-foreground",
                                )}>
                                  {evt.emotional_tone}
                                </span>
                              )}
                              {evt.location && (
                                <button
                                  className="text-[10px] text-green-600 dark:text-green-400 hover:underline"
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    setFocusLocation(evt.location!, "timeline")
                                    if (novelSlug) navigate(getNavPath("map"))
                                  }}
                                >
                                  📍 {evt.location}
                                </button>
                              )}
                            </div>
                            {selectedEvent?.id === evt.id && evt.participants.length > 0 && (
                              <div className="mt-2 flex items-center gap-1.5 flex-wrap">
                                <span className="text-[10px] text-muted-foreground">参与者:</span>
                                {evt.participants.map((p) => (
                                  <button
                                    key={p}
                                    className="text-[10px] text-blue-600 hover:underline"
                                    onClick={(e) => { e.stopPropagation(); handlePersonClick(p) }}
                                  >
                                    {p}
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </div>

          {/* Swimlane sidebar */}
          {showSwimlanes && (
            <div className="w-64 flex-shrink-0 border-l overflow-auto">
              <div className="p-3">
                <h3 className="text-sm font-medium mb-2">人物泳道</h3>
                <p className="text-[10px] text-muted-foreground mb-2">
                  选择人物筛选其相关事件
                </p>

                <div className="flex items-center gap-2 mb-3">
                  <span className="text-[10px] text-muted-foreground">最少事件</span>
                  <div className="flex items-center gap-1">
                    {[1, 3, 5, 10].map((n) => (
                      <Button
                        key={n}
                        variant={minSwimlaneEvents === n ? "default" : "outline"}
                        size="xs"
                        onClick={() => setMinSwimlaneEvents(n)}
                      >
                        {n}
                      </Button>
                    ))}
                  </div>
                </div>

                <p className="text-[10px] text-muted-foreground mb-2">
                  {filteredPersons.length} / {totalPersons} 人物
                </p>

                <div className="space-y-1">
                  {filteredPersons.map((person) => (
                    <button
                      key={person}
                      className={cn(
                        "w-full text-left text-xs px-2 py-1.5 rounded-md hover:bg-muted/50 transition-colors flex items-center justify-between",
                        selectedPersons.includes(person) && "bg-primary/10 text-primary font-medium",
                      )}
                      onClick={() => togglePerson(person)}
                    >
                      <span>{person}</span>
                      <span className="text-muted-foreground">
                        {swimlanes[person]?.length ?? 0}
                      </span>
                    </button>
                  ))}
                </div>

                {selectedPersons.length > 0 && (
                  <Button
                    variant="ghost"
                    size="xs"
                    className="mt-2 w-full"
                    onClick={() => setSelectedPersons([])}
                  >
                    清除筛选
                  </Button>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
