import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useParams } from "react-router-dom"
import { useVirtualizer } from "@tanstack/react-virtual"
import { fetchTimelineData } from "@/api/client"
import { useNavigate } from "react-router-dom"
import { useChapterRangeStore } from "@/stores/chapterRangeStore"
import { useTimelineStore, type FilterType } from "@/stores/timelineStore"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { useVisualizationFocusStore } from "@/stores/visualizationFocusStore"
import { novelPath } from "@/lib/novelPaths"
import { VisualizationLayout } from "@/components/visualization/VisualizationLayout"
import { EntityCardDrawer } from "@/components/entity-cards/EntityCardDrawer"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { trackEvent } from "@/lib/tracker"
import { recordTabVisit } from "@/lib/tabTracking"
import { useI18n } from "@/i18n"
import { lazy, Suspense } from "react"
import {
  TIMELINE_EVENT_TYPE_IDS,
  sceneToneLabel,
  sceneToneStyle,
  timelineEventColor,
  timelineEventTypeId,
  timelineEventTypeLabel,
} from "@/lib/domainLabels"
import { timelineEventSummary } from "@/lib/timelineSummary"

const StorylineView = lazy(() => import("./StorylineView"))

interface TimelineEvent {
  id: string
  chapter: number
  summary: string
  type: string
  type_id?: string
  importance: string
  participants: string[]
  location: string | null
  is_major?: boolean
  emotional_tone?: string | null
  emotional_tone_id?: string | null
  summary_template_id?: string | null
  summary_args?: Record<string, unknown> | null
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

// FilterType imported from timelineStore (without "all" — "all" is UI-only toggle)
const DEFAULT_HIDDEN: FilterType[] = ["character_appearance", "item_transfer"]

export default function TimelinePage() {
  const { t } = useI18n()
  const { novelId } = useParams<{ novelId: string }>()
  const navigate = useNavigate()
  const { chapterStart, chapterEnd, setAnalyzedRange } = useChapterRangeStore()
  const openEntityCard = useEntityCardStore((s) => s.openCard)
  const setFocusLocation = useVisualizationFocusStore((s) => s.setFocusLocation)

  const [events, setEvents] = useState<TimelineEvent[]>([])
  const [swimlanes, setSwimlanes] = useState<Record<string, string[]>>({})
  const [loading, setLoading] = useState(true)
  const [, setSuggestedMinSwimlane] = useState(1)

  // Filters — persisted in store across page navigations
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

  const ALL_CONTENT_TYPES: FilterType[] = [...TIMELINE_EVENT_TYPE_IDS]
  const SMART_DEFAULTS = new Set<FilterType>(["battle", "growth", "social", "travel", "org_change", "relation_change", "other"])

  const toggleTypeFilter = useCallback((type: string) => {
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
  }, [setFilterTypes])

  const toggleChapterCollapse = useCallback((chapter: number) => {
    setCollapsedChapters((prev) => {
      const next = new Set(prev)
      if (next.has(chapter)) next.delete(chapter)
      else next.add(chapter)
      return next
    })
  }, [])

  const expandAll = useCallback(() => {
    setCollapsedChapters(new Set())
  }, [])

  useEffect(() => { recordTabVisit("timeline") }, [])

  // Load data
  useEffect(() => {
    if (!novelId) return
    let cancelled = false
    setLoading(true)
    trackEvent("view_timeline")

    fetchTimelineData(novelId, chapterStart, chapterEnd)
      .then((data) => {
        if (cancelled) return
        const range = data.analyzed_range as number[]
        if (range && range[0] > 0) {
          setAnalyzedRange(range[0], range[1])
        }
        setEvents((data.events as TimelineEvent[]) ?? [])
        setSwimlanes((data.swimlanes as Record<string, string[]>) ?? {})
        const minSl = (data.suggested_min_swimlane as number) ?? 5
        setSuggestedMinSwimlane(minSl)
        setMinSwimlaneEvents(minSl)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => { cancelled = true }
  }, [novelId, chapterStart, chapterEnd, setAnalyzedRange])

  // Filtered events
  const filteredEvents = useMemo(() => {
    return events.filter((e) => {
      if (!filterTypes.has(timelineEventTypeId(e.type_id, e.type) as FilterType)) return false
      if (filterImportance === "high" && e.importance !== "high") return false
      if (filterImportance === "medium" && e.importance === "low") return false
      if (selectedPersons.length > 0 && !e.participants.some((p) => selectedPersons.includes(p))) return false
      return true
    })
  }, [events, filterTypes, filterImportance, selectedPersons])

  // Group events by chapter for display
  const chapterGroups = useMemo(() => {
    const groups = new Map<number, TimelineEvent[]>()
    for (const evt of filteredEvents) {
      if (!groups.has(evt.chapter)) groups.set(evt.chapter, [])
      groups.get(evt.chapter)!.push(evt)
    }
    return Array.from(groups.entries()).sort((a, b) => a[0] - b[0])
  }, [filteredEvents])

  // Auto-collapse chapters that only have low-importance events
  const autoCollapsedChapters = useMemo(() => {
    if (!autoCollapseLow) return new Set<number>()
    const auto = new Set<number>()
    for (const [ch, evts] of chapterGroups) {
      if (evts.every((e) => e.importance === "low" && !e.is_major)) {
        auto.add(ch)
      }
    }
    return auto
  }, [chapterGroups, autoCollapseLow])

  const effectiveCollapsed = useMemo(() => {
    const merged = new Set(collapsedChapters)
    for (const ch of autoCollapsedChapters) merged.add(ch)
    return merged
  }, [collapsedChapters, autoCollapsedChapters])

  const collapseAll = useCallback(() => {
    setCollapsedChapters(new Set(chapterGroups.map(([ch]) => ch)))
  }, [chapterGroups])

  // Flatten chapter groups into virtual list items
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
        for (const evt of sorted) {
          items.push({ kind: "event", event: evt })
        }
      }
    }
    return items
  }, [chapterGroups, effectiveCollapsed, autoCollapsedChapters, collapsedChapters])

  const timelineContainerRef = useRef<HTMLDivElement>(null)
  const timelineVirtualizer = useVirtualizer({
    count: flatItems.length,
    getScrollElement: () => timelineContainerRef.current,
    estimateSize: (index) => (flatItems[index].kind === "chapter" ? 32 : 72),
    overscan: 15,
  })

  // Restore scroll position on mount, save on unmount
  useEffect(() => {
    if (savedScrollTop > 0 && timelineContainerRef.current) {
      requestAnimationFrame(() => {
        if (timelineContainerRef.current) {
          timelineContainerRef.current.scrollTop = savedScrollTop
        }
      })
    }
    return () => {
      if (timelineContainerRef.current) {
        setScrollTop(timelineContainerRef.current.scrollTop)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Filtered swimlane persons (above min threshold)
  const filteredPersons = useMemo(
    () => Object.keys(swimlanes)
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

  const EVENT_TYPES: string[] = ["all", ...TIMELINE_EVENT_TYPE_IDS]

  const isAllSelected = useMemo(() => {
    return ALL_CONTENT_TYPES.every((t) => filterTypes.has(t))
  }, [filterTypes])

  return (
    <VisualizationLayout>
      <div className="flex h-full flex-col">
        {/* Toolbar */}
        <div className="flex items-center gap-3 border-b px-4 py-2 flex-shrink-0">
          {/* View mode tabs */}
          <div className="flex items-center gap-1 mr-2">
            <button
              className={cn(
                "px-2.5 py-1 rounded text-xs font-medium transition",
                viewMode === "list"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              )}
              onClick={() => setViewMode("list")}
            >
              ▤ {t("timeline.view.list")}
            </button>
            <button
              className={cn(
                "px-2.5 py-1 rounded text-xs font-medium transition",
                viewMode === "storyline"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              )}
              onClick={() => setViewMode("storyline")}
            >
              ═ {t("timeline.view.storyline")}
            </button>
          </div>

          <div className="w-px h-5 bg-border" />

          {/* Type filter (multi-select) */}
          <div className="flex items-center gap-1 flex-wrap">
            <span className="text-xs text-muted-foreground mr-1">{t("timeline.filter.type")}</span>
            {EVENT_TYPES.map((type) => {
              const isActive = type === "all" ? isAllSelected : filterTypes.has(type as FilterType)
              const isHiddenDefault = DEFAULT_HIDDEN.includes(type as FilterType)
              return (
                <Button
                  key={type}
                  variant={isActive ? "default" : "outline"}
                  size="xs"
                  onClick={() => toggleTypeFilter(type)}
                  className={cn(!isActive && isHiddenDefault && "opacity-60")}
                >
                  {type === "all" ? t("common.all") : timelineEventTypeLabel(t, type)}
                </Button>
              )
            })}
          </div>

          <div className="w-px h-5 bg-border" />

          {/* Importance filter */}
          <div className="flex items-center gap-1">
            <span className="text-xs text-muted-foreground mr-1">{t("timeline.filter.importance")}</span>
            {(["all", "medium", "high"] as const).map((level) => (
              <Button
                key={level}
                variant={filterImportance === level ? "default" : "outline"}
                size="xs"
                onClick={() => setFilterImportance(level)}
              >
                {level === "all"
                  ? t("common.all")
                  : level === "medium"
                    ? t("timeline.importance.mediumPlus")
                    : t("timeline.importance.highOnly")}
              </Button>
            ))}
          </div>

          <div className="w-px h-5 bg-border" />

          {/* Auto-collapse toggle */}
          <Button
            variant={autoCollapseLow ? "default" : "outline"}
            size="xs"
            onClick={() => setAutoCollapseLow(!autoCollapseLow)}
            title={t("timeline.autoCollapseTitle")}
          >
            {t("timeline.autoCollapse")}
          </Button>

          <div className="flex-1" />

          <span className="text-xs text-muted-foreground">
            {t("timeline.eventCount", { shown: filteredEvents.length, total: events.length })}
          </span>

          <Button variant="outline" size="xs" onClick={collapseAll}>
            {t("common.collapse")}
          </Button>
          <Button variant="outline" size="xs" onClick={expandAll}>
            {t("common.expand")}
          </Button>

          <Button
            variant={showSwimlanes ? "default" : "outline"}
            size="xs"
            onClick={() => setShowSwimlanes(!showSwimlanes)}
          >
            {t("timeline.swimlanes")}
          </Button>
        </div>

        {/* Storyline view */}
        {viewMode === "storyline" && (
          <Suspense fallback={<div className="flex items-center justify-center flex-1 text-muted-foreground text-sm">{t("timeline.storylineLoading")}</div>}>
            <StorylineView
              events={filteredEvents}
              swimlanes={swimlanes}
              novelId={novelId ?? ""}
              filterTypes={filterTypes}
              onToggleType={toggleTypeFilter}
            />
          </Suspense>
        )}

        {/* List view */}
        {viewMode === "list" && <div className="flex flex-1 overflow-hidden">
          {/* Main timeline area */}
          <div ref={timelineContainerRef} className="flex-1 overflow-auto">
            {loading && (
              <div className="flex items-center justify-center h-full">
                <p className="text-muted-foreground">{t("timeline.loading")}</p>
              </div>
            )}

            {!loading && events.length === 0 && (
              <div className="flex items-center justify-center h-full">
                <p className="text-muted-foreground">{t("timeline.empty")}</p>
              </div>
            )}

            {!loading && flatItems.length > 0 && (
              <div className="p-4">
                {/* Legend */}
                <div className="flex items-center gap-3 mb-4 text-[10px] text-muted-foreground flex-wrap">
                  {TIMELINE_EVENT_TYPE_IDS.map((type) => (
                    <span key={type} className="flex items-center gap-1">
                      <span
                        className="inline-block size-2 rounded-full"
                        style={{ backgroundColor: timelineEventColor(type) }}
                      />
                      {timelineEventTypeLabel(t, type)}
                    </span>
                  ))}
                  <span className="ml-2">{t("timeline.legend.sizeHint")}</span>
                </div>

                {/* Virtualized Timeline */}
                <div
                  className="relative"
                  style={{ height: `${timelineVirtualizer.getTotalSize()}px` }}
                >
                  {/* Vertical timeline line */}
                  <div className="absolute left-[60px] top-0 bottom-0 w-px bg-border" />

                  {timelineVirtualizer.getVirtualItems().map((virtualRow) => {
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
                            {t("common.chapterShort", { chapter: item.chapter })}
                          </span>
                          <div className="size-2.5 rounded-full bg-border z-10" />
                          <span className="text-[10px] text-muted-foreground">
                            {t("timeline.chapterEventCount", { count: item.eventCount })} {item.isCollapsed ? "▸" : "▾"}
                            {item.isAutoCollapsed && (
                              <span className="ml-1 text-yellow-600 dark:text-yellow-400">{t("timeline.lowShort")}</span>
                            )}
                          </span>
                        </div>
                      )
                    }
                    const evt = item.event
                    const summary = timelineEventSummary(t, evt)
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
                              backgroundColor: timelineEventColor(evt.type_id, evt.type),
                            }}
                          />
                          <div className="flex-1 min-w-0">
                            <p className="text-sm leading-snug">{summary}</p>
                            <div className="flex items-center gap-2 mt-1 flex-wrap">
                              <span
                                className="text-[10px] px-1.5 py-0.5 rounded"
                                style={{
                                  backgroundColor: timelineEventColor(evt.type_id, evt.type) + "20",
                                  color: timelineEventColor(evt.type_id, evt.type),
                                }}
                              >
                                {timelineEventTypeLabel(t, evt.type_id, evt.type)}
                              </span>
                              {evt.is_major && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-50 text-purple-600 dark:bg-purple-950/30">
                                  {t("timeline.importance.key")}
                                </span>
                              )}
                              {!evt.is_major && evt.importance === "high" && (
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-50 text-red-600 dark:bg-red-950/30">
                                  {t("timeline.importance.important")}
                                </span>
                              )}
                              {evt.emotional_tone && (
                                <span className={cn(
                                  "text-[10px] px-1.5 py-0.5 rounded",
                                  sceneToneStyle(evt.emotional_tone_id, evt.emotional_tone),
                                )}>
                                  {sceneToneLabel(t, evt.emotional_tone_id, evt.emotional_tone)}
                                </span>
                              )}
                              {evt.location && (
                                <button
                                  className="text-[10px] text-green-600 dark:text-green-400 hover:underline"
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    setFocusLocation(evt.location!, "timeline")
                                    if (novelId) navigate(novelPath(novelId, "map"))
                                  }}
                                >
                                  📍 {evt.location}
                                </button>
                              )}
                            </div>
                            {selectedEvent?.id === evt.id && evt.participants.length > 0 && (
                              <div className="mt-2 flex items-center gap-1.5 flex-wrap">
                                <span className="text-[10px] text-muted-foreground">{t("timeline.participants")}</span>
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
                <h3 className="text-sm font-medium mb-2">{t("timeline.swimlanes.title")}</h3>
                <p className="text-[10px] text-muted-foreground mb-2">
                  {t("timeline.swimlanes.description")}
                </p>

                {/* Min event threshold */}
                <div className="flex items-center gap-2 mb-3">
                  <span className="text-[10px] text-muted-foreground">{t("timeline.swimlanes.minEvents")}</span>
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
                  {t("timeline.swimlanes.personCount", { shown: filteredPersons.length, total: totalPersons })}
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
                    {t("timeline.clearFilter")}
                  </Button>
                )}
              </div>
            </div>
          )}
        </div>}

        {novelId && <EntityCardDrawer novelId={novelId} />}
      </div>
    </VisualizationLayout>
  )
}
