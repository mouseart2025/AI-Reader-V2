import { useCallback, useEffect, useState } from "react"
import { MoreVerticalIcon, AlertTriangleIcon, PencilIcon } from "lucide-react"
import type { EntityProfile, EntityType, EntitySummary, PersonProfile } from "@/api/types"
import {
  fetchEntities,
  fetchEntityProfile,
  mergeAliases,
  splitAliases,
  renameEntity,
  listEntityOverrides,
  deleteEntityOverride,
} from "@/api/client"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu"

interface Props {
  novelId: string
  profile: EntityProfile
}

function aliasNames(p: EntityProfile): string[] {
  return p.type === "person" ? (p as PersonProfile).aliases.map((a) => a.name) : []
}

export function AliasEditControls({ novelId, profile }: Props) {
  const refresh = useEntityCardStore((s) => s.refresh)
  const replaceCurrent = useEntityCardStore((s) => s.replaceCurrent)

  const [mergeOpen, setMergeOpen] = useState(false)
  const [splitOpen, setSplitOpen] = useState(false)
  const [renameOpen, setRenameOpen] = useState(false)
  const [newName, setNewName] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // ── Merge state ──
  const [candidates, setCandidates] = useState<EntitySummary[]>([])
  const [query, setQuery] = useState("")
  const [target, setTarget] = useState<EntitySummary | null>(null)
  const [mergeMembers, setMergeMembers] = useState<string[]>([])
  const [canonical, setCanonical] = useState(profile.name)

  // ── Split state ──
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [splitDest, setSplitDest] = useState<string>("") // "" => new independent entity

  const reset = useCallback(() => {
    setError(null)
    setBusy(false)
    setTarget(null)
    setQuery("")
    setMergeMembers([])
    setCanonical(profile.name)
    setChecked(new Set())
    setSplitDest("")
    setNewName("")
  }, [profile.name])

  async function doRename() {
    const to = newName.trim()
    if (!to || to === profile.name) return
    setBusy(true)
    setError(null)
    try {
      await renameEntity(novelId, profile.name, to)
      setRenameOpen(false)
      reset()
      replaceCurrent(to, profile.type as EntityType)
    } catch (e) {
      setError(e instanceof Error ? e.message : "改名失败")
      setBusy(false)
    }
  }

  // Load same-type entities when a dialog needing a picker opens.
  useEffect(() => {
    if (!mergeOpen && !splitOpen) return
    let cancelled = false
    fetchEntities(novelId, profile.type)
      .then((r) => {
        if (!cancelled) setCandidates(r.entities.filter((e) => e.name !== profile.name))
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [mergeOpen, splitOpen, novelId, profile.type, profile.name])

  // On target selection, pull the target's aliases so the merge member set is
  // complete (the alias map is flat — every name must be listed explicitly).
  async function selectTarget(t: EntitySummary) {
    setTarget(t)
    setError(null)
    try {
      const tp = (await fetchEntityProfile(novelId, t.name, t.type)) as unknown as EntityProfile
      const members = Array.from(
        new Set([profile.name, ...aliasNames(profile), t.name, ...aliasNames(tp)]),
      )
      setMergeMembers(members)
      setCanonical(profile.name)
    } catch {
      setMergeMembers([profile.name, ...aliasNames(profile), t.name])
    }
  }

  async function doMerge() {
    if (!target || mergeMembers.length < 2) return
    setBusy(true)
    setError(null)
    try {
      await mergeAliases(novelId, mergeMembers, canonical)
      setMergeOpen(false)
      reset()
      // Navigate the drawer to the resulting canonical entity.
      replaceCurrent(canonical, profile.type as EntityType)
    } catch (e) {
      setError(e instanceof Error ? e.message : "合并失败")
      setBusy(false)
    }
  }

  async function doSplit() {
    const aliases = Array.from(checked)
    if (aliases.length === 0) return
    setBusy(true)
    setError(null)
    try {
      await splitAliases(novelId, profile.name, aliases, splitDest || null)
      setSplitOpen(false)
      reset()
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : "拆分失败")
      setBusy(false)
    }
  }

  async function doUndo() {
    setBusy(true)
    try {
      const { overrides } = await listEntityOverrides(novelId)
      const mine = overrides.filter((o) => {
        const j = o.override_json
        return (
          o.override_key === profile.name ||
          j.canonical === profile.name ||
          j.to === profile.name ||
          j.source === profile.name
        )
      })
      for (const o of mine) await deleteEntityOverride(novelId, o.id)
      refresh()
    } catch {
      setBusy(false)
    }
  }

  const personAliases = profile.type === "person" ? (profile as PersonProfile).aliases : []
  const filtered = query
    ? candidates.filter((c) => c.name.includes(query))
    : candidates.slice(0, 30)
  const edited = profile.edit_status === "edited"

  return (
    <div className="flex items-center gap-1">
      {profile.conflict && (
        <Badge variant="outline" className="border-amber-500 text-amber-600 gap-1" title="新章节的自动识别与你的修正存在分歧；你的修正仍然生效">
          <AlertTriangleIcon className="size-3" />
          冲突
        </Badge>
      )}
      {edited && (
        <Badge variant="secondary" className="gap-1" title="此实体有手动修正">
          <PencilIcon className="size-3" />
          已修正
        </Badge>
      )}

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon-xs" aria-label="编辑实体">
            <MoreVerticalIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={() => { reset(); setNewName(profile.name); setRenameOpen(true) }}>
            改名…
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => { reset(); setMergeOpen(true) }}>
            合并到另一个实体…
          </DropdownMenuItem>
          {profile.type === "person" && personAliases.length > 0 && (
            <DropdownMenuItem onClick={() => { reset(); setSplitOpen(true) }}>
              管理别名（拆分）
            </DropdownMenuItem>
          )}
          {edited && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={doUndo} disabled={busy}>
                撤销修正
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>

      {/* ── Merge dialog ── */}
      <Dialog open={mergeOpen} onOpenChange={(o) => { setMergeOpen(o); if (!o) reset() }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>合并「{profile.name}」到另一个实体</DialogTitle>
          </DialogHeader>

          {!target ? (
            <div className="space-y-2">
              <Input
                placeholder="搜索目标实体…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                autoFocus
              />
              <div className="max-h-60 overflow-y-auto rounded border">
                {filtered.map((c) => (
                  <button
                    key={c.name}
                    className="hover:bg-accent block w-full px-3 py-1.5 text-left text-sm"
                    onClick={() => selectTarget(c)}
                  >
                    {c.name}
                    <span className="text-muted-foreground ml-2 text-xs">{c.chapter_count} 章</span>
                  </button>
                ))}
                {filtered.length === 0 && (
                  <p className="text-muted-foreground px-3 py-2 text-sm">无匹配实体</p>
                )}
              </div>
            </div>
          ) : (
            <div className="space-y-3 text-sm">
              <p>
                把 <b>{profile.name}</b> 与 <b>{target.name}</b> 合并，共 {mergeMembers.length} 个别名：
              </p>
              <p className="text-muted-foreground max-h-20 overflow-y-auto">
                {mergeMembers.join(" · ")}
              </p>
              <div>
                <p className="mb-1 font-medium">显示名：</p>
                <div className="flex flex-wrap gap-3">
                  {[profile.name, target.name].map((n) => (
                    <label key={n} className="flex items-center gap-1">
                      <input
                        type="radio"
                        name="canonical"
                        checked={canonical === n}
                        onChange={() => setCanonical(n)}
                      />
                      {n}
                    </label>
                  ))}
                </div>
              </div>
              <p className="text-muted-foreground text-xs">⚠ 此操作可随时撤销，不改动原文数据</p>
            </div>
          )}

          {error && <p className="text-destructive text-sm">{error}</p>}

          <DialogFooter>
            {target && (
              <Button variant="ghost" size="sm" onClick={() => setTarget(null)} disabled={busy}>
                返回
              </Button>
            )}
            <Button size="sm" onClick={doMerge} disabled={!target || busy}>
              {busy ? "合并中…" : "确认合并"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Rename dialog ── */}
      <Dialog open={renameOpen} onOpenChange={(o) => { setRenameOpen(o); if (!o) reset() }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>给「{profile.name}」改名</DialogTitle>
          </DialogHeader>
          <div className="space-y-2 text-sm">
            <p className="text-muted-foreground text-xs">
              改成正确的名字(如 少年 → 杨过)。原名会作为别名保留;此操作可撤销,不改原文数据。
            </p>
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="新名称"
              autoFocus
              onKeyDown={(e) => { if (e.key === "Enter") doRename() }}
            />
          </div>
          {error && <p className="text-destructive text-sm">{error}</p>}
          <DialogFooter>
            <Button
              size="sm"
              onClick={doRename}
              disabled={busy || !newName.trim() || newName.trim() === profile.name}
            >
              {busy ? "改名中…" : "确认改名"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Split dialog ── */}
      <Dialog open={splitOpen} onOpenChange={(o) => { setSplitOpen(o); if (!o) reset() }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>管理「{profile.name}」的别名</DialogTitle>
          </DialogHeader>

          <div className="space-y-3 text-sm">
            <p className="text-muted-foreground text-xs">勾选不属于本实体的别名，将其拆出</p>
            <div className="max-h-52 space-y-1 overflow-y-auto">
              <div className="flex items-center gap-2 opacity-60">
                <span>🔒</span>
                <span>{profile.name}（显示名）</span>
              </div>
              {personAliases.map((a) => (
                <label key={a.name} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={checked.has(a.name)}
                    onChange={(e) => {
                      const next = new Set(checked)
                      if (e.target.checked) next.add(a.name)
                      else next.delete(a.name)
                      setChecked(next)
                    }}
                  />
                  {a.name}
                  {a.edited && <Badge variant="secondary" className="text-[10px]">已修正</Badge>}
                </label>
              ))}
            </div>

            {checked.size > 0 && (
              <div>
                <p className="mb-1 font-medium">拆出去向：</p>
                <select
                  className="border-input bg-background w-full rounded border px-2 py-1.5 text-sm"
                  value={splitDest}
                  onChange={(e) => setSplitDest(e.target.value)}
                >
                  <option value="">成为新的独立实体</option>
                  {candidates.map((c) => (
                    <option key={c.name} value={c.name}>归入：{c.name}</option>
                  ))}
                </select>
              </div>
            )}
          </div>

          {error && <p className="text-destructive text-sm">{error}</p>}

          <DialogFooter>
            <Button size="sm" onClick={doSplit} disabled={checked.size === 0 || busy}>
              {busy ? "拆分中…" : `拆出所选（${checked.size}）`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
