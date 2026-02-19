import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  deleteWorldStructureOverride,
  fetchWorldStructure,
  fetchWorldStructureOverrides,
  saveWorldStructureOverrides,
} from "@/api/client"
import type {
  OverrideType,
  WorldStructureData,
  WorldStructureOverride,
  WorldStructurePortal,
} from "@/api/types"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

// ── Constants ──────────────────────────────

const TIER_OPTIONS = [
  { value: "continent", label: "大洲" },
  { value: "region", label: "区域" },
  { value: "province", label: "省/路" },
  { value: "prefecture", label: "州/府" },
  { value: "city", label: "城市" },
  { value: "town", label: "城镇" },
  { value: "village", label: "村庄" },
  { value: "landmark", label: "地标" },
  { value: "building", label: "建筑" },
  { value: "room", label: "房间" },
]

const TIER_LABELS: Record<string, string> = Object.fromEntries(
  TIER_OPTIONS.map((t) => [t.value, t.label]),
)

type TabId = "tree" | "portals" | "overrides"

// ── Main Component ──────────────────────────

interface WorldStructureEditorProps {
  novelId: string
  open: boolean
  onClose: () => void
  onStructureChanged: () => void
}

export function WorldStructureEditor({
  novelId,
  open,
  onClose,
  onStructureChanged,
}: WorldStructureEditorProps) {
  const [ws, setWs] = useState<WorldStructureData | null>(null)
  const [overrides, setOverrides] = useState<WorldStructureOverride[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<TabId>("tree")
  const [selectedLocation, setSelectedLocation] = useState<string | null>(null)
  const [search, setSearch] = useState("")

  // Pending changes: key = "type:locationName", value = override payload
  const [pendingChanges, setPendingChanges] = useState<
    Map<string, { type: OverrideType; key: string; json: Record<string, unknown> }>
  >(new Map())

  // Portal pending state (kept from original)
  const [pendingPortalAdds, setPendingPortalAdds] = useState<WorldStructurePortal[]>([])
  const [pendingPortalDeletes, setPendingPortalDeletes] = useState<Set<string>>(new Set())
  const [newPortal, setNewPortal] = useState({
    name: "",
    source_layer: "overworld",
    source_location: "",
    target_layer: "",
    target_location: "",
    is_bidirectional: true,
  })

  // Load data
  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    Promise.all([fetchWorldStructure(novelId), fetchWorldStructureOverrides(novelId)])
      .then(([wsData, ovData]) => {
        if (cancelled) return
        setWs(wsData)
        setOverrides(ovData.overrides)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [novelId, open])

  // Reset pending on data reload
  useEffect(() => {
    setPendingChanges(new Map())
    setPendingPortalAdds([])
    setPendingPortalDeletes(new Set())
    setSelectedLocation(null)
  }, [ws])

  const hasPendingChanges =
    pendingChanges.size > 0 || pendingPortalAdds.length > 0 || pendingPortalDeletes.size > 0

  // Overridden keys lookup
  const overriddenKeys = useMemo(() => {
    const set = new Set<string>()
    for (const ov of overrides) set.add(`${ov.override_type}:${ov.override_key}`)
    return set
  }, [overrides])

  // Save all pending changes
  const handleSave = useCallback(async () => {
    if (!hasPendingChanges) return
    setSaving(true)

    const batch: { override_type: OverrideType; override_key: string; override_json: Record<string, unknown> }[] = []

    for (const change of pendingChanges.values()) {
      batch.push({ override_type: change.type, override_key: change.key, override_json: change.json })
    }
    for (const portal of pendingPortalAdds) {
      batch.push({ override_type: "add_portal", override_key: portal.name, override_json: portal as unknown as Record<string, unknown> })
    }
    for (const portalName of pendingPortalDeletes) {
      batch.push({ override_type: "delete_portal", override_key: portalName, override_json: {} })
    }

    try {
      const updatedWs = await saveWorldStructureOverrides(novelId, batch)
      setWs(updatedWs)
      const ovData = await fetchWorldStructureOverrides(novelId)
      setOverrides(ovData.overrides)
      onStructureChanged()
      showToast("保存成功")
    } catch {
      showToast("保存失败")
    } finally {
      setSaving(false)
    }
  }, [hasPendingChanges, pendingChanges, pendingPortalAdds, pendingPortalDeletes, novelId, onStructureChanged])

  // Delete a specific override
  const handleDeleteOverride = useCallback(
    async (overrideId: number) => {
      try {
        const updatedWs = await deleteWorldStructureOverride(novelId, overrideId)
        setWs(updatedWs)
        setOverrides((prev) => prev.filter((o) => o.id !== overrideId))
        onStructureChanged()
        showToast("已重置为 AI 生成")
      } catch {
        showToast("删除失败")
      }
    },
    [novelId, onStructureChanged],
  )

  // Field change handler for detail panel
  const handleFieldChange = useCallback(
    (locName: string, field: "parent" | "region" | "layer" | "tier", value: string) => {
      const typeMap: Record<string, OverrideType> = {
        parent: "location_parent",
        region: "location_region",
        layer: "location_layer",
        tier: "location_tier",
      }
      const jsonMap: Record<string, Record<string, unknown>> = {
        parent: { parent: value },
        region: { region: value },
        layer: { layer_id: value },
        tier: { tier: value },
      }
      setPendingChanges((prev) => {
        const next = new Map(prev)
        next.set(`${field}:${locName}`, {
          type: typeMap[field],
          key: locName,
          json: jsonMap[field],
        })
        return next
      })
    },
    [],
  )

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 2500)
  }

  // Close on Escape
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-40 bg-black/20" onClick={onClose} />

      {/* Drawer */}
      <div className="fixed top-0 right-0 z-50 flex h-screen w-[440px] flex-col border-l bg-background shadow-lg">
        {/* Header */}
        <div className="flex items-center justify-between border-b px-4 py-3">
          <h2 className="text-sm font-medium">编辑世界结构</h2>
          <div className="flex items-center gap-2">
            {hasPendingChanges && (
              <Button variant="default" size="sm" onClick={handleSave} disabled={saving}>
                {saving ? "保存中..." : `保存 (${pendingChanges.size + pendingPortalAdds.length + pendingPortalDeletes.size})`}
              </Button>
            )}
            <Button variant="ghost" size="icon-xs" onClick={onClose}>
              <XIcon className="size-4" />
            </Button>
          </div>
        </div>

        {/* Search */}
        <div className="px-4 py-2 border-b">
          <Input
            placeholder="搜索地点..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-7 text-xs"
          />
        </div>

        {/* Tabs */}
        <div className="flex border-b">
          {([
            ["tree", "地点层级"],
            ["portals", `传送门 (${ws?.portals?.length ?? 0})`],
            ["overrides", `覆盖记录 (${overrides.length})`],
          ] as [TabId, string][]).map(([id, label]) => (
            <button
              key={id}
              className={cn(
                "flex-1 px-3 py-2 text-xs font-medium transition-colors",
                activeTab === id
                  ? "border-b-2 border-primary text-primary"
                  : "text-muted-foreground hover:text-foreground",
              )}
              onClick={() => setActiveTab(id)}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex flex-1 flex-col min-h-0">
          {loading && (
            <div className="flex h-32 items-center justify-center">
              <p className="text-muted-foreground text-sm">加载中...</p>
            </div>
          )}

          {!loading && ws && activeTab === "tree" && (
            <LocationTreeTab
              ws={ws}
              search={search}
              selectedLocation={selectedLocation}
              overriddenKeys={overriddenKeys}
              pendingChanges={pendingChanges}
              onSelect={setSelectedLocation}
              onFieldChange={handleFieldChange}
              overrides={overrides}
              onDeleteOverride={handleDeleteOverride}
            />
          )}

          {!loading && ws && activeTab === "portals" && (
            <PortalEditor
              portals={ws.portals}
              layers={ws.layers.map((l) => ({ id: l.layer_id, name: l.name }))}
              pendingAdds={pendingPortalAdds}
              pendingDeletes={pendingPortalDeletes}
              overriddenKeys={overriddenKeys}
              overrides={overrides}
              newPortal={newPortal}
              onNewPortalChange={setNewPortal}
              onAddPortal={() => {
                if (!newPortal.name || !newPortal.target_layer) return
                setPendingPortalAdds((prev) => [...prev, { ...newPortal, first_chapter: null }])
                setNewPortal({ name: "", source_layer: "overworld", source_location: "", target_layer: "", target_location: "", is_bidirectional: true })
              }}
              onDeletePortal={(name) => setPendingPortalDeletes((prev) => new Set([...prev, name]))}
              onDeleteOverride={handleDeleteOverride}
            />
          )}

          {!loading && ws && activeTab === "overrides" && (
            <OverrideHistoryTab overrides={overrides} onDelete={handleDeleteOverride} />
          )}
        </div>

        {/* Toast */}
        {toast && (
          <div className="absolute bottom-4 left-1/2 -translate-x-1/2 rounded-lg border bg-background px-3 py-2 text-xs shadow-lg">
            {toast}
          </div>
        )}
      </div>
    </>
  )
}

// ── Location Tree Tab ──────────────────────

interface TreeNode {
  name: string
  depth: number
  tier: string
  childCount: number
  hasChildren: boolean
  isExpanded: boolean
  isOverridden: boolean
  matchesSearch: boolean
}

function LocationTreeTab({
  ws,
  search,
  selectedLocation,
  overriddenKeys,
  pendingChanges,
  onSelect,
  onFieldChange,
  overrides,
  onDeleteOverride,
}: {
  ws: WorldStructureData
  search: string
  selectedLocation: string | null
  overriddenKeys: Set<string>
  pendingChanges: Map<string, { type: OverrideType; key: string; json: Record<string, unknown> }>
  onSelect: (name: string | null) => void
  onFieldChange: (locName: string, field: "parent" | "region" | "layer" | "tier", value: string) => void
  overrides: WorldStructureOverride[]
  onDeleteOverride: (id: number) => void
}) {
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set())
  const [initialized, setInitialized] = useState(false)

  // Build tree structure (with cycle detection)
  const { roots, childrenMap, allNames, safeParents } = useMemo(() => {
    const rawParents = ws.location_parents ?? {}
    const tiers = ws.location_tiers ?? {}

    // Copy parents and break any cycles so the tree is acyclic
    const parents: Record<string, string> = { ...rawParents }
    const checked = new Set<string>()
    for (const start of Object.keys(parents)) {
      if (checked.has(start)) continue
      const visited = new Set<string>()
      let node: string | undefined = start
      while (node && parents[node] && !visited.has(node)) {
        visited.add(node)
        node = parents[node]
      }
      if (node && visited.has(node)) {
        // Cycle detected — break the edge FROM node to its parent
        delete parents[node]
      }
      for (const v of visited) checked.add(v)
    }

    const allNames = new Set([
      ...Object.keys(parents),
      ...Object.values(parents),
      ...Object.keys(rawParents),
      ...Object.values(rawParents),
      ...Object.keys(tiers),
    ])

    const childrenMap = new Map<string, string[]>()
    for (const [child, parent] of Object.entries(parents)) {
      const children = childrenMap.get(parent) ?? []
      children.push(child)
      childrenMap.set(parent, children)
    }
    for (const [, children] of childrenMap) children.sort()

    const childSet = new Set(Object.keys(parents))
    const roots = [...allNames].filter((n) => !childSet.has(n)).sort()

    return { roots, childrenMap, allNames, safeParents: parents }
  }, [ws])

  // Default expand: roots + first 2 levels for a nested appearance
  useEffect(() => {
    if (initialized || roots.length === 0) return
    const defaultExpanded = new Set<string>()
    for (const root of roots) {
      defaultExpanded.add(root)
      // Also expand depth-1 children
      for (const child of childrenMap.get(root) ?? []) {
        defaultExpanded.add(child)
      }
    }
    setExpandedNodes(defaultExpanded)
    setInitialized(true)
  }, [roots, childrenMap, initialized])

  // Search: find matching nodes + their ancestors
  const searchMatchedNodes = useMemo(() => {
    if (!search.trim()) return null
    const q = search.toLowerCase()
    const matched = new Set<string>()
    const ancestors = new Set<string>()

    for (const name of allNames) {
      if (name.toLowerCase().includes(q)) {
        matched.add(name)
        // Walk up to root (using cycle-safe parents)
        let cur = name
        const walked = new Set<string>()
        while (safeParents[cur] && !walked.has(cur)) {
          walked.add(cur)
          ancestors.add(safeParents[cur])
          cur = safeParents[cur]
        }
      }
    }
    return { matched, ancestors }
  }, [search, allNames, safeParents])

  // Flatten visible tree nodes
  const treeNodes = useMemo(() => {
    const tiers = ws.location_tiers ?? {}
    const result: TreeNode[] = []
    const visited = new Set<string>()
    const isSearching = searchMatchedNodes !== null

    const dfs = (name: string, depth: number) => {
      if (visited.has(name)) return
      visited.add(name)

      const children = childrenMap.get(name) ?? []
      const isOverridden =
        overriddenKeys.has(`location_parent:${name}`) ||
        overriddenKeys.has(`location_region:${name}`) ||
        overriddenKeys.has(`location_tier:${name}`)
      const matchesSearch = isSearching
        ? searchMatchedNodes!.matched.has(name) || searchMatchedNodes!.ancestors.has(name)
        : true

      if (!matchesSearch) return

      const isExpanded = isSearching
        ? searchMatchedNodes!.ancestors.has(name) || searchMatchedNodes!.matched.has(name)
        : expandedNodes.has(name)

      result.push({
        name,
        depth,
        tier: tiers[name] ?? "",
        childCount: children.length,
        hasChildren: children.length > 0,
        isExpanded,
        isOverridden,
        matchesSearch: isSearching ? searchMatchedNodes!.matched.has(name) : false,
      })

      if (isExpanded) {
        for (const child of children) {
          dfs(child, depth + 1)
        }
      }
    }

    for (const root of roots) dfs(root, 0)

    // Add true orphans (no parent in the tree) — skip nodes hidden under collapsed parents
    for (const name of allNames) {
      if (!visited.has(name)) {
        const parent = safeParents[name]
        if (parent && allNames.has(parent)) continue // hidden under a collapsed parent
        dfs(name, 0)
      }
    }

    return result
  }, [roots, childrenMap, allNames, safeParents, expandedNodes, ws.location_tiers, overriddenKeys, searchMatchedNodes])

  const toggleExpand = useCallback((name: string) => {
    setExpandedNodes((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }, [])

  return (
    <>
      {/* Tree browser */}
      <div className="flex-1 overflow-y-auto">
        {treeNodes.length === 0 && (
          <p className="text-muted-foreground text-xs text-center py-8">
            {search ? "无匹配结果" : "暂无地点数据"}
          </p>
        )}
        {treeNodes.map((node) => (
          <div
            key={node.name}
            className={cn(
              "flex items-center gap-1 py-1 pr-3 hover:bg-muted/40 cursor-pointer transition-colors text-xs",
              selectedLocation === node.name && "bg-primary/10 text-primary",
              node.matchesSearch && "font-medium",
            )}
            style={{ paddingLeft: `${node.depth * 16 + 8}px` }}
            onClick={() => onSelect(node.name)}
          >
            {node.hasChildren ? (
              <button
                className="w-4 text-muted-foreground hover:text-foreground flex-shrink-0"
                onClick={(e) => { e.stopPropagation(); toggleExpand(node.name) }}
              >
                {node.isExpanded ? "▾" : "▸"}
              </button>
            ) : (
              <span className="w-4 flex-shrink-0" />
            )}
            {node.isOverridden && (
              <span className="size-1.5 rounded-full bg-amber-500 flex-shrink-0" />
            )}
            <span className="truncate flex-1">{node.name}</span>
            {node.childCount > 0 && (
              <span className="text-[10px] text-muted-foreground flex-shrink-0">
                {node.childCount}
              </span>
            )}
            {node.tier && (
              <span className="text-[10px] text-muted-foreground bg-muted px-1 rounded flex-shrink-0">
                {TIER_LABELS[node.tier] ?? node.tier}
              </span>
            )}
          </div>
        ))}
      </div>

      {/* Detail panel */}
      {selectedLocation && (
        <DetailPanel
          locationName={selectedLocation}
          ws={ws}
          pendingChanges={pendingChanges}
          overrides={overrides}
          onFieldChange={onFieldChange}
          onDeleteOverride={onDeleteOverride}
          onClose={() => onSelect(null)}
        />
      )}
    </>
  )
}

// ── Detail Panel ──────────────────────────

function DetailPanel({
  locationName,
  ws,
  pendingChanges,
  overrides,
  onFieldChange,
  onDeleteOverride,
  onClose,
}: {
  locationName: string
  ws: WorldStructureData
  pendingChanges: Map<string, { type: OverrideType; key: string; json: Record<string, unknown> }>
  overrides: WorldStructureOverride[]
  onFieldChange: (locName: string, field: "parent" | "region" | "layer" | "tier", value: string) => void
  onDeleteOverride: (id: number) => void
  onClose: () => void
}) {
  const currentParent =
    (pendingChanges.get(`parent:${locationName}`)?.json?.parent as string) ??
    ws.location_parents?.[locationName] ?? ""
  const currentRegion =
    (pendingChanges.get(`region:${locationName}`)?.json?.region as string) ??
    ws.location_region_map?.[locationName] ?? ""
  const currentLayer =
    (pendingChanges.get(`layer:${locationName}`)?.json?.layer_id as string) ??
    ws.location_layer_map?.[locationName] ?? "overworld"
  const currentTier =
    (pendingChanges.get(`tier:${locationName}`)?.json?.tier as string) ??
    ws.location_tiers?.[locationName] ?? ""

  const allLocationNames = useMemo(() => {
    const names = new Set([
      ...Object.keys(ws.location_parents ?? {}),
      ...Object.values(ws.location_parents ?? {}),
      ...Object.keys(ws.location_tiers ?? {}),
    ])
    names.delete(locationName) // can't be parent of self
    return [...names].sort()
  }, [ws, locationName])

  const allRegionNames = useMemo(() => {
    const regions = new Set<string>()
    for (const layer of ws.layers) {
      for (const r of layer.regions) regions.add(r.name)
    }
    return [...regions].sort()
  }, [ws.layers])

  const layerOptions = useMemo(
    () => ws.layers.map((l) => ({ value: l.layer_id, label: l.name })),
    [ws.layers],
  )

  // Check if any field has an override
  const relevantOverrides = overrides.filter(
    (ov) =>
      ov.override_key === locationName &&
      ["location_parent", "location_region", "location_layer", "location_tier"].includes(ov.override_type),
  )

  return (
    <div className="border-t bg-muted/20 p-3 space-y-2 flex-shrink-0">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium truncate">{locationName}</h4>
        <Button variant="ghost" size="icon-xs" onClick={onClose}>
          <XIcon className="size-3.5" />
        </Button>
      </div>

      <SearchableField
        label="父级"
        value={currentParent}
        options={allLocationNames}
        onChange={(v) => onFieldChange(locationName, "parent", v)}
      />
      <SearchableField
        label="区域"
        value={currentRegion}
        options={allRegionNames}
        onChange={(v) => onFieldChange(locationName, "region", v)}
      />

      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground w-10 flex-shrink-0">层</span>
        <Select value={currentLayer} onValueChange={(v) => onFieldChange(locationName, "layer", v)}>
          <SelectTrigger size="sm" className="h-7 flex-1 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {layerOptions.map((l) => (
              <SelectItem key={l.value} value={l.value}>{l.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground w-10 flex-shrink-0">类型</span>
        <Select value={currentTier} onValueChange={(v) => onFieldChange(locationName, "tier", v)}>
          <SelectTrigger size="sm" className="h-7 flex-1 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {TIER_OPTIONS.map((t) => (
              <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {relevantOverrides.length > 0 && (
        <div className="flex items-center gap-2 pt-1">
          <span className="text-[10px] text-amber-600">
            {relevantOverrides.length} 项已修改
          </span>
          {relevantOverrides.map((ov) => (
            <Button
              key={ov.id}
              variant="ghost"
              size="xs"
              className="text-[10px] h-5"
              onClick={() => onDeleteOverride(ov.id)}
            >
              重置 {ov.override_type.replace("location_", "")}
            </Button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Searchable Field (lightweight combobox) ──

function SearchableField({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: string[]
  onChange: (value: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [inputValue, setInputValue] = useState("")
  const containerRef = useRef<HTMLDivElement>(null)

  const filtered = useMemo(() => {
    if (!inputValue) return options.slice(0, 50)
    const q = inputValue.toLowerCase()
    return options.filter((o) => o.toLowerCase().includes(q)).slice(0, 50)
  }, [options, inputValue])

  // Close on click outside
  useEffect(() => {
    if (!open) return
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClick)
    return () => document.removeEventListener("mousedown", handleClick)
  }, [open])

  return (
    <div className="flex items-center gap-2" ref={containerRef}>
      <span className="text-xs text-muted-foreground w-10 flex-shrink-0">{label}</span>
      <div className="relative flex-1">
        <button
          type="button"
          className="flex h-7 w-full items-center justify-between rounded-md border bg-background px-2 text-xs hover:bg-muted/50"
          onClick={() => { setOpen(!open); setInputValue("") }}
        >
          <span className="truncate">{value || "无"}</span>
          <span className="text-muted-foreground ml-1">▾</span>
        </button>

        {open && (
          <div className="absolute top-8 left-0 right-0 z-50 rounded-md border bg-popover shadow-md">
            <input
              autoFocus
              type="text"
              placeholder="输入搜索..."
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              className="w-full border-b px-2 py-1.5 text-xs bg-transparent outline-none"
            />
            <div className="max-h-40 overflow-y-auto">
              {filtered.length === 0 && (
                <div className="px-2 py-2 text-xs text-muted-foreground">无匹配</div>
              )}
              {filtered.map((opt) => (
                <button
                  key={opt}
                  type="button"
                  className={cn(
                    "w-full text-left px-2 py-1 text-xs hover:bg-muted/60 transition-colors",
                    opt === value && "bg-primary/10 text-primary font-medium",
                  )}
                  onClick={() => { onChange(opt); setOpen(false) }}
                >
                  {opt}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Portal Editor (kept from original, cleaned up) ──

function PortalEditor({
  portals,
  layers,
  pendingAdds,
  pendingDeletes,
  overriddenKeys,
  overrides,
  newPortal,
  onNewPortalChange,
  onAddPortal,
  onDeletePortal,
  onDeleteOverride,
}: {
  portals: WorldStructurePortal[]
  layers: { id: string; name: string }[]
  pendingAdds: WorldStructurePortal[]
  pendingDeletes: Set<string>
  overriddenKeys: Set<string>
  overrides: WorldStructureOverride[]
  newPortal: {
    name: string; source_layer: string; source_location: string
    target_layer: string; target_location: string; is_bidirectional: boolean
  }
  onNewPortalChange: (v: typeof newPortal) => void
  onAddPortal: () => void
  onDeletePortal: (name: string) => void
  onDeleteOverride: (id: number) => void
}) {
  return (
    <div className="flex-1 overflow-y-auto p-4">
      <h4 className="text-xs font-medium mb-2">已有传送门</h4>
      <div className="space-y-1 mb-4">
        {portals.length === 0 && pendingAdds.length === 0 && (
          <p className="text-muted-foreground text-xs text-center py-4">暂无传送门</p>
        )}
        {portals.map((portal) => {
          const isDeleted = pendingDeletes.has(portal.name)
          const isOverridden = overriddenKeys.has(`add_portal:${portal.name}`)
          const overrideItem = isOverridden
            ? overrides.find((o) => o.override_type === "add_portal" && o.override_key === portal.name)
            : null
          const srcLayer = layers.find((l) => l.id === portal.source_layer)
          const tgtLayer = layers.find((l) => l.id === portal.target_layer)

          return (
            <div
              key={portal.name}
              className={cn(
                "flex items-center gap-2 rounded border px-2 py-1.5",
                isDeleted && "opacity-30 line-through",
                isOverridden && "border-amber-300 bg-amber-50 dark:bg-amber-950/20",
              )}
            >
              <div className="min-w-0 flex-1">
                <div className="text-xs font-medium truncate">
                  {portal.name} {portal.is_bidirectional ? "(双向)" : "(单向)"}
                </div>
                <div className="text-[10px] text-muted-foreground truncate">
                  {srcLayer?.name ?? portal.source_layer}
                  {portal.source_location ? `(${portal.source_location})` : ""} →{" "}
                  {tgtLayer?.name ?? portal.target_layer}
                  {portal.target_location ? `(${portal.target_location})` : ""}
                </div>
              </div>
              {isOverridden && <span className="text-[10px] text-amber-600 flex-shrink-0">已修改</span>}
              {isOverridden && overrideItem && (
                <Button variant="ghost" size="icon-xs" title="重置" onClick={() => onDeleteOverride(overrideItem.id)}>
                  <ResetIcon className="size-3.5 text-muted-foreground" />
                </Button>
              )}
              {!isDeleted && (
                <Button variant="ghost" size="icon-xs" title="删除" onClick={() => onDeletePortal(portal.name)}>
                  <XIcon className="size-3.5 text-destructive" />
                </Button>
              )}
            </div>
          )
        })}
        {pendingAdds.map((portal) => (
          <div key={portal.name} className="flex items-center gap-2 rounded border border-blue-300 bg-blue-50 px-2 py-1.5 dark:bg-blue-950/20">
            <div className="min-w-0 flex-1">
              <div className="text-xs font-medium truncate">{portal.name} (新增)</div>
              <div className="text-[10px] text-muted-foreground truncate">{portal.source_layer} → {portal.target_layer}</div>
            </div>
            <span className="text-[10px] text-blue-600 flex-shrink-0">待保存</span>
          </div>
        ))}
      </div>

      <h4 className="text-xs font-medium mb-2">添加传送门</h4>
      <div className="space-y-2 rounded border p-3">
        <Input placeholder="传送门名称" value={newPortal.name}
          onChange={(e) => onNewPortalChange({ ...newPortal, name: e.target.value })} className="h-7 text-xs" />
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[10px] text-muted-foreground">源层</label>
            <Select value={newPortal.source_layer} onValueChange={(val) => onNewPortalChange({ ...newPortal, source_layer: val })}>
              <SelectTrigger size="sm" className="h-7 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>{layers.map((l) => (<SelectItem key={l.id} value={l.id}>{l.name}</SelectItem>))}</SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-[10px] text-muted-foreground">源地点</label>
            <Input placeholder="可选" value={newPortal.source_location}
              onChange={(e) => onNewPortalChange({ ...newPortal, source_location: e.target.value })} className="h-7 text-xs" />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[10px] text-muted-foreground">目标层</label>
            <Select value={newPortal.target_layer} onValueChange={(val) => onNewPortalChange({ ...newPortal, target_layer: val })}>
              <SelectTrigger size="sm" className="h-7 text-xs"><SelectValue placeholder="选择..." /></SelectTrigger>
              <SelectContent>{layers.map((l) => (<SelectItem key={l.id} value={l.id}>{l.name}</SelectItem>))}</SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-[10px] text-muted-foreground">目标地点</label>
            <Input placeholder="可选" value={newPortal.target_location}
              onChange={(e) => onNewPortalChange({ ...newPortal, target_location: e.target.value })} className="h-7 text-xs" />
          </div>
        </div>
        <div className="flex items-center justify-between">
          <label className="flex items-center gap-1.5 text-xs">
            <input type="checkbox" checked={newPortal.is_bidirectional}
              onChange={(e) => onNewPortalChange({ ...newPortal, is_bidirectional: e.target.checked })} className="rounded" />
            双向传送
          </label>
          <Button variant="outline" size="sm" onClick={onAddPortal}
            disabled={!newPortal.name || !newPortal.target_layer}>添加</Button>
        </div>
      </div>
    </div>
  )
}

// ── Override History Tab ──────────────────

function OverrideHistoryTab({
  overrides,
  onDelete,
}: {
  overrides: WorldStructureOverride[]
  onDelete: (id: number) => void
}) {
  const TYPE_LABELS: Record<string, string> = {
    location_region: "区域",
    location_layer: "层",
    location_parent: "父级",
    location_tier: "类型",
    add_portal: "添加传送门",
    delete_portal: "删除传送门",
  }

  const sorted = useMemo(
    () => [...overrides].sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [overrides],
  )

  return (
    <div className="flex-1 overflow-y-auto p-4">
      {sorted.length === 0 && (
        <p className="text-muted-foreground text-xs text-center py-8">暂无覆盖记录</p>
      )}
      <div className="space-y-1">
        {sorted.map((ov) => (
          <div key={ov.id} className="flex items-center gap-2 rounded border px-2 py-1.5">
            <div className="flex-1 min-w-0">
              <div className="text-xs font-medium truncate">{ov.override_key}</div>
              <div className="text-[10px] text-muted-foreground">
                {TYPE_LABELS[ov.override_type] ?? ov.override_type} ·{" "}
                {ov.override_json && Object.keys(ov.override_json).length > 0
                  ? Object.values(ov.override_json).join(", ")
                  : ""}{" "}
                · {ov.created_at}
              </div>
            </div>
            <Button variant="ghost" size="icon-xs" title="删除覆盖" onClick={() => onDelete(ov.id)}>
              <ResetIcon className="size-3.5 text-muted-foreground" />
            </Button>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Icons ──────────────────────────────

function XIcon({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M18 6 6 18" /><path d="m6 6 12 12" />
    </svg>
  )
}

function ResetIcon({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" /><path d="M3 3v5h5" />
    </svg>
  )
}
