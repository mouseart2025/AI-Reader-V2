import { useCallback, useEffect, useState } from "react"
import type { EntityOverride } from "@/api/types"
import { listEntityOverrides, deleteEntityOverride } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"

interface Props {
  novelId: string
  /** Optional: open an entity card when a record is clicked. */
  onOpenEntity?: (name: string) => void
}

/** Centralized "我的修正" list — all user alias merges/splits, each undoable (FR6). */
export function MyEditsPanel({ novelId, onOpenEntity }: Props) {
  const [overrides, setOverrides] = useState<EntityOverride[]>([])
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<number | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    listEntityOverrides(novelId)
      .then((r) => setOverrides(r.overrides))
      .catch(() => setOverrides([]))
      .finally(() => setLoading(false))
  }, [novelId])

  useEffect(() => { load() }, [load])

  async function undo(id: number) {
    setBusyId(id)
    try {
      await deleteEntityOverride(novelId, id)
      setOverrides((prev) => prev.filter((o) => o.id !== id))
    } finally {
      setBusyId(null)
    }
  }

  if (loading) return <p className="text-muted-foreground p-4 text-sm">加载中...</p>
  if (overrides.length === 0)
    return (
      <div className="p-4">
        <p className="text-muted-foreground text-sm">还没有手动修正。</p>
        <p className="text-muted-foreground mt-1 text-xs">
          在百科卡或关系图上点实体右上角的「⋯」即可合并或拆分别名。
        </p>
      </div>
    )

  return (
    <div className="space-y-2 p-4">
      <p className="text-muted-foreground text-xs">共 {overrides.length} 条修正，均可撤销，不影响原文数据。</p>
      {overrides.map((o) => {
        const j = o.override_json
        const kind = o.override_type
        const LABELS: Record<string, string> = {
          alias_merge: "合并", alias_split: "拆分", entity_rename: "改名",
          concept_rename: "概念改名", concept_recategory: "概念改类", concept_delete: "概念删除",
        }
        const label = LABELS[kind] ?? kind
        const badgeVariant =
          kind === "alias_merge" ? "secondary"
            : kind === "concept_delete" ? "destructive"
              : kind === "alias_split" ? "outline" : "default"
        const target =
          kind === "alias_merge" ? j.canonical
            : kind === "entity_rename" ? j.to ?? o.override_key
              : o.override_key
        const detail =
          kind === "alias_merge" ? (j.members ?? []).join(" · ")
            : kind === "entity_rename" ? `${o.override_key} → ${j.to ?? ""}`
              : kind === "concept_rename" ? `${o.override_key} → ${j.to ?? ""}`
                : kind === "concept_recategory" ? `${o.override_key} · 分类 → ${j.to ?? ""}`
                  : kind === "concept_delete" ? `已删除：${o.override_key}`
                    : `${j.source ?? ""} ✂ ${(j.aliases ?? []).join(" · ")}`
        return (
          <div key={o.id} className="flex items-start justify-between gap-2 rounded border p-2 text-sm">
            <div className="min-w-0 flex-1">
              <div className="mb-1 flex items-center gap-1.5">
                <Badge variant={badgeVariant} className="text-[10px]">
                  {label}
                </Badge>
                <button
                  className="truncate font-medium hover:underline"
                  onClick={() => target && onOpenEntity?.(target)}
                  disabled={!target}
                >
                  {target ?? "（独立实体）"}
                </button>
              </div>
              <p className="text-muted-foreground truncate text-xs">{detail}</p>
            </div>
            <Button
              variant="ghost"
              size="xs"
              onClick={() => undo(o.id)}
              disabled={busyId === o.id}
            >
              {busyId === o.id ? "…" : "撤销"}
            </Button>
          </div>
        )
      })}
    </div>
  )
}
