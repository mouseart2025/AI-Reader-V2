import { useMemo, useState } from "react"

import type { EntityDictItem } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

/**
 * 预扫描候选合并（issue #18）。
 *
 * 场景：译名不统一（罗切斯特 / 罗彻斯特）或绰号被拆成独立候选时，先在这里
 * 合并，再开始分析——字典是注入抽取提示词的，合并必须发生在分析之前。
 * 合并是覆盖层操作，可撤销；不会改动原文或已分析的章节。
 */
export function PrescanMergeDialog({
  open,
  onOpenChange,
  entities,
  busy,
  onSubmit,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  entities: EntityDictItem[]
  busy: boolean
  onSubmit: (pairs: { source: string; target: string }[]) => void
}) {
  const [query, setQuery] = useState("")
  const [selected, setSelected] = useState<string[]>([])
  const [canonical, setCanonical] = useState<string>("")

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = q
      ? entities.filter(
          (e) =>
            e.name.toLowerCase().includes(q) ||
            e.aliases.some((a) => a.toLowerCase().includes(q)),
        )
      : entities
    return list.slice(0, 200)
  }, [entities, query])

  const toggle = (name: string) => {
    setSelected((prev) => {
      const next = prev.includes(name)
        ? prev.filter((n) => n !== name)
        : [...prev, name]
      if (canonical && !next.includes(canonical)) setCanonical("")
      return next
    })
  }

  // 默认把选中项里频次最高的当作合并后的名字
  const suggested = useMemo(() => {
    if (selected.length === 0) return ""
    const byName = new Map(entities.map((e) => [e.name, e]))
    return [...selected].sort(
      (a, b) => (byName.get(b)?.frequency ?? 0) - (byName.get(a)?.frequency ?? 0),
    )[0]
  }, [entities, selected])

  const target = canonical || suggested
  const pairs = selected
    .filter((n) => n !== target)
    .map((source) => ({ source, target }))

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>合并重复候选</DialogTitle>
          <DialogDescription>
            把同一人物/地点的多个候选合并成一条，再开始分析。合并只影响注入提示词的实体字典，可随时撤销。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索名称或别名…"
            autoFocus
          />

          {entities.length === 0 ? (
            <p className="text-muted-foreground py-6 text-center text-sm">
              还没有可合并的候选，请先完成预扫描。
            </p>
          ) : (
            <div className="max-h-64 space-y-1 overflow-y-auto rounded-md border p-1">
              {filtered.map((e) => {
                const checked = selected.includes(e.name)
                return (
                  <button
                    key={`${e.entity_type}-${e.name}`}
                    type="button"
                    onClick={() => toggle(e.name)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm transition-colors",
                      checked ? "bg-primary/10 ring-primary/30 ring-1" : "hover:bg-muted/60",
                    )}
                  >
                    <span
                      className={cn(
                        "flex size-4 shrink-0 items-center justify-center rounded border text-[10px]",
                        checked
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-muted-foreground/40",
                      )}
                    >
                      {checked ? "✓" : ""}
                    </span>
                    <Badge variant="secondary" className="shrink-0 text-xs font-normal">
                      {e.entity_type}
                    </Badge>
                    <span className="truncate font-medium">{e.name}</span>
                    {e.aliases.length > 0 && (
                      <span className="text-muted-foreground truncate text-xs">
                        ({e.aliases.join(", ")})
                      </span>
                    )}
                    <span className="text-muted-foreground ml-auto shrink-0 text-xs tabular-nums">
                      ×{e.frequency}
                    </span>
                  </button>
                )
              })}
              {filtered.length === 0 && (
                <p className="text-muted-foreground py-4 text-center text-xs">没有匹配的候选</p>
              )}
            </div>
          )}

          {selected.length >= 2 && (
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">合并后的名字：</span>
              <select
                value={target}
                onChange={(e) => setCanonical(e.target.value)}
                className="border-input bg-background rounded-md border px-2 py-1 text-sm"
              >
                {selected.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
              <span className="text-muted-foreground text-xs">
                其余 {pairs.length} 个并入别名
              </span>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            取消
          </Button>
          <Button
            onClick={() => onSubmit(pairs)}
            disabled={busy || pairs.length === 0}
          >
            {busy ? "合并中…" : `合并 ${pairs.length} 个候选`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
