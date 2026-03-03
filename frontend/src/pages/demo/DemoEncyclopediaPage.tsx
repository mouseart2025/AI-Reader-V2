/**
 * DemoEncyclopediaPage — searchable/filterable encyclopedia using static demo data.
 * Supports category tabs, text search, and virtual scrolling.
 */
import { useMemo, useRef, useState } from "react"
import { useVirtualizer } from "@tanstack/react-virtual"
import { useDemoData } from "@/app/DemoContext"
import { useEntityCardStore } from "@/stores/entityCardStore"
import type { EntityType } from "@/api/types"

interface EncEntry {
  name: string
  type: string
  category: string
  definition: string
  first_chapter: number
  chapter_count?: number
  parent?: string | null
  depth?: number
  tier?: string
  icon?: string
}

const TYPE_COLORS: Record<string, string> = {
  person: "#3b82f6", location: "#10b981", item: "#f59e0b",
  org: "#8b5cf6", concept: "#6b7280",
}

const TYPE_LABELS: Record<string, string> = {
  person: "人物", location: "地点", item: "物品",
  org: "组织", concept: "概念",
}

const TIER_COLORS: Record<string, string> = {
  world: "#ef4444", continent: "#f97316", kingdom: "#f59e0b",
  region: "#eab308", city: "#3b82f6", site: "#10b981", building: "#64748b",
}

