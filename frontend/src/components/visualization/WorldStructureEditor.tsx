import { useCallback, useEffect, useMemo, useState } from "react"
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
  const [activeTab, setActiveTab] = useState<"regions" | "portals">("regions")

  // Pending changes (not yet saved)
  const [pendingRegionChanges, setPendingRegionChanges] = useState<
    Map<string, string>
  >(new Map())
  const [pendingPortalAdds, setPendingPortalAdds] = useState<
    WorldStructurePortal[]
  >([])
  const [pendingPortalDeletes, setPendingPortalDeletes] = useState<
    Set<string>
  >(new Set())

  // New portal form
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

    Promise.all([
      fetchWorldStructure(novelId),
      fetchWorldStructureOverrides(novelId),
    ])
      .then(([wsData, ovData]) => {
        if (cancelled) return
        setWs(wsData)
        setOverrides(ovData.overrides)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [novelId, open])

  // Reset pending changes when data loads
  useEffect(() => {
    setPendingRegionChanges(new Map())
    setPendingPortalAdds([])
    setPendingPortalDeletes(new Set())
  }, [ws])

  // All region names for the dropdown
  const allRegions = useMemo(() => {
    if (!ws) return []
    const regions: { name: string; layerId: string }[] = []
    for (const layer of ws.layers) {
      for (const region of layer.regions) {
        regions.push({ name: region.name, layerId: layer.layer_id })
      }
    }
    return regions
  }, [ws])

  // All location names with their current region
  const locationEntries = useMemo(() => {
    if (!ws) return []
    const entries: { name: string; region: string }[] = []
    const regionMap = ws.location_region_map ?? {}
    for (const name of Object.keys(regionMap).sort()) {
      entries.push({ name, region: regionMap[name] })
    }
    return entries
  }, [ws])

  // Overridden keys lookup
  const overriddenKeys = useMemo(() => {
    const set = new Set<string>()
    for (const ov of overrides) {
      set.add(`${ov.override_type}:${ov.override_key}`)
    }
    return set
  }, [overrides])

  const hasPendingChanges =
    pendingRegionChanges.size > 0 ||
    pendingPortalAdds.length > 0 ||
    pendingPortalDeletes.size > 0

  // Save all pending changes
  const handleSave = useCallback(async () => {
    if (!hasPendingChanges) return
    setSaving(true)

    const batch: {
      override_type: OverrideType
      override_key: string
      override_json: Record<string, unknown>
    }[] = []

    // Region changes
    for (const [locName, regionName] of pendingRegionChanges) {
      batch.push({
        override_type: "location_region",
        override_key: locName,
        override_json: { region: regionName },
      })
    }

    // Portal adds
    for (const portal of pendingPortalAdds) {
      batch.push({
        override_type: "add_portal",
        override_key: portal.name,
        override_json: portal as unknown as Record<string, unknown>,
      })
    }

    // Portal deletes
    for (const portalName of pendingPortalDeletes) {
      batch.push({
        override_type: "delete_portal",
        override_key: portalName,
        override_json: {},
      })
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
  }, [
    hasPendingChanges,
    pendingRegionChanges,
    pendingPortalAdds,
    pendingPortalDeletes,
    novelId,
    onStructureChanged,
  ])

  // Delete a specific override
  const handleDeleteOverride = useCallback(
    async (overrideId: number) => {
      try {
        const updatedWs = await deleteWorldStructureOverride(
          novelId,
          overrideId,
        )
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

  // Add a new portal
  const handleAddPortal = useCallback(() => {
    if (!newPortal.name || !newPortal.target_layer) return
    setPendingPortalAdds((prev) => [
      ...prev,
      {
        ...newPortal,
        first_chapter: null,
      },
    ])
    setNewPortal({
      name: "",
      source_layer: "overworld",
      source_location: "",
      target_layer: "",
      target_location: "",
      is_bidirectional: true,
    })
  }, [newPortal])

  // Mark a portal for deletion
  const handleDeletePortal = useCallback((portalName: string) => {
    setPendingPortalDeletes((prev) => new Set([...prev, portalName]))
  }, [])

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 2000)
  }

  // Close on Escape
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose()
    }
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
              <Button
                variant="default"
                size="sm"
                onClick={handleSave}
                disabled={saving}
              >
                {saving ? "保存中..." : "保存"}
              </Button>
            )}
            <Button variant="ghost" size="icon-xs" onClick={onClose}>
              <XIcon className="size-4" />
            </Button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b">
          <button
            className={cn(
              "flex-1 px-4 py-2 text-xs font-medium transition-colors",
              activeTab === "regions"
                ? "border-b-2 border-primary text-primary"
                : "text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setActiveTab("regions")}
          >
            区域归属 ({locationEntries.length})
          </button>
          <button
            className={cn(
              "flex-1 px-4 py-2 text-xs font-medium transition-colors",
              activeTab === "portals"
                ? "border-b-2 border-primary text-primary"
                : "text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setActiveTab("portals")}
          >
            传送门 ({ws?.portals?.length ?? 0})
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {loading && (
            <div className="flex h-32 items-center justify-center">
              <p className="text-muted-foreground text-sm">加载中...</p>
            </div>
          )}

          {!loading && ws && activeTab === "regions" && (
            <RegionEditor
              entries={locationEntries}
              regions={allRegions}
              pendingChanges={pendingRegionChanges}
              overriddenKeys={overriddenKeys}
              overrides={overrides}
              onRegionChange={(locName, region) => {
                setPendingRegionChanges((prev) => {
                  const next = new Map(prev)
                  next.set(locName, region)
                  return next
                })
              }}
              onDeleteOverride={handleDeleteOverride}
            />
          )}

          {!loading && ws && activeTab === "portals" && (
            <PortalEditor
              portals={ws.portals}
              layers={ws.layers.map((l) => ({
                id: l.layer_id,
                name: l.name,
              }))}
              pendingAdds={pendingPortalAdds}
              pendingDeletes={pendingPortalDeletes}
              overriddenKeys={overriddenKeys}
              overrides={overrides}
              newPortal={newPortal}
              onNewPortalChange={setNewPortal}
              onAddPortal={handleAddPortal}
              onDeletePortal={handleDeletePortal}
              onDeleteOverride={handleDeleteOverride}
            />
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

// ── Region Editor ──────────────────────────────

function RegionEditor({
  entries,
  regions,
  pendingChanges,
  overriddenKeys,
  overrides,
  onRegionChange,
  onDeleteOverride,
}: {
  entries: { name: string; region: string }[]
  regions: { name: string; layerId: string }[]
  pendingChanges: Map<string, string>
  overriddenKeys: Set<string>
  overrides: WorldStructureOverride[]
  onRegionChange: (locName: string, region: string) => void
  onDeleteOverride: (id: number) => void
}) {
  const [filter, setFilter] = useState("")

  const filtered = useMemo(() => {
    if (!filter) return entries
    const q = filter.toLowerCase()
    return entries.filter(
      (e) =>
        e.name.toLowerCase().includes(q) ||
        e.region.toLowerCase().includes(q),
    )
  }, [entries, filter])

  return (
    <div>
      <Input
        placeholder="搜索地点名称..."
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        className="mb-3 h-8 text-xs"
      />

      <div className="space-y-1">
        {filtered.map((entry) => {
          const isOverridden = overriddenKeys.has(
            `location_region:${entry.name}`,
          )
          const pendingRegion = pendingChanges.get(entry.name)
          const currentRegion = pendingRegion ?? entry.region
          const overrideItem = isOverridden
            ? overrides.find(
                (o) =>
                  o.override_type === "location_region" &&
                  o.override_key === entry.name,
              )
            : null

          return (
            <div
              key={entry.name}
              className={cn(
                "flex items-center gap-2 rounded px-2 py-1.5",
                isOverridden && "bg-amber-50 dark:bg-amber-950/20",
                pendingRegion && "bg-blue-50 dark:bg-blue-950/20",
              )}
            >
              <span className="min-w-0 flex-1 truncate text-xs">
                {entry.name}
              </span>

              {isOverridden && (
                <span className="text-[10px] text-amber-600 flex-shrink-0">
                  已修改
                </span>
              )}

              <Select
                value={currentRegion}
                onValueChange={(val) => onRegionChange(entry.name, val)}
              >
                <SelectTrigger size="sm" className="h-7 w-32 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {regions.map((r) => (
                    <SelectItem key={r.name} value={r.name}>
                      {r.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              {isOverridden && overrideItem && (
                <Button
                  variant="ghost"
                  size="icon-xs"
                  title="重置为 AI 生成"
                  onClick={() => onDeleteOverride(overrideItem.id)}
                >
                  <ResetIcon className="size-3.5 text-muted-foreground" />
                </Button>
              )}
            </div>
          )
        })}
      </div>

      {filtered.length === 0 && (
        <p className="text-muted-foreground text-xs text-center py-8">
          {entries.length === 0 ? "暂无地点数据" : "无匹配结果"}
        </p>
      )}
    </div>
  )
}

// ── Portal Editor ──────────────────────────────

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
    name: string
    source_layer: string
    source_location: string
    target_layer: string
    target_location: string
    is_bidirectional: boolean
  }
  onNewPortalChange: (v: typeof newPortal) => void
  onAddPortal: () => void
  onDeletePortal: (name: string) => void
  onDeleteOverride: (id: number) => void
}) {
  return (
    <div>
      {/* Existing portals */}
      <h4 className="text-xs font-medium mb-2">已有传送门</h4>
      <div className="space-y-1 mb-4">
        {portals.length === 0 && pendingAdds.length === 0 && (
          <p className="text-muted-foreground text-xs text-center py-4">
            暂无传送门
          </p>
        )}

        {portals.map((portal) => {
          const isDeleted = pendingDeletes.has(portal.name)
          const isOverridden = overriddenKeys.has(
            `add_portal:${portal.name}`,
          )
          const overrideItem = isOverridden
            ? overrides.find(
                (o) =>
                  o.override_type === "add_portal" &&
                  o.override_key === portal.name,
              )
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
                  {portal.name}
                  {portal.is_bidirectional ? " (双向)" : " (单向)"}
                </div>
                <div className="text-[10px] text-muted-foreground truncate">
                  {srcLayer?.name ?? portal.source_layer}
                  {portal.source_location
                    ? `(${portal.source_location})`
                    : ""}{" "}
                  → {tgtLayer?.name ?? portal.target_layer}
                  {portal.target_location
                    ? `(${portal.target_location})`
                    : ""}
                </div>
              </div>

              {isOverridden && (
                <span className="text-[10px] text-amber-600 flex-shrink-0">
                  已修改
                </span>
              )}

              {isOverridden && overrideItem && (
                <Button
                  variant="ghost"
                  size="icon-xs"
                  title="重置为 AI 生成"
                  onClick={() => onDeleteOverride(overrideItem.id)}
                >
                  <ResetIcon className="size-3.5 text-muted-foreground" />
                </Button>
              )}

              {!isDeleted && (
                <Button
                  variant="ghost"
                  size="icon-xs"
                  title="删除传送门"
                  onClick={() => onDeletePortal(portal.name)}
                >
                  <XIcon className="size-3.5 text-destructive" />
                </Button>
              )}
            </div>
          )
        })}

        {/* Pending additions */}
        {pendingAdds.map((portal) => (
          <div
            key={portal.name}
            className="flex items-center gap-2 rounded border border-blue-300 bg-blue-50 px-2 py-1.5 dark:bg-blue-950/20"
          >
            <div className="min-w-0 flex-1">
              <div className="text-xs font-medium truncate">
                {portal.name} (新增)
              </div>
              <div className="text-[10px] text-muted-foreground truncate">
                {portal.source_layer} → {portal.target_layer}
              </div>
            </div>
            <span className="text-[10px] text-blue-600 flex-shrink-0">
              待保存
            </span>
          </div>
        ))}
      </div>

      {/* Add portal form */}
      <h4 className="text-xs font-medium mb-2">添加传送门</h4>
      <div className="space-y-2 rounded border p-3">
        <Input
          placeholder="传送门名称"
          value={newPortal.name}
          onChange={(e) =>
            onNewPortalChange({ ...newPortal, name: e.target.value })
          }
          className="h-7 text-xs"
        />

        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[10px] text-muted-foreground">源层</label>
            <Select
              value={newPortal.source_layer}
              onValueChange={(val) =>
                onNewPortalChange({ ...newPortal, source_layer: val })
              }
            >
              <SelectTrigger size="sm" className="h-7 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {layers.map((l) => (
                  <SelectItem key={l.id} value={l.id}>
                    {l.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-[10px] text-muted-foreground">
              源地点
            </label>
            <Input
              placeholder="可选"
              value={newPortal.source_location}
              onChange={(e) =>
                onNewPortalChange({
                  ...newPortal,
                  source_location: e.target.value,
                })
              }
              className="h-7 text-xs"
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[10px] text-muted-foreground">
              目标层
            </label>
            <Select
              value={newPortal.target_layer}
              onValueChange={(val) =>
                onNewPortalChange({ ...newPortal, target_layer: val })
              }
            >
              <SelectTrigger size="sm" className="h-7 text-xs">
                <SelectValue placeholder="选择..." />
              </SelectTrigger>
              <SelectContent>
                {layers.map((l) => (
                  <SelectItem key={l.id} value={l.id}>
                    {l.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-[10px] text-muted-foreground">
              目标地点
            </label>
            <Input
              placeholder="可选"
              value={newPortal.target_location}
              onChange={(e) =>
                onNewPortalChange({
                  ...newPortal,
                  target_location: e.target.value,
                })
              }
              className="h-7 text-xs"
            />
          </div>
        </div>

        <div className="flex items-center justify-between">
          <label className="flex items-center gap-1.5 text-xs">
            <input
              type="checkbox"
              checked={newPortal.is_bidirectional}
              onChange={(e) =>
                onNewPortalChange({
                  ...newPortal,
                  is_bidirectional: e.target.checked,
                })
              }
              className="rounded"
            />
            双向传送
          </label>
          <Button
            variant="outline"
            size="sm"
            onClick={onAddPortal}
            disabled={!newPortal.name || !newPortal.target_layer}
          >
            添加
          </Button>
        </div>
      </div>
    </div>
  )
}

// ── Icons ──────────────────────────────────

function XIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  )
}

function ResetIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
      <path d="M3 3v5h5" />
    </svg>
  )
}
