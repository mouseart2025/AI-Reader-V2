/**
 * DemoEncyclopediaPage — searchable/filterable encyclopedia using static demo data.
 * Supports category tabs, text search, virtual scrolling, world view tab, and concept popup.
 */
import { useCallback, useMemo, useRef, useState } from "react"
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

interface WorldStructure {
  location_parents: Record<string, string>
  location_tiers: Record<string, string>
}

interface TreeNode {
  name: string
  tier: string
  children: TreeNode[]
  definition?: string
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

const TIER_LABELS: Record<string, string> = {
  world: "世界", continent: "大陆", kingdom: "国",
  region: "区域", city: "城镇", site: "场所", building: "建筑",
}

// ── Build location hierarchy tree ────────────────

function buildLocationTree(ws: WorldStructure, entries: EncEntry[]): TreeNode[] {
  const { location_parents, location_tiers } = ws
  const defMap = new Map(entries.filter((e) => e.type === "location").map((e) => [e.name, e.definition]))

  // Find all locations
  const allLocations = new Set<string>()
  for (const [child, parent] of Object.entries(location_parents)) {
    allLocations.add(child)
    allLocations.add(parent)
  }

  // Build children map
  const childrenMap = new Map<string, string[]>()
  for (const [child, parent] of Object.entries(location_parents)) {
    if (!childrenMap.has(parent)) childrenMap.set(parent, [])
    childrenMap.get(parent)!.push(child)
  }

  // Find roots (locations that are not children of anything)
  const childSet = new Set(Object.keys(location_parents))
  const roots = [...allLocations].filter((loc) => !childSet.has(loc))

  // Recursive tree builder (limit depth to avoid infinite loops)
  const visited = new Set<string>()
  function buildNode(name: string, depth: number): TreeNode {
    visited.add(name)
    const children = (childrenMap.get(name) ?? [])
      .filter((c) => !visited.has(c))
      .sort((a, b) => {
        const ta = TIER_ORDER[location_tiers[a]] ?? 99
        const tb = TIER_ORDER[location_tiers[b]] ?? 99
        return ta - tb || a.localeCompare(b, "zh")
      })
      .map((c) => depth < 6 ? buildNode(c, depth + 1) : ({ name: c, tier: location_tiers[c] ?? "", children: [] }))

    return {
      name,
      tier: location_tiers[name] ?? "",
      children,
      definition: defMap.get(name),
    }
  }

  const TIER_ORDER: Record<string, number> = {
    world: 0, continent: 1, kingdom: 2, region: 3, city: 4, site: 5, building: 6,
  }

  // Sort roots by tier
  const sortedRoots = roots.sort((a, b) => {
    const ta = TIER_ORDER[location_tiers[a]] ?? 99
    const tb = TIER_ORDER[location_tiers[b]] ?? 99
    return ta - tb || a.localeCompare(b, "zh")
  })

  return sortedRoots.map((r) => buildNode(r, 0))
}

// ── TreeNodeView component ───────────────────────

function TreeNodeView({
  node,
  depth,
  onSelect,
  expandedNodes,
  toggleExpand,
}: {
  node: TreeNode
  depth: number
  onSelect: (name: string) => void
  expandedNodes: Set<string>
  toggleExpand: (name: string) => void
}) {
  const hasChildren = node.children.length > 0
  const isExpanded = expandedNodes.has(node.name)
  const tierColor = TIER_COLORS[node.tier] ?? "#6b7280"

  return (
    <div>
      <div
        className="flex items-center gap-1 rounded px-1 py-0.5 hover:bg-slate-800/50 cursor-pointer"
        style={{ paddingLeft: `${depth * 16 + 4}px` }}
      >
        {/* Expand/collapse toggle */}
        {hasChildren ? (
          <button
            onClick={(e) => { e.stopPropagation(); toggleExpand(node.name) }}
            className="flex size-4 items-center justify-center text-slate-500 hover:text-slate-300"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`size-3 transition-transform ${isExpanded ? "rotate-90" : ""}`}>
              <path d="m9 18 6-6-6-6"/>
            </svg>
          </button>
        ) : (
          <span className="size-4" />
        )}

        {/* Tier dot */}
        <span className="size-2 rounded-full flex-shrink-0" style={{ backgroundColor: tierColor }} />

        {/* Name */}
        <button
          onClick={() => onSelect(node.name)}
          className="truncate text-xs text-slate-300 hover:text-white"
        >
          {node.name}
        </button>

        {/* Tier badge */}
        {node.tier && (
          <span className="ml-auto text-[10px] text-slate-600">
            {TIER_LABELS[node.tier] ?? node.tier}
          </span>
        )}

        {/* Child count */}
        {hasChildren && (
          <span className="text-[10px] text-slate-600">
            {node.children.length}
          </span>
        )}
      </div>

      {/* Children */}
      {isExpanded && node.children.map((child) => (
        <TreeNodeView
          key={child.name}
          node={child}
          depth={depth + 1}
          onSelect={onSelect}
          expandedNodes={expandedNodes}
          toggleExpand={toggleExpand}
        />
      ))}
    </div>
  )
}

