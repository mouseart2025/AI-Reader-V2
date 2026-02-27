import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { useVirtualizer } from "@tanstack/react-virtual"
import { fetchEncyclopediaStats, fetchEncyclopediaEntries, fetchConceptDetail, fetchLocationConflicts, fetchWorldStructure, rebuildHierarchy, applyHierarchyChanges } from "@/api/client"
import type { WorldStructureData } from "@/api/types"
import type { HierarchyRebuildResult } from "@/api/types"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { EntityCardDrawer } from "@/components/entity-cards/EntityCardDrawer"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
import { trackEvent } from "@/lib/tracker"

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
  chapter_count?: number
  parent?: string | null
  depth?: number
  tier?: string
  icon?: string
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

const TIER_LABELS: Record<string, string> = {
  world: "世界",
  continent: "大陆",
  kingdom: "国",
  region: "区域",
  city: "城",
  site: "场所",
  building: "建筑",
}

const TIER_COLORS: Record<string, string> = {
  world: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  continent: "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
  kingdom: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
  region: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300",
  city: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  site: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  building: "bg-slate-100 text-slate-700 dark:bg-slate-800/40 dark:text-slate-300",
}

export default function EncyclopediaPage() {
  const { novelId } = useParams<{ novelId: string }>()
  const navigate = useNavigate()
  const openEntityCard = useEntityCardStore((s) => s.openCard)
  const [stats, setStats] = useState<CategoryStats | null>(null)
  const [entries, setEntries] = useState<EncEntry[]>([])
  const [loading, setLoading] = useState(true)

  const [activeCategory, setActiveCategory] = useState<string | null>(null)
  const [sortBy, setSortBy] = useState<"name" | "chapter" | "hierarchy" | "mentions">("name")
  const [search, setSearch] = useState("")
  const [conceptDetail, setConceptDetail] = useState<ConceptDetail | null>(null)
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set())
  const [rebuilding, setRebuilding] = useState(false)
  const [rebuildProgress, setRebuildProgress] = useState("")
  const [toast, setToast] = useState<string | null>(null)
  const [rebuildResult, setRebuildResult] = useState<HierarchyRebuildResult | null>(null)
  const [selectedChanges, setSelectedChanges] = useState<Set<number>>(new Set())
  const [applying, setApplying] = useState(false)
  const [conflictMap, setConflictMap] = useState<Record<string, { type: string; severity: string; description: string; chapters: number[] }[]>>({})
  const [worldStructure, setWorldStructure] = useState<WorldStructureData | null>(null)

  // Reset hierarchy sort when leaving location category
  useEffect(() => {
    if (activeCategory !== "location" && sortBy === "hierarchy") setSortBy("name")
    if (activeCategory === "location" && sortBy === "hierarchy") return // keep hierarchy
  }, [activeCategory, sortBy])

  // Load stats
  useEffect(() => {
    if (!novelId) return
    fetchEncyclopediaStats(novelId).then((data) => setStats(data as unknown as CategoryStats))
  }, [novelId])

  // Load location conflicts when hierarchy view is active
  useEffect(() => {
    if (!novelId || sortBy !== "hierarchy") return
    fetchLocationConflicts(novelId).then(setConflictMap)
  }, [novelId, sortBy])

  // Load entries when category or sort changes
  useEffect(() => {
    if (!novelId) return
    setLoading(true)
    setConceptDetail(null)
    trackEvent("view_encyclopedia")
    fetchEncyclopediaEntries(novelId, activeCategory ?? undefined, sortBy)
      .then((data) => setEntries(data.entries))
      .finally(() => setLoading(false))
  }, [novelId, activeCategory, sortBy])

  // Filtered entries by search
  const filteredEntries = useMemo(() => {
    if (!search.trim()) return entries
    const q = search.toLowerCase()
    return entries.filter((e) => e.name.toLowerCase().includes(q) || (e.definition ?? "").toLowerCase().includes(q))
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

    // Add truly disconnected locations (not children of collapsed parents)
    for (const e of locations) {
      if (!visited.has(e.name)) {
        // If parent is in the list, this node is hidden under a collapsed parent — skip
        if (e.parent && nameSet.has(e.parent)) continue
        const children = childrenMap.get(e.name) ?? []
        result.push({
          ...e,
          depth: e.depth ?? 0,
          hasChildren: children.length > 0,
          isExpanded: expandedNodes.has(e.name),
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

  // Virtual list setup
  const listContainerRef = useRef<HTMLDivElement>(null)
  const isTreeView = sortBy === "hierarchy" && !search.trim() && treeEntries.length > 0
  const virtualItemCount = isTreeView ? treeEntries.length : filteredEntries.length
  const virtualizer = useVirtualizer({
    count: virtualItemCount,
    getScrollElement: () => listContainerRef.current,
    estimateSize: () => (isTreeView ? 40 : 48),
    overscan: 15,
  })

  const handleEntryClick = useCallback(
    (entry: EncEntry) => {
      if (entry.type === "concept") {
        // Show concept detail
        if (!novelId) return
        fetchConceptDetail(novelId, entry.name).then((data) =>
          setConceptDetail(data as unknown as ConceptDetail),
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
    // World view special entry
    items.push({ key: "__worldview__", label: "世界观", count: 0, indent: 0 })
    return items
  }, [stats])

  // Load world structure when worldview tab is selected
  useEffect(() => {
    if (!novelId || activeCategory !== "__worldview__") return
    fetchWorldStructure(novelId).then(setWorldStructure)
  }, [novelId, activeCategory])

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
        <div ref={listContainerRef} className="flex-1 overflow-auto">
          {/* Sort controls */}
          <div className={cn("flex items-center gap-2 border-b px-4 py-2 sticky top-0 bg-background z-10", activeCategory === "__worldview__" && "hidden")}>
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
            <Button
              variant={sortBy === "mentions" ? "default" : "outline"}
              size="xs"
              onClick={() => setSortBy("mentions")}
            >
              热度
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
            {activeCategory === "location" && sortBy === "hierarchy" && (
              <Button
                variant="outline"
                size="xs"
                disabled={rebuilding}
                onClick={() => {
                  if (!novelId || rebuilding) return
                  setRebuilding(true)
                  setRebuildProgress("正在初始化...")
                  rebuildHierarchy(novelId, setRebuildProgress)
                    .then((res) => {
                      if (res.changes.length === 0) {
                        setToast("层级无变化")
                        setTimeout(() => setToast(null), 4000)
                      } else {
                        setRebuildResult(res)
                        setSelectedChanges(new Set(res.changes.map((c, i) => c.auto_select ? i : -1).filter(i => i >= 0)))
                      }
                    })
                    .catch(() => {
                      setToast("层级重建失败")
                      setTimeout(() => setToast(null), 4000)
                    })
                    .finally(() => {
                      setRebuilding(false)
                      setRebuildProgress("")
                    })
                }}
              >
                {rebuilding ? "重建中..." : "重建层级"}
              </Button>
            )}
            <div className="flex-1" />
            {rebuilding && rebuildProgress && (
              <span className="text-xs text-blue-600 animate-pulse">{rebuildProgress}</span>
            )}
            {toast && (
              <span className="text-xs text-green-600">{toast}</span>
            )}
            <span className="text-xs text-muted-foreground">
              {filteredEntries.length} 条目
            </span>
          </div>

          {activeCategory === "__worldview__" ? (
            <div className="p-4 space-y-4">
              {!worldStructure ? (
                <p className="text-muted-foreground text-sm">加载中...</p>
              ) : (
                <>
                  {/* Layers */}
                  <div>
                    <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">世界层级</h4>
                    <div className="space-y-2">
                      {worldStructure.layers.map((layer) => (
                        <div key={layer.layer_id} className="rounded border p-2">
                          <div className="flex items-center gap-2 mb-1">
                            <span className="text-sm font-medium">{layer.name}</span>
                            <span className="text-[10px] px-1 py-0.5 rounded bg-muted text-muted-foreground">{layer.layer_type}</span>
                          </div>
                          {layer.description && <p className="text-xs text-muted-foreground">{layer.description}</p>}
                          {layer.regions.length > 0 && (
                            <div className="mt-1 text-xs text-muted-foreground">
                              {layer.regions.length} 区域: {layer.regions.map((r) => r.name).join("、")}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Portals */}
                  {worldStructure.portals.length > 0 && (
                    <div>
                      <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">传送门 ({worldStructure.portals.length})</h4>
                      <div className="space-y-1">
                        {worldStructure.portals.map((p, i) => (
                          <div key={i} className="text-xs">
                            <span className="font-medium">{p.name}</span>
                            <span className="text-muted-foreground"> {p.source_location} → {p.target_location}</span>
                            {p.is_bidirectional && <span className="text-muted-foreground"> (双向)</span>}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Spatial scale */}
                  {worldStructure.spatial_scale && (
                    <div>
                      <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">空间尺度</h4>
                      <span className="text-sm">{worldStructure.spatial_scale}</span>
                    </div>
                  )}

                  {/* Genre */}
                  {worldStructure.novel_genre_hint && (
                    <div>
                      <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1">类型</h4>
                      <span className="text-sm">{worldStructure.novel_genre_hint}</span>
                    </div>
                  )}

                  {/* Location stats */}
                  <div className="grid grid-cols-2 gap-2">
                    <div className="rounded bg-muted/50 p-2 text-sm">
                      <span className="text-muted-foreground text-xs block">父子关系</span>
                      <span className="font-medium">{Object.keys(worldStructure.location_parents).length}</span>
                    </div>
                    <div className="rounded bg-muted/50 p-2 text-sm">
                      <span className="text-muted-foreground text-xs block">层级分类</span>
                      <span className="font-medium">{Object.keys(worldStructure.location_tiers).length}</span>
                    </div>
                  </div>
                </>
              )}
            </div>
          ) : loading ? (
            <div className="flex items-center justify-center h-40">
              <p className="text-muted-foreground text-sm">加载中...</p>
            </div>
          ) : filteredEntries.length === 0 ? (
            <div className="flex items-center justify-center h-40">
              <p className="text-muted-foreground text-sm">暂无数据</p>
            </div>
          ) : virtualItemCount === 0 ? null : (
            <div
              style={{ height: `${virtualizer.getTotalSize()}px`, position: "relative" }}
            >
              {virtualizer.getVirtualItems().map((virtualRow) => {
                if (isTreeView) {
                  const entry = treeEntries[virtualRow.index]
                  return (
                    <div
                      key={virtualRow.key}
                      style={{
                        position: "absolute",
                        top: 0,
                        left: 0,
                        width: "100%",
                        transform: `translateY(${virtualRow.start}px)`,
                        paddingLeft: `${entry.depth * 20 + 16}px`,
                      }}
                      className="flex items-center gap-2 py-2 pr-4 hover:bg-muted/30 cursor-pointer transition-colors border-b"
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
                      {conflictMap[entry.name] && (
                        <span
                          className="inline-block size-2 rounded-full bg-red-500 flex-shrink-0"
                          title={conflictMap[entry.name].map((c) => c.description).join("; ")}
                        />
                      )}
                      {entry.tier && TIER_LABELS[entry.tier] && (
                        <span className={cn("text-[10px] px-1 py-0.5 rounded flex-shrink-0", TIER_COLORS[entry.tier] ?? "bg-muted text-muted-foreground")}>
                          {TIER_LABELS[entry.tier]}
                        </span>
                      )}
                      {entry.definition && (
                        <span className="text-xs text-muted-foreground truncate flex-1 min-w-0">
                          {entry.definition}
                        </span>
                      )}
                      <span className="text-[10px] text-muted-foreground flex-shrink-0 ml-auto">
                        Ch.{entry.first_chapter}
                      </span>
                    </div>
                  )
                } else {
                  const entry = filteredEntries[virtualRow.index]
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
                      className="flex items-start gap-3 px-4 py-2.5 hover:bg-muted/30 cursor-pointer transition-colors border-b"
                      onClick={() => handleEntryClick(entry)}
                    >
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
                          {entry.type === "location" && entry.tier && TIER_LABELS[entry.tier] && (
                            <span className={cn("text-[10px] px-1 py-0.5 rounded", TIER_COLORS[entry.tier] ?? "bg-muted text-muted-foreground")}>
                              {TIER_LABELS[entry.tier]}
                            </span>
                          )}
                          {(entry.chapter_count ?? 0) > 0 && (
                            <span className="text-[10px] text-muted-foreground/70">
                              {entry.chapter_count}章
                            </span>
                          )}
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
                  )
                }
              })}
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
                  <button
                    className="text-xs text-muted-foreground ml-2 hover:text-primary hover:underline cursor-pointer"
                    onClick={() => navigate(`/read/${novelId}?chapter=${conceptDetail.first_chapter}`)}
                  >
                    首次出现: 第{conceptDetail.first_chapter}章
                  </button>
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
                          <button
                            className="text-[10px] text-muted-foreground hover:text-primary hover:underline cursor-pointer"
                            onClick={() => navigate(`/read/${novelId}?chapter=${ex.chapter}`)}
                          >
                            — 第{ex.chapter}章
                          </button>
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
                                setConceptDetail(d as unknown as ConceptDetail),
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
                        <div key={e.name} className="text-xs flex items-center gap-1">
                          <button
                            className="text-blue-600 hover:underline"
                            onClick={() =>
                              openEntityCard(e.name, e.type as "person" | "location" | "item" | "org")
                            }
                          >
                            {e.name}
                          </button>
                          <button
                            className="text-muted-foreground hover:text-primary hover:underline cursor-pointer"
                            onClick={() => navigate(`/read/${novelId}?chapter=${e.chapter}`)}
                          >
                            (Ch.{e.chapter})
                          </button>
                        </div>
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

      {/* Hierarchy changes preview Dialog */}
      <Dialog open={rebuildResult !== null} onOpenChange={(open) => { if (!open) setRebuildResult(null) }}>
        <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>层级变更预览</DialogTitle>
            {rebuildResult && (
              <div className="flex items-center gap-3 text-xs text-muted-foreground pt-1">
                <span className="text-green-600">+{rebuildResult.summary.added} 新增</span>
                <span className="text-yellow-600">~{rebuildResult.summary.changed} 变更</span>
                <span className="text-red-600">-{rebuildResult.summary.removed} 移除</span>
                <span className="mx-1">|</span>
                <span>根节点 {rebuildResult.summary.old_root_count} → {rebuildResult.summary.new_root_count}</span>
                {rebuildResult.summary.scene_analysis_used && <span className="px-1 py-0.5 rounded bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300">场景分析</span>}
                {rebuildResult.summary.llm_review_used && <span className="px-1 py-0.5 rounded bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300">LLM审查</span>}
              </div>
            )}
          </DialogHeader>

          {rebuildResult && (
            <div className="flex-1 overflow-auto border rounded-md">
              <table className="w-full text-xs">
                <thead className="bg-muted/50 sticky top-0">
                  <tr>
                    <th className="w-8 px-2 py-1.5 text-left">
                      <input
                        type="checkbox"
                        checked={selectedChanges.size === rebuildResult.changes.length}
                        ref={(el) => { if (el) el.indeterminate = selectedChanges.size > 0 && selectedChanges.size < rebuildResult.changes.length }}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setSelectedChanges(new Set(rebuildResult.changes.map((_, i) => i)))
                          } else {
                            setSelectedChanges(new Set())
                          }
                        }}
                      />
                    </th>
                    <th className="px-2 py-1.5 text-left">地点</th>
                    <th className="px-2 py-1.5 text-left">原父级</th>
                    <th className="w-6 px-1 py-1.5" />
                    <th className="px-2 py-1.5 text-left">新父级</th>
                    <th className="w-16 px-2 py-1.5 text-left">类型</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {rebuildResult.changes.map((change, idx) => (
                    <tr key={change.location} className={cn("hover:bg-muted/30", !change.auto_select && "opacity-60")}>
                      <td className="px-2 py-1.5">
                        <input
                          type="checkbox"
                          checked={selectedChanges.has(idx)}
                          onChange={(e) => {
                            setSelectedChanges((prev) => {
                              const next = new Set(prev)
                              if (e.target.checked) next.add(idx)
                              else next.delete(idx)
                              return next
                            })
                          }}
                        />
                      </td>
                      <td className="px-2 py-1.5 font-medium">
                        {change.location}
                        {change.reason && <span className="block text-[10px] text-muted-foreground font-normal">{change.reason}</span>}
                      </td>
                      <td className="px-2 py-1.5 text-muted-foreground">{change.old_parent ?? "—"}</td>
                      <td className="px-1 py-1.5 text-muted-foreground text-center">→</td>
                      <td className="px-2 py-1.5">{change.new_parent ?? "—"}</td>
                      <td className="px-2 py-1.5">
                        <span className={cn(
                          "px-1.5 py-0.5 rounded text-[10px]",
                          change.change_type === "added" && "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
                          change.change_type === "changed" && "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300",
                          change.change_type === "removed" && "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
                        )}>
                          {change.change_type === "added" ? "新增" : change.change_type === "changed" ? "变更" : "移除"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <DialogFooter className="flex-row items-center gap-2 sm:flex-row">
            <span className="text-xs text-muted-foreground flex-1">
              已选 {selectedChanges.size} / {rebuildResult?.changes.length ?? 0} 项
            </span>
            <Button variant="outline" size="sm" onClick={() => setRebuildResult(null)}>
              取消
            </Button>
            <Button
              size="sm"
              disabled={applying || selectedChanges.size === 0}
              onClick={() => {
                if (!novelId || !rebuildResult || applying) return
                setApplying(true)
                const selected = rebuildResult.changes
                  .filter((_, i) => selectedChanges.has(i))
                  .map((c) => ({ location: c.location, new_parent: c.new_parent }))
                applyHierarchyChanges(novelId, selected, rebuildResult.location_tiers)
                  .then((res) => {
                    setRebuildResult(null)
                    setToast(`层级已更新: ${res.root_count} 个根节点`)
                    setTimeout(() => setToast(null), 4000)
                    // Reload entries to reflect new hierarchy
                    setLoading(true)
                    fetchEncyclopediaEntries(novelId, "location", "hierarchy")
                      .then((data) => setEntries(data.entries))
                      .finally(() => setLoading(false))
                  })
                  .catch(() => {
                    setToast("应用变更失败")
                    setTimeout(() => setToast(null), 4000)
                  })
                  .finally(() => setApplying(false))
              }}
            >
              {applying ? "应用中..." : `应用 ${selectedChanges.size} 项变更`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
