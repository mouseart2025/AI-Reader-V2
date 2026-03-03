/**
 * DemoTimelinePage — interactive timeline with type filtering and virtual scrolling.
 * Uses static demo data for events and swimlanes.
 */
import { useMemo, useRef, useState } from "react"
import { useVirtualizer } from "@tanstack/react-virtual"
import { useDemoData } from "@/app/DemoContext"

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

const EVENT_TYPE_COLORS: Record<string, string> = {
  "战斗": "#ef4444", "成长": "#3b82f6", "社交": "#10b981", "旅行": "#f97316",
  "关系变化": "#06b6d4", "角色登场": "#8b5cf6", "物品交接": "#eab308",
  "组织变动": "#ec4899", "其他": "#6b7280",
}

const TONE_COLORS: Record<string, string> = {
  "紧张": "#ef4444", "悲伤": "#3b82f6", "欢乐": "#f59e0b",
  "恐惧": "#7c3aed", "愤怒": "#dc2626", "感动": "#ec4899",
  "平静": "#6b7280", "神秘": "#8b5cf6",
}

const ALL_TYPES = ["战斗", "成长", "社交", "旅行", "关系变化", "角色登场", "物品交接", "组织变动", "其他"]

type FlatItem =
  | { kind: "chapter"; chapter: number; eventCount: number }
  | { kind: "event"; event: TimelineEvent }

export default function DemoTimelinePage() {
  const { data } = useDemoData()
  const timelineData = data.timeline as {
    events: TimelineEvent[]
    swimlanes?: Record<string, string[]>
    suggested_hidden_types?: string[]
  }

  const events = timelineData.events ?? []

  // Default hidden types from backend suggestion
  const defaultHidden = useMemo(
    () => new Set(timelineData.suggested_hidden_types ?? ["角色登场", "物品交接"]),
    [timelineData],
  )

  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(defaultHidden)
  const [importanceFilter, setImportanceFilter] = useState<"all" | "medium" | "high">("all")
  const containerRef = useRef<HTMLDivElement>(null)

  const toggleType = (type: string) => {
    setHiddenTypes((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }

  // Filter and group events
  const flatItems = useMemo(() => {
    const filtered = events.filter((e) => {
      if (hiddenTypes.has(e.type)) return false
      if (importanceFilter === "high" && e.importance !== "high") return false
      if (importanceFilter === "medium" && e.importance === "low") return false
      return true
    })

    // Group by chapter
    const byChapter = new Map<number, TimelineEvent[]>()
    for (const e of filtered) {
      const list = byChapter.get(e.chapter) ?? []
      list.push(e)
      byChapter.set(e.chapter, list)
    }

    const items: FlatItem[] = []
    const chapters = [...byChapter.keys()].sort((a, b) => a - b)
    for (const ch of chapters) {
      const chEvents = byChapter.get(ch)!
      items.push({ kind: "chapter", chapter: ch, eventCount: chEvents.length })
      // Sort: high importance first
      const sorted = chEvents.sort((a, b) => {
        const order: Record<string, number> = { high: 0, medium: 1, low: 2 }
        return (order[a.importance] ?? 2) - (order[b.importance] ?? 2)
      })
      for (const e of sorted) {
        items.push({ kind: "event", event: e })
      }
    }
    return items
  }, [events, hiddenTypes, importanceFilter])

  const virtualizer = useVirtualizer({
    count: flatItems.length,
    getScrollElement: () => containerRef.current,
    estimateSize: (i) => (flatItems[i].kind === "chapter" ? 36 : 80),
    overscan: 20,
  })

  return (
    <div className="flex h-full flex-col">
      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-2 border-b bg-white/80 px-4 py-2">
        <span className="text-xs text-gray-500">
          {flatItems.filter((i) => i.kind === "event").length} 事件
        </span>
        <div className="flex gap-1">
          {ALL_TYPES.map((type) => (
            <button
              key={type}
              onClick={() => toggleType(type)}
              className="rounded px-2 py-0.5 text-xs font-medium transition"
              style={{
                backgroundColor: hiddenTypes.has(type) ? "#f3f4f6" : EVENT_TYPE_COLORS[type] + "15",
                color: hiddenTypes.has(type) ? "#9ca3af" : EVENT_TYPE_COLORS[type],
                border: `1px solid ${hiddenTypes.has(type) ? "#e5e7eb" : EVENT_TYPE_COLORS[type] + "30"}`,
              }}
            >
              {type}
            </button>
          ))}
        </div>
        <div className="flex gap-1 border-l pl-2">
          {(["all", "medium", "high"] as const).map((level) => (
            <button
              key={level}
              onClick={() => setImportanceFilter(level)}
              className={`rounded px-2 py-0.5 text-xs transition ${
                importanceFilter === level
                  ? "bg-blue-50 text-blue-700 border border-blue-200"
                  : "text-gray-400 hover:text-gray-600"
              }`}
            >
              {level === "all" ? "全部" : level === "medium" ? "中+" : "仅高"}
            </button>
          ))}
        </div>
      </div>

      {/* Virtual timeline */}
      <div ref={containerRef} className="flex-1 overflow-auto">
        <div
          style={{ height: virtualizer.getTotalSize(), position: "relative" }}
          className="mx-auto max-w-3xl px-4"
        >
          {/* Vertical line */}
          <div className="absolute left-[60px] top-0 bottom-0 w-px bg-gray-200" />

          {virtualizer.getVirtualItems().map((virtualItem) => {
            const item = flatItems[virtualItem.index]
            return (
              <div
                key={virtualItem.key}
                style={{
                  position: "absolute",
                  top: virtualItem.start,
                  height: virtualItem.size,
                  left: 0,
                  right: 0,
                }}
              >
                {item.kind === "chapter" ? (
                  <div className="flex items-center gap-2 py-1">
                    <span className="w-[52px] text-right text-xs font-semibold text-gray-700">
                      第{item.chapter}回
                    </span>
                    <div className="h-2.5 w-2.5 rounded-full border-2 border-gray-400 bg-white" />
                    <span className="text-xs text-gray-400">{item.eventCount} 事件</span>
                  </div>
                ) : (
                  <div className="ml-[72px] mr-2 rounded-lg border bg-white p-2.5 shadow-sm">
                    <div className="flex items-start gap-2">
                      <span
                        className="mt-0.5 inline-block h-2.5 w-2.5 flex-shrink-0 rounded-full"
                        style={{ backgroundColor: EVENT_TYPE_COLORS[item.event.type] ?? "#6b7280" }}
                      />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm leading-snug text-gray-800">{item.event.summary}</p>
                        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-400">
                          <span
                            className="rounded px-1.5 py-0.5"
                            style={{
                              backgroundColor: (EVENT_TYPE_COLORS[item.event.type] ?? "#6b7280") + "15",
                              color: EVENT_TYPE_COLORS[item.event.type] ?? "#6b7280",
                            }}
                          >
                            {item.event.type}
                          </span>
                          {item.event.is_major && (
                            <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-600">重要</span>
                          )}
                          {item.event.emotional_tone && (
                            <span
                              className="rounded px-1.5 py-0.5"
                              style={{
                                backgroundColor: (TONE_COLORS[item.event.emotional_tone] ?? "#6b7280") + "15",
                                color: TONE_COLORS[item.event.emotional_tone] ?? "#6b7280",
                              }}
                            >
                              {item.event.emotional_tone}
                            </span>
                          )}
                          {item.event.location && <span>{item.event.location}</span>}
                          {item.event.participants.length > 0 && (
                            <span className="truncate">{item.event.participants.slice(0, 3).join("、")}</span>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