// ── Main component ───────────────────────────────

export default function DemoEncyclopediaPage() {
  const { data } = useDemoData()
  const encyclopediaData = data.encyclopedia as { entries: EncEntry[] }
  const worldStructure = data.worldStructure as WorldStructure | null

  const entries = encyclopediaData?.entries ?? []

  const [activeTab, setActiveTab] = useState<"entries" | "world">("entries")
  const [activeType, setActiveType] = useState<string | null>(null)
  const [search, setSearch] = useState("")
  const [sortBy, setSortBy] = useState<"name" | "chapter" | "mentions">("name")
  const [selectedEntry, setSelectedEntry] = useState<EncEntry | null>(null)
  const [conceptPopup, setConceptPopup] = useState<EncEntry | null>(null)
  const openCard = useEntityCardStore((s) => s.openCard)
  const listRef = useRef<HTMLDivElement>(null)

  // World view state
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(() => new Set())
  const [worldSearch, setWorldSearch] = useState("")

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

  // Location hierarchy tree
  const locationTree = useMemo(() => {
    if (!worldStructure?.location_parents) return []
    return buildLocationTree(worldStructure, entries)
  }, [worldStructure, entries])

  // Filtered tree nodes for search
  const filteredTreeRoots = useMemo(() => {
    if (!worldSearch.trim()) return locationTree
    const q = worldSearch.trim().toLowerCase()

    // Find all matching names and their ancestors
    const matchingNames = new Set<string>()
    function collectMatches(nodes: TreeNode[]) {
      for (const node of nodes) {
        if (node.name.toLowerCase().includes(q)) {
          matchingNames.add(node.name)
        }
        collectMatches(node.children)
      }
    }
    collectMatches(locationTree)

    // If nothing matches, return empty
    if (matchingNames.size === 0) return []

    // Filter tree to only include paths to matching nodes
    function filterTree(nodes: TreeNode[]): TreeNode[] {
      return nodes
        .map((node) => {
          const filteredChildren = filterTree(node.children)
          if (matchingNames.has(node.name) || filteredChildren.length > 0) {
            return { ...node, children: filteredChildren }
          }
          return null
        })
        .filter((n): n is TreeNode => n !== null)
    }
    return filterTree(locationTree)
  }, [locationTree, worldSearch])

  const virtualizer = useVirtualizer({
    count: filteredEntries.length,
    getScrollElement: () => listRef.current,
    estimateSize: () => 72,
    overscan: 15,
  })

  const toggleExpand = useCallback((name: string) => {
    setExpandedNodes((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }, [])

  const handleWorldNodeSelect = useCallback((name: string) => {
    const entry = entries.find((e) => e.name === name && e.type === "location")
    if (entry) {
      openCard(entry.name, "location")
    }
  }, [entries, openCard])

  // Expand top-level nodes by default (lazy init)
  const expandTopLevel = useCallback(() => {
    if (expandedNodes.size === 0 && locationTree.length > 0) {
      const topNames = new Set(locationTree.map((n) => n.name))
      // Also expand second level for main roots
      for (const root of locationTree) {
        for (const child of root.children.slice(0, 5)) {
          topNames.add(child.name)
        }
      }
      setExpandedNodes(topNames)
    }
  }, [expandedNodes.size, locationTree])

  const handleEntryClick = useCallback((entry: EncEntry, isSelected: boolean) => {
    if (entry.type === "concept") {
      setConceptPopup(isSelected ? null : entry)
      return
    }
    setSelectedEntry(isSelected ? null : entry)
    if (!isSelected) openCard(entry.name, entry.type as EntityType)
  }, [openCard])

  return (
    <div className="flex h-full bg-slate-950">
      {/* Category Sidebar — hidden on mobile */}
      <div className="hidden w-44 flex-shrink-0 overflow-y-auto border-r border-slate-800 bg-slate-900 p-2 sm:block">
        {/* Tab switcher */}
        <div className="mb-2 flex rounded-md border border-slate-700 p-0.5">
          <button
            onClick={() => setActiveTab("entries")}
            className={`flex-1 rounded px-2 py-1 text-xs transition ${
              activeTab === "entries" ? "bg-blue-500/20 text-blue-400 font-medium" : "text-slate-500 hover:text-slate-300"
            }`}
          >
            百科
          </button>
          <button
            onClick={() => { setActiveTab("world"); expandTopLevel() }}
            className={`flex-1 rounded px-2 py-1 text-xs transition ${
              activeTab === "world" ? "bg-blue-500/20 text-blue-400 font-medium" : "text-slate-500 hover:text-slate-300"
            }`}
          >
            世界观
          </button>
        </div>

        {activeTab === "entries" && (
          <>
            <button
              onClick={() => setActiveType(null)}
              className={`mb-1 w-full rounded-md px-3 py-2 text-left text-sm transition ${
                activeType === null ? "bg-blue-500/20 font-medium text-blue-400" : "text-slate-400 hover:bg-slate-800"
              }`}
            >
              全部 <span className="text-xs text-slate-500">({entries.length})</span>
            </button>
            {Object.entries(TYPE_LABELS).map(([type, label]) => (
              <button
                key={type}
                onClick={() => setActiveType(type)}
                className={`mb-0.5 w-full rounded-md px-3 py-1.5 text-left text-sm transition ${
                  activeType === type ? "bg-blue-500/20 font-medium text-blue-400" : "text-slate-400 hover:bg-slate-800"
                }`}
              >
                <span
                  className="mr-1.5 inline-block h-2 w-2 rounded-full"
                  style={{ backgroundColor: TYPE_COLORS[type] }}
                />
                {label}{" "}
                <span className="text-xs text-slate-500">({typeCounts[type] ?? 0})</span>
              </button>
            ))}
          </>
        )}

        {activeTab === "world" && (
          <div className="space-y-1">
            {/* Tier legend */}
            <p className="px-1 text-[10px] text-slate-600">地点层级</p>
            {Object.entries(TIER_LABELS).map(([tier, label]) => (
              <div key={tier} className="flex items-center gap-1.5 px-2 text-[10px] text-slate-500">
                <span className="size-2 rounded-full" style={{ backgroundColor: TIER_COLORS[tier] }} />
                {label}
              </div>
            ))}
            <div className="mt-2 border-t border-slate-800 pt-2 px-1">
              <p className="text-[10px] text-slate-600">{Object.keys(worldStructure?.location_parents ?? {}).length} 地点层级关系</p>
            </div>
          </div>
        )}
      </div>

      {/* Main Content */}
      <div className="flex flex-1 flex-col">
        {activeTab === "entries" ? (
          <>
            {/* Search + Sort bar */}
            <div className="flex items-center gap-3 border-b border-slate-800 px-4 py-2">
              <input
                type="text"
                placeholder="搜索名称或描述..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="flex-1 rounded-md border border-slate-700 bg-slate-800 px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 focus:border-blue-500 focus:outline-none"
              />
              <div className="flex gap-1">
                {(["name", "chapter", "mentions"] as const).map((s) => (
                  <button
                    key={s}
                    onClick={() => setSortBy(s)}
                    className={`rounded px-2 py-1 text-xs transition ${
                      sortBy === s ? "bg-blue-500/20 text-blue-400" : "text-slate-500 hover:text-slate-300"
                    }`}
                  >
                    {s === "name" ? "名称" : s === "chapter" ? "首次出现" : "提及次数"}
                  </button>
                ))}
              </div>
              <span className="text-xs text-slate-500">{filteredEntries.length} 条</span>
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
                        className={`cursor-pointer border-b border-slate-800/50 px-4 py-2 transition ${
                          isSelected ? "bg-blue-500/10" : "hover:bg-slate-900"
                        }`}
                        onClick={() => handleEntryClick(entry, isSelected)}
                      >
                        <div className="flex items-start gap-2">
                          <span
                            className="mt-1 inline-block h-2.5 w-2.5 flex-shrink-0 rounded-full"
                            style={{ backgroundColor: TYPE_COLORS[entry.type] ?? "#6b7280" }}
                          />
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <span className="font-medium text-sm text-slate-200">{entry.name}</span>
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
                              <span className="text-xs text-slate-500">
                                第{entry.first_chapter}回
                                {entry.chapter_count ? ` · ${entry.chapter_count}次` : ""}
                              </span>
                            </div>
                            <p className="mt-0.5 line-clamp-1 text-xs text-slate-500">{entry.definition}</p>
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>

              {/* Detail Panel — hidden on mobile */}
              {selectedEntry && (
                <div className="hidden w-80 flex-shrink-0 overflow-y-auto border-l border-slate-800 bg-slate-900 p-4 md:block">
                  <div className="mb-3 flex items-center gap-2">
                    <span
                      className="inline-block h-3 w-3 rounded-full"
                      style={{ backgroundColor: TYPE_COLORS[selectedEntry.type] ?? "#6b7280" }}
                    />
                    <h3 className="text-lg font-semibold text-white">{selectedEntry.name}</h3>
                  </div>
                  <p className="mb-2 text-xs text-slate-400">
                    {TYPE_LABELS[selectedEntry.type] ?? selectedEntry.type}
                    {selectedEntry.category && selectedEntry.category !== selectedEntry.type
                      ? ` · ${selectedEntry.category}`
                      : ""}
                  </p>
                  {selectedEntry.tier && (
                    <p className="mb-2 text-xs text-slate-500">层级: {selectedEntry.tier}</p>
                  )}
                  {selectedEntry.parent && (
                    <p className="mb-2 text-xs text-slate-500">
                      上级:{" "}
                      <button
                        className="text-blue-400 hover:underline"
                        onClick={() => {
                          const parentEntry = entries.find((e) => e.name === selectedEntry.parent && e.type === "location")
                          if (parentEntry) {
                            setSelectedEntry(parentEntry)
                            openCard(parentEntry.name, "location")
                          }
                        }}
                      >
                        {selectedEntry.parent}
                      </button>
                    </p>
                  )}
                  <p className="mb-3 text-sm leading-relaxed text-slate-300">
                    {selectedEntry.definition}
                  </p>
                  <p className="text-xs text-slate-500">
                    首次出现: 第{selectedEntry.first_chapter}回
                    {selectedEntry.chapter_count ? ` · 出现 ${selectedEntry.chapter_count} 次` : ""}
                  </p>
                  <button
                    onClick={() => setSelectedEntry(null)}
                    className="mt-4 w-full rounded-md border border-slate-700 px-3 py-1.5 text-xs text-slate-400 hover:bg-slate-800"
                  >
                    关闭
                  </button>
                </div>
              )}
            </div>
          </>
        ) : (
          /* ── World View Tab ── */
          <div className="flex flex-1 flex-col overflow-hidden">
            {/* World search bar */}
            <div className="flex items-center gap-3 border-b border-slate-800 px-4 py-2">
              <input
                type="text"
                placeholder="搜索地点..."
                value={worldSearch}
                onChange={(e) => setWorldSearch(e.target.value)}
                className="flex-1 rounded-md border border-slate-700 bg-slate-800 px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 focus:border-blue-500 focus:outline-none"
              />
              <button
                onClick={() => {
                  // Expand all
                  const all = new Set<string>()
                  function collectNames(nodes: TreeNode[]) {
                    for (const n of nodes) { all.add(n.name); collectNames(n.children) }
                  }
                  collectNames(locationTree)
                  setExpandedNodes(all)
                }}
                className="rounded px-2 py-1 text-xs text-slate-500 hover:text-slate-300"
              >
                全部展开
              </button>
              <button
                onClick={() => setExpandedNodes(new Set())}
                className="rounded px-2 py-1 text-xs text-slate-500 hover:text-slate-300"
              >
                全部折叠
              </button>
              <span className="text-xs text-slate-500">
                {filteredTreeRoots.length} 根节点
              </span>
            </div>

            {/* Tree view */}
            <div className="flex-1 overflow-auto px-2 py-2">
              {filteredTreeRoots.length === 0 ? (
                <p className="mt-8 text-center text-sm text-slate-500">
                  {worldSearch ? "未找到匹配的地点" : "暂无世界观数据"}
                </p>
              ) : (
                filteredTreeRoots.map((root) => (
                  <TreeNodeView
                    key={root.name}
                    node={root}
                    depth={0}
                    onSelect={handleWorldNodeSelect}
                    expandedNodes={expandedNodes}
                    toggleExpand={toggleExpand}
                  />
                ))
              )}
            </div>
          </div>
        )}
      </div>

      {/* Concept Popup */}
      {conceptPopup && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/30" onClick={() => setConceptPopup(null)}>
          <div className="w-96 max-w-[90vw] rounded-lg border border-slate-700 bg-slate-800 p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="size-2.5 rounded-full bg-gray-500" />
                <h4 className="text-lg font-bold text-white">{conceptPopup.name}</h4>
              </div>
              <span className="rounded bg-slate-700 px-2 py-0.5 text-[10px] text-slate-400">
                {conceptPopup.category}
              </span>
            </div>
            <p className="mb-4 text-sm leading-relaxed text-slate-300">
              {conceptPopup.definition}
            </p>
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>
                首次出现: 第{conceptPopup.first_chapter}回
                {conceptPopup.chapter_count ? ` · 出现 ${conceptPopup.chapter_count} 次` : ""}
              </span>
              <button
                onClick={() => setConceptPopup(null)}
                className="rounded border border-slate-600 px-3 py-1 text-slate-400 hover:text-white transition"
              >
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
