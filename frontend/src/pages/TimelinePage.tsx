import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useParams } from "react-router-dom"
import { fetchTimelineData } from "@/api/client"
import { useChapterRangeStore } from "@/stores/chapterRangeStore"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { VisualizationLayout } from "@/components/visualization/VisualizationLayout"
import { EntityCardDrawer } from "@/components/entity-cards/EntityCardDrawer"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface TimelineEvent {
  id: string
  chapter: number
  summary: string
  type: string
  importance: string
  participants: string[]
  location: string | null
}

// Color by event type
function eventColor(type: string): string {
  switch (type) {
    case "战斗": return "#ef4444"
    case "成长": return "#3b82f6"
    case "社交": return "#10b981"
    case "旅行": return "#f97316"
    default: return "#6b7280"
  }
}

function importanceSize(importance: string): number {
  switch (importance) {
    case "high": return 8
    case "medium": return 5
    case "low": return 3
    default: return 4
  }
}

type FilterType = "all" | "战斗" | "成长" | "社交" | "旅行" | "其他"

export default function TimelinePage() {
  const { novelId } = useParams<{ novelId: string }>()
  const { chapterStart, chapterEnd, setAnalyzedRange } = useChapterRangeStore()
  const openEntityCard = useEntityCardStore((s) => s.openCard)

  const [events, setEvents] = useState<TimelineEvent[]>([])
  const [swimlanes, setSwimlanes] = useState<Record<string, string[]>>({})
  const [loading, setLoading] = useState(true)

  // Filters
  const [filterType, setFilterType] = useState<FilterType>("all")
  const [filterImportance, setFilterImportance] = useState<"all" | "high" | "medium">("all")
  const [selectedEvent, setSelectedEvent] = useState<TimelineEvent | null>(null)
  const [showSwimlanes, setShowSwimlanes] = useState(false)
  const [selectedPersons, setSelectedPersons] = useState<string[]>([])

  // Load data
  useEffect(() => {
    if (!novelId) return
    let cancelled = false
    setLoading(true)

    fetchTimelineData(novelId, chapterStart, chapterEnd)
      .then((data) => {
        if (cancelled) return
        const range = data.analyzed_range as number[]
        if (range && range[0] > 0) {
          setAnalyzedRange(range[0], range[1])
        }
        setEvents((data.events as TimelineEvent[]) ?? [])
        setSwimlanes((data.swimlanes as Record<string, string[]>) ?? {})
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => { cancelled = true }
  }, [novelId, chapterStart, chapterEnd, setAnalyzedRange])

  // Filtered events
  const filteredEvents = useMemo(() => {
    return events.filter((e) => {
      if (filterType !== "all" && e.type !== filterType) return false
      if (filterImportance === "high" && e.importance !== "high") return false
      if (filterImportance === "medium" && e.importance === "low") return false
      if (selectedPersons.length > 0 && !e.participants.some((p) => selectedPersons.includes(p))) return false
      return true
    })
  }, [events, filterType, filterImportance, selectedPersons])

  // Group events by chapter for display
  const chapterGroups = useMemo(() => {
    const groups = new Map<number, TimelineEvent[]>()
    for (const evt of filteredEvents) {
      if (!groups.has(evt.chapter)) groups.set(evt.chapter, [])
      groups.get(evt.chapter)!.push(evt)
    }
    return Array.from(groups.entries()).sort((a, b) => a[0] - b[0])
  }, [filteredEvents])

  // All persons across swimlanes
  const allPersons = useMemo(
    () => Object.keys(swimlanes).sort((a, b) => (swimlanes[b]?.length ?? 0) - (swimlanes[a]?.length ?? 0)),
    [swimlanes],
  )

  const handlePersonClick = useCallback(
    (name: string) => openEntityCard(name, "person"),
    [openEntityCard],
  )

  const togglePerson = useCallback((person: string) => {
    setSelectedPersons((prev) =>
      prev.includes(person) ? prev.filter((p) => p !== person) : [...prev, person],
    )
  }, [])

  const EVENT_TYPES: FilterType[] = ["all", "战斗", "成长", "社交", "旅行", "其他"]

  return (
    <VisualizationLayout activeTab="timeline">
      <div className="flex h-full flex-col">
        {/* Toolbar */}
        <div className="flex items-center gap-3 border-b px-4 py-2 flex-shrink-0">
          {/* Type filter */}
          <div className="flex items-center gap-1">
            <span className="text-xs text-muted-foreground mr-1">类型</span>
            {EVENT_TYPES.map((t) => (
              <Button
                key={t}
                variant={filterType === t ? "default" : "outline"}
                size="xs"
                onClick={() => setFilterType(t)}
              >
                {t === "all" ? "全部" : t}
              </Button>
            ))}
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

          <div className="flex-1" />

          <span className="text-xs text-muted-foreground">
            {filteredEvents.length} / {events.length} 事件
          </span>

          <Button
            variant={showSwimlanes ? "default" : "outline"}
            size="xs"
            onClick={() => setShowSwimlanes(!showSwimlanes)}
          >
            泳道
          </Button>
        </div>

        <div className="flex flex-1 overflow-hidden">
          {/* Main timeline area */}
          <div className="flex-1 overflow-auto">
            {loading && (
              <div className="flex items-center justify-center h-full">
                <p className="text-muted-foreground">Loading timeline...</p>
              </div>
            )}

            {!loading && events.length === 0 && (
              <div className="flex items-center justify-center h-full">
                <p className="text-muted-foreground">暂无事件数据</p>
              </div>
            )}

            {!loading && chapterGroups.length > 0 && (
              <div className="p-4">
                {/* Legend */}
                <div className="flex items-center gap-4 mb-4 text-[10px] text-muted-foreground">
                  {[
                    { label: "战斗", color: "#ef4444" },
                    { label: "成长", color: "#3b82f6" },
                    { label: "社交", color: "#10b981" },
                    { label: "旅行", color: "#f97316" },
                    { label: "其他", color: "#6b7280" },
                  ].map((item) => (
                    <span key={item.label} className="flex items-center gap-1">
                      <span
                        className="inline-block size-2 rounded-full"
                        style={{ backgroundColor: item.color }}
                      />
                      {item.label}
                    </span>
                  ))}
                  <span className="ml-2">●大=高 ●中=中 ·小=低</span>
                </div>

                {/* Timeline */}
                <div className="relative">
                  {/* Vertical timeline line */}
                  <div className="absolute left-[60px] top-0 bottom-0 w-px bg-border" />

                  {chapterGroups.map(([chapter, evts]) => (
                    <div key={chapter} className="mb-4">
                      {/* Chapter marker */}
                      <div className="flex items-center gap-3 mb-1">
                        <span className="text-xs font-mono text-muted-foreground w-[52px] text-right">
                          Ch.{chapter}
                        </span>
                        <div className="size-2.5 rounded-full bg-border z-10" />
                      </div>

                      {/* Events in this chapter */}
                      <div className="ml-[72px] space-y-1.5">
                        {evts
                          .sort((a, b) => {
                            const imp = { high: 3, medium: 2, low: 1 }
                            return (imp[b.importance as keyof typeof imp] ?? 0) - (imp[a.importance as keyof typeof imp] ?? 0)
                          })
                          .map((evt) => (
                            <div
                              key={evt.id}
                              className={cn(
                                "flex items-start gap-2 p-2 rounded-md border cursor-pointer transition-colors",
                                selectedEvent?.id === evt.id
                                  ? "bg-muted border-primary/50"
                                  : "hover:bg-muted/50",
                              )}
                              onClick={() => setSelectedEvent(selectedEvent?.id === evt.id ? null : evt)}
                            >
                              {/* Event dot */}
                              <span
                                className="rounded-full flex-shrink-0 mt-1"
                                style={{
                                  width: importanceSize(evt.importance) * 2,
                                  height: importanceSize(evt.importance) * 2,
                                  backgroundColor: eventColor(evt.type),
                                }}
                              />

                              <div className="flex-1 min-w-0">
                                <p className="text-sm leading-snug">{evt.summary}</p>

                                {/* Tags */}
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

                                  {evt.importance === "high" && (
                                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-50 text-red-600 dark:bg-red-950/30">
                                      重要
                                    </span>
                                  )}

                                  {evt.location && (
                                    <span className="text-[10px] text-muted-foreground">
                                      @ {evt.location}
                                    </span>
                                  )}
                                </div>

                                {/* Participants (shown on expand) */}
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
                          ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Swimlane sidebar */}
          {showSwimlanes && (
            <div className="w-64 flex-shrink-0 border-l overflow-auto">
              <div className="p-3">
                <h3 className="text-sm font-medium mb-2">人物泳道</h3>
                <p className="text-[10px] text-muted-foreground mb-3">
                  选择人物筛选其相关事件
                </p>

                <div className="space-y-1">
                  {allPersons.map((person) => (
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

        {novelId && <EntityCardDrawer novelId={novelId} />}
      </div>
    </VisualizationLayout>
  )
}