export default function DemoEncyclopediaPage() {
  const { data } = useDemoData()
  const encyclopediaData = data.encyclopedia as { entries: EncEntry[] }
  void data.encyclopediaStats // stats available for future use

  const entries = encyclopediaData?.entries ?? []

  const [activeType, setActiveType] = useState<string | null>(null)
  const [search, setSearch] = useState("")
  const [sortBy, setSortBy] = useState<"name" | "chapter" | "mentions">("name")
  const [selectedEntry, setSelectedEntry] = useState<EncEntry | null>(null)
  const openCard = useEntityCardStore((s) => s.openCard)
  const listRef = useRef<HTMLDivElement>(null)

  // Category counts
  const typeCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const e of entries) {
      counts[e.type] = (counts[e.type] ?? 0) + 1
    }
    return counts
  }, [entries])

  // Filter + sort
  const filteredEntries = useMemo(() => {
    let filtered = entries
    if (activeType) {
      filtered = filtered.filter((e) => e.type === activeType)
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      filtered = filtered.filter(
        (e) => e.name.toLowerCase().includes(q) || e.definition?.toLowerCase().includes(q),
      )
    }
    const sorted = [...filtered]
    if (sortBy === "name") sorted.sort((a, b) => a.name.localeCompare(b.name, "zh"))
    else if (sortBy === "chapter") sorted.sort((a, b) => a.first_chapter - b.first_chapter)
    else if (sortBy === "mentions") sorted.sort((a, b) => (b.chapter_count ?? 0) - (a.chapter_count ?? 0))
    return sorted
  }, [entries, activeType, search, sortBy])

  const virtualizer = useVirtualizer({
    count: filteredEntries.length,
    getScrollElement: () => listRef.current,
    estimateSize: () => 72,
    overscan: 15,
  })

  return (
    <div className="flex h-full">
      {/* Category Sidebar — hidden on mobile */}
      <div className="hidden w-44 flex-shrink-0 overflow-y-auto border-r bg-gray-50 p-2 sm:block">
        <button
          onClick={() => setActiveType(null)}
          className={`mb-1 w-full rounded-md px-3 py-2 text-left text-sm transition ${
            activeType === null ? "bg-blue-50 font-medium text-blue-700" : "text-gray-600 hover:bg-gray-100"
          }`}
        >
          全部 <span className="text-xs text-gray-400">({entries.length})</span>
        </button>
        {Object.entries(TYPE_LABELS).map(([type, label]) => (
          <button
            key={type}
            onClick={() => setActiveType(type)}
            className={`mb-0.5 w-full rounded-md px-3 py-1.5 text-left text-sm transition ${
              activeType === type ? "bg-blue-50 font-medium text-blue-700" : "text-gray-600 hover:bg-gray-100"
            }`}
          >
            <span
              className="mr-1.5 inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: TYPE_COLORS[type] }}
            />
            {label}{" "}
            <span className="text-xs text-gray-400">({typeCounts[type] ?? 0})</span>
          </button>
        ))}
      </div>

      {/* Main Content */}
      <div className="flex flex-1 flex-col">
        {/* Search + Sort bar */}
        <div className="flex items-center gap-3 border-b px-4 py-2">
          <input
            type="text"
            placeholder="搜索名称或描述..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="flex-1 rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
          />
          <div className="flex gap-1">
            {(["name", "chapter", "mentions"] as const).map((s) => (
              <button
                key={s}
                onClick={() => setSortBy(s)}
                className={`rounded px-2 py-1 text-xs transition ${
                  sortBy === s ? "bg-blue-50 text-blue-700" : "text-gray-400 hover:text-gray-600"
                }`}
              >
                {s === "name" ? "名称" : s === "chapter" ? "首次出现" : "提及次数"}
              </button>
            ))}
          </div>
          <span className="text-xs text-gray-400">{filteredEntries.length} 条</span>
        </div>

        {/* Entry List + Detail Panel */}
        <div className="flex flex-1 overflow-hidden">
          {/* Virtual list */}
          <div ref={listRef} className="flex-1 overflow-auto">
            <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
              {virtualizer.getVirtualItems().map((vi) => {
                const entry = filteredEntries[vi.index]
                const isSelected = selectedEntry?.name === entry.name
                return (
                  <div
                    key={vi.key}
                    style={{ position: "absolute", top: vi.start, height: vi.size, left: 0, right: 0 }}
                    className={`cursor-pointer border-b px-4 py-2 transition ${
                      isSelected ? "bg-blue-50" : "hover:bg-gray-50"
                    }`}
                    onClick={() => {
                      setSelectedEntry(isSelected ? null : entry)
                      if (!isSelected) openCard(entry.name, entry.type as EntityType)
                    }}
                  >
                    <div className="flex items-start gap-2">
                      <span
                        className="mt-1 inline-block h-2.5 w-2.5 flex-shrink-0 rounded-full"
                        style={{ backgroundColor: TYPE_COLORS[entry.type] ?? "#6b7280" }}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-sm">{entry.name}</span>
                          {entry.tier && (
                            <span
                              className="rounded px-1.5 py-0.5 text-[10px]"
                              style={{
                                backgroundColor: (TIER_COLORS[entry.tier] ?? "#6b7280") + "15",
                                color: TIER_COLORS[entry.tier] ?? "#6b7280",
                              }}
                            >
                              {entry.tier}
                            </span>
                          )}
                          <span className="text-xs text-gray-400">
                            第{entry.first_chapter}回
                            {entry.chapter_count ? ` · ${entry.chapter_count}次` : ""}
                          </span>
                        </div>
                        <p className="mt-0.5 line-clamp-1 text-xs text-gray-500">{entry.definition}</p>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Detail Panel — hidden on mobile (entity card drawer is used instead) */}
          {selectedEntry && (
            <div className="hidden w-80 flex-shrink-0 overflow-y-auto border-l bg-gray-50 p-4 md:block">
              <div className="mb-3 flex items-center gap-2">
                <span
                  className="inline-block h-3 w-3 rounded-full"
                  style={{ backgroundColor: TYPE_COLORS[selectedEntry.type] ?? "#6b7280" }}
                />
                <h3 className="text-lg font-semibold">{selectedEntry.name}</h3>
              </div>
              <p className="mb-2 text-xs text-gray-500">
                {TYPE_LABELS[selectedEntry.type] ?? selectedEntry.type}
                {selectedEntry.category && selectedEntry.category !== selectedEntry.type
                  ? ` · ${selectedEntry.category}`
                  : ""}
              </p>
              {selectedEntry.tier && (
                <p className="mb-2 text-xs text-gray-400">层级: {selectedEntry.tier}</p>
              )}
              {selectedEntry.parent && (
                <p className="mb-2 text-xs text-gray-400">
                  上级: {selectedEntry.parent}
                </p>
              )}
              <p className="mb-3 text-sm leading-relaxed text-gray-700">
                {selectedEntry.definition}
              </p>
              <p className="text-xs text-gray-400">
                首次出现: 第{selectedEntry.first_chapter}回
                {selectedEntry.chapter_count ? ` · 出现 ${selectedEntry.chapter_count} 次` : ""}
              </p>
              <button
                onClick={() => setSelectedEntry(null)}
                className="mt-4 w-full rounded-md border px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-100"
              >
                关闭
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
