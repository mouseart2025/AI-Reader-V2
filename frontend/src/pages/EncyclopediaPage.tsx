import { useCallback, useEffect, useMemo, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { fetchNovel, fetchEncyclopediaStats, fetchEncyclopediaEntries, fetchConceptDetail } from "@/api/client"
import type { Novel } from "@/api/types"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { EntityCardDrawer } from "@/components/entity-cards/EntityCardDrawer"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

interface CategoryStats {
  total: number
  person: number
  location: number
  item: number
  org: number
  concept: number
  concept_categories: Record<string, number>
}

interface EncEntry {
  name: string
  type: string
  category: string
  definition: string
  first_chapter: number
  parent?: string | null
  depth?: number
}

interface ConceptDetail {
  name: string
  category: string
  definition: string
  first_chapter: number
  excerpts: { chapter: number; text: string }[]
  related_concepts: string[]
  related_entities: { name: string; type: string; chapter: number }[]
}

const TYPE_COLORS: Record<string, string> = {
  person: "#3b82f6",
  location: "#10b981",
  item: "#f59e0b",
  org: "#8b5cf6",
  concept: "#6b7280",
}

const TYPE_LABELS: Record<string, string> = {
  person: "人物",
  location: "地点",
  item: "物品",
  org: "组织",
  concept: "概念",
}

export default function EncyclopediaPage() {
  const { novelId } = useParams<{ novelId: string }>()
  const navigate = useNavigate()
  const openEntityCard = useEntityCardStore((s) => s.openCard)

  const [novel, setNovel] = useState<Novel | null>(null)
  const [stats, setStats] = useState<CategoryStats | null>(null)
  const [entries, setEntries] = useState<EncEntry[]>([])
  const [loading, setLoading] = useState(true)

  const [activeCategory, setActiveCategory] = useState<string | null>(null)
  const [sortBy, setSortBy] = useState<"name" | "chapter" | "hierarchy">("name")
  const [search, setSearch] = useState("")
  const [conceptDetail, setConceptDetail] = useState<ConceptDetail | null>(null)
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set())

  // Reset hierarchy sort when leaving location category
  useEffect(() => {
    if (activeCategory !== "location" && sortBy === "hierarchy") setSortBy("name")
  }, [activeCategory, sortBy])

  // Load novel info and stats
  useEffect(() => {
    if (!novelId) return
    fetchNovel(novelId).then(setNovel)
    fetchEncyclopediaStats(novelId).then((data) => setStats(data as CategoryStats))
  }, [novelId])

  // Load entries when category or sort changes
  useEffect(() => {
    if (!novelId) return
    setLoading(true)
    setConceptDetail(null)
    fetchEncyclopediaEntries(novelId, activeCategory ?? undefined, sortBy)
      .then((data) => setEntries(data.entries))
      .finally(() => setLoading(false))
  }, [novelId, activeCategory, sortBy])

  // Filtered entries by search
  const filteredEntries = useMemo(() => {
    if (!search.trim()) return entries
    const q = search.toLowerCase()
    return entries.filter((e) => e.name.toLowerCase().includes(q))
  }, [entries, search])

  // Build tree entries for hierarchy view
  interface TreeEntry extends EncEntry {
    depth: number
    hasChildren: boolean
    isExpanded: boolean
  }

  const treeEntries = useMemo((): TreeEntry[] => {
    if (sortBy !== "hierarchy" || search.trim()) return []

    const locations = filteredEntries.filter((e) => e.type === "location")
    const nameSet = new Set(locations.map((e) => e.name))
    const entryMap = new Map(locations.map((e) => [e.name, e]))

    // Build children map
    const childrenMap = new Map<string, string[]>()
    for (const e of locations) {
      if (e.parent && nameSet.has(e.parent)) {
        const children = childrenMap.get(e.parent) ?? []
        children.push(e.name)
        childrenMap.set(e.parent, children)
      }
    }

    // Sort children alphabetically
    for (const [, children] of childrenMap) {
      children.sort()
    }

    // Identify roots
    const roots = locations
      .filter((e) => !e.parent || !nameSet.has(e.parent))
      .map((e) => e.name)
      .sort()

    // DFS with expand/collapse
    const result: TreeEntry[] = []
    const visited = new Set<string>()

    const dfs = (name: string, depth: number) => {
      if (visited.has(name)) return
      visited.add(name)
      const entry = entryMap.get(name)
      if (!entry) return
      const children = childrenMap.get(name) ?? []
      const isExpanded = expandedNodes.has(name)
      result.push({
        ...entry,
        depth: entry.depth ?? depth,
        hasChildren: children.length > 0,
        isExpanded,
      })
      if (isExpanded) {
        for (const child of children) {
          dfs(child, (entry.depth ?? depth) + 1)
        }
      }
    }

    for (const root of roots) {
      dfs(root, 0)
    }

    // Add unvisited locations
    for (const e of locations) {
      if (!visited.has(e.name)) {
        result.push({
          ...e,
          depth: e.depth ?? 0,
          hasChildren: false,
          isExpanded: false,
        })
      }
    }

    return result
  }, [filteredEntries, sortBy, expandedNodes, search])

  const toggleExpand = useCallback((name: string) => {
    setExpandedNodes((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }, [])

  const handleEntryClick = useCallback(
    (entry: EncEntry) => {
      if (entry.type === "concept") {
        // Show concept detail
        if (!novelId) return
        fetchConceptDetail(novelId, entry.name).then((data) =>
          setConceptDetail(data as ConceptDetail),
        )
      } else {
        openEntityCard(entry.name, entry.type as "person" | "location" | "item" | "org")
      }
    },
    [novelId, openEntityCard],
  )

  const categoryItems = useMemo(() => {
    if (!stats) return []
    const items: { key: string | null; label: string; count: number; indent: number }[] = [
      { key: null, label: "全部", count: stats.total, indent: 0 },
      { key: "person", label: "人物", count: stats.person, indent: 1 },
      { key: "location", label: "地点", count: stats.location, indent: 1 },
      { key: "item", label: "物品", count: stats.item, indent: 1 },
      { key: "org", label: "组织", count: stats.org, indent: 1 },
      { key: "concept", label: "概念", count: stats.concept, indent: 1 },
    ]
    // Add concept sub-categories
    for (const [cat, count] of Object.entries(stats.concept_categories)) {
      items.push({ key: cat, label: cat, count, indent: 2 })
    }
    return items
  }, [stats])

  return (
    <div className="flex h-full flex-col">
      {/* Search bar */}
      <div className="flex items-center gap-4 border-b bg-muted/30 px-4 py-1.5">
        <span className="text-muted-foreground text-xs">搜索</span>
        <Input
          type="text"
          placeholder="搜索实体、概念..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-56 h-7 text-xs"
        />
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Left: Category sidebar */}
        <div className="w-52 flex-shrink-0 border-r overflow-auto py-2">
          {categoryItems.map((item) => (
            <button
              key={item.key ?? "all"}
              className={cn(
                "w-full text-left text-xs py-1.5 px-3 hover:bg-muted/50 flex items-center justify-between transition-colors",
                activeCategory === item.key && "bg-muted font-medium",
              )}
              style={{ paddingLeft: `${item.indent * 16 + 12}px` }}
              onClick={() => setActiveCategory(item.key)}
            >
              <span className="flex items-center gap-1.5">
                {item.indent > 0 && item.key && TYPE_COLORS[item.key] && (
                  <span
                    className="inline-block size-2 rounded-full"
                    style={{ backgroundColor: TYPE_COLORS[item.key] }}
                  />
                )}
                {item.label}
              </span>
              <span className="text-muted-foreground">{item.count}</span>
            </button>
          ))}
        </div>

        {/* Middle: Entry list */}
        <div className="flex-1 overflow-auto">
          {/* Sort controls */}
          <div className="flex items-center gap-2 border-b px-4 py-2 sticky top-0 bg-background z-10">
            <span className="text-xs text-muted-foreground">排序</span>
            <Button
              variant={sortBy === "name" ? "default" : "outline"}
              size="xs"
              onClick={() => setSortBy("name")}
            >
              名称
            </Button>
            <Button
              variant={sortBy === "chapter" ? "default" : "outline"}
              size="xs"
              onClick={() => setSortBy("chapter")}
            >
              章节
            </Button>
            {activeCategory === "location" && (
              <Button
                variant={sortBy === "hierarchy" ? "default" : "outline"}
                size="xs"
                onClick={() => setSortBy("hierarchy")}
              >
                层级
              </Button>
            )}
            <div className="flex-1" />
            <span className="text-xs text-muted-foreground">
              {filteredEntries.length} 条目
            </span>
          </div>

          {loading ? (
            <div className="flex items-center justify-center h-40">
              <p className="text-muted-foreground text-sm">加载中...</p>
            </div>
          ) : filteredEntries.length === 0 ? (
            <div className="flex items-center justify-center h-40">
              <p className="text-muted-foreground text-sm">暂无数据</p>
            </div>
          ) : sortBy === "hierarchy" && !search.trim() && treeEntries.length > 0 ? (
            <div className="divide-y">
              {treeEntries.map((entry) => (
                <div
                  key={`${entry.name}-tree`}
                  className="flex items-center gap-2 py-2 pr-4 hover:bg-muted/30 cursor-pointer transition-colors"
                  style={{ paddingLeft: `${entry.depth * 20 + 16}px` }}
                  onClick={() => handleEntryClick(entry)}
                >
                  {entry.hasChildren ? (
                    <button
                      className="w-4 text-xs text-muted-foreground hover:text-foreground flex-shrink-0"
                      onClick={(e) => {
                        e.stopPropagation()
                        toggleExpand(entry.name)
                      }}
                    >
                      {entry.isExpanded ? "\u25BC" : "\u25B6"}
                    </button>
                  ) : (
                    <span className="w-4 flex-shrink-0" />
                  )}
                  <span
                    className="inline-block size-2.5 rounded-full flex-shrink-0"
                    style={{ backgroundColor: TYPE_COLORS.location }}
                  />
                  <span className="text-sm font-medium truncate">{entry.name}</span>
                  {entry.definition && (
                    <span className="text-xs text-muted-foreground truncate flex-1 min-w-0">
                      {entry.definition}
                    </span>
                  )}
                  <span className="text-[10px] text-muted-foreground flex-shrink-0 ml-auto">
                    Ch.{entry.first_chapter}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="divide-y">
              {filteredEntries.map((entry) => (
                <div
                  key={`${entry.name}-${entry.type}`}
                  className="flex items-start gap-3 px-4 py-2.5 hover:bg-muted/30 cursor-pointer transition-colors"
                  onClick={() => handleEntryClick(entry)}
                >
                  {/* Type indicator */}
                  <span
                    className="inline-block size-2.5 rounded-full mt-1.5 flex-shrink-0"
                    style={{ backgroundColor: TYPE_COLORS[entry.type] ?? "#6b7280" }}
                  />

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{entry.name}</span>
                      <span className="text-[10px] text-muted-foreground px-1 py-0.5 rounded bg-muted">
                        {TYPE_LABELS[entry.type] ?? entry.category}
                      </span>
                    </div>
                    {entry.definition && (
                      <p className="text-xs text-muted-foreground mt-0.5 truncate">
                        {entry.definition}
                      </p>
                    )}
                  </div>

                  <span className="text-[10px] text-muted-foreground flex-shrink-0 mt-1">
                    Ch.{entry.first_chapter}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right: Concept detail (shown when a concept is selected) */}
        {conceptDetail && (
          <div className="w-80 flex-shrink-0 border-l overflow-auto">
            <div className="p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-medium">{conceptDetail.name}</h3>
                <button
                  className="text-muted-foreground text-xs hover:text-foreground"
                  onClick={() => setConceptDetail(null)}
                >
                  ✕
                </button>
              </div>

              <div className="space-y-3">
                {/* Category */}
                <div>
                  <span className="text-[10px] text-muted-foreground block mb-0.5">分类</span>
                  <span className="text-xs px-1.5 py-0.5 rounded bg-muted">
                    {conceptDetail.category}
                  </span>
                  <span className="text-xs text-muted-foreground ml-2">
                    首次出现: 第{conceptDetail.first_chapter}章
                  </span>
                </div>

                {/* Definition */}
                <div>
                  <span className="text-[10px] text-muted-foreground block mb-0.5">定义</span>
                  <p className="text-sm">{conceptDetail.definition}</p>
                </div>

                {/* Excerpts */}
                {conceptDetail.excerpts.length > 0 && (
                  <div>
                    <span className="text-[10px] text-muted-foreground block mb-1">
                      原文摘录 ({conceptDetail.excerpts.length})
                    </span>
                    <div className="space-y-1.5">
                      {conceptDetail.excerpts.map((ex, i) => (
                        <div key={i} className="text-xs border-l-2 border-muted pl-2">
                          <p>{ex.text}</p>
                          <span className="text-[10px] text-muted-foreground">
                            — 第{ex.chapter}章
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Related concepts */}
                {conceptDetail.related_concepts.length > 0 && (
                  <div>
                    <span className="text-[10px] text-muted-foreground block mb-1">
                      关联概念 ({conceptDetail.related_concepts.length})
                    </span>
                    <div className="flex flex-wrap gap-1">
                      {conceptDetail.related_concepts.map((c) => (
                        <button
                          key={c}
                          className="text-xs px-1.5 py-0.5 rounded bg-muted hover:bg-muted/80"
                          onClick={() => {
                            if (novelId)
                              fetchConceptDetail(novelId, c).then((d) =>
                                setConceptDetail(d as ConceptDetail),
                              )
                          }}
                        >
                          {c}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* Related entities */}
                {conceptDetail.related_entities.length > 0 && (
                  <div>
                    <span className="text-[10px] text-muted-foreground block mb-1">
                      关联实体 ({conceptDetail.related_entities.length})
                    </span>
                    <div className="space-y-1">
                      {conceptDetail.related_entities.map((e) => (
                        <button
                          key={e.name}
                          className="block text-xs text-blue-600 hover:underline"
                          onClick={() =>
                            openEntityCard(e.name, e.type as "person" | "location" | "item" | "org")
                          }
                        >
                          {e.name}
                          <span className="text-muted-foreground ml-1">
                            (Ch.{e.chapter})
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {novelId && <EntityCardDrawer novelId={novelId} />}
    </div>
  )
}
