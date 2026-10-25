import { useEffect, useMemo, useState } from "react"
import { ChevronDown, ChevronRight, Download } from "lucide-react"

import { costDetailCsvUrl, fetchCostDetail } from "@/api/client"
import type { CostDetailResponse } from "@/api/types"
import { Button } from "@/components/ui/button"

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`
  return String(n)
}

function formatDuration(ms: number): string {
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}秒`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}分${s % 60}秒`
  const h = Math.floor(m / 60)
  return `${h}小时${m % 60}分`
}

/**
 * Per-chapter cost & duration report shown after analysis completes.
 *
 * The full table (and CSV) lives in Settings; this is the quick "where did the
 * time/money go" view: totals, average speed, slowest and most expensive
 * chapters. Chapter numbers come from `chapters.chapter_num`, not the DB row id.
 */
export function AnalysisCostReport({ novelId }: { novelId: string }) {
  const [data, setData] = useState<CostDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [open, setOpen] = useState(true)

  useEffect(() => {
    let cancelled = false
    fetchCostDetail(novelId)
      .then((res) => {
        if (!cancelled) setData(res)
      })
      .catch(() => {
        if (!cancelled) setError(true)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [novelId])

  const slowest = useMemo(
    () =>
      [...(data?.chapters ?? [])]
        .filter((c) => c.extraction_ms > 0)
        .sort((a, b) => b.extraction_ms - a.extraction_ms)
        .slice(0, 5),
    [data],
  )
  const expensive = useMemo(
    () =>
      [...(data?.chapters ?? [])]
        .filter((c) => c.cost_cny > 0)
        .sort((a, b) => b.cost_cny - a.cost_cny)
        .slice(0, 5),
    [data],
  )

  if (loading) {
    return <p className="text-muted-foreground text-sm">加载成本/耗时明细…</p>
  }
  if (error || !data) {
    return <p className="text-muted-foreground text-sm">暂无成本/耗时明细</p>
  }

  const s = data.summary
  const hasCost = s.total_cost_cny > 0

  return (
    <div className="rounded-md border p-3 text-sm space-y-2">
      <button
        className="flex w-full items-center gap-1.5 text-left"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        <span className="font-medium">成本与耗时</span>
        <span className="text-muted-foreground text-xs">
          {s.total_chapters} 章 · {formatTokens(s.total_input_tokens + s.total_output_tokens)} tokens
          {s.total_ms > 0 ? ` · ${formatDuration(s.total_ms)}` : ""}
        </span>
        <span className="ml-auto" />
        <Button
          variant="ghost"
          size="xs"
          onClick={(e) => {
            e.stopPropagation()
            window.open(costDetailCsvUrl(novelId), "_blank")
          }}
        >
          <Download className="size-3" />
          导出 CSV
        </Button>
      </button>

      {open && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div className="rounded-md bg-muted/40 px-2 py-1.5">
              <div className="text-[11px] text-muted-foreground">总耗时</div>
              <div className="font-mono">{formatDuration(s.total_ms)}</div>
            </div>
            <div className="rounded-md bg-muted/40 px-2 py-1.5">
              <div className="text-[11px] text-muted-foreground">均速</div>
              <div className="font-mono">{formatDuration(s.avg_chapter_ms)}/章</div>
            </div>
            <div className="rounded-md bg-muted/40 px-2 py-1.5">
              <div className="text-[11px] text-muted-foreground">最快 / 最慢</div>
              <div className="font-mono">
                {formatDuration(s.min_chapter_ms)} / {formatDuration(s.max_chapter_ms)}
              </div>
            </div>
            <div className="rounded-md bg-muted/40 px-2 py-1.5">
              <div className="text-[11px] text-muted-foreground">
                {hasCost ? "总费用" : "输出 Token"}
              </div>
              <div className="font-mono">
                {hasCost ? `¥${s.total_cost_cny.toFixed(2)}` : formatTokens(s.total_output_tokens)}
              </div>
            </div>
          </div>

          <div className="grid gap-2 sm:grid-cols-2">
            <div>
              <div className="text-[11px] text-muted-foreground mb-1">最慢的 5 章</div>
              <div className="space-y-0.5">
                {slowest.length === 0 && (
                  <span className="text-muted-foreground text-xs">无耗时记录</span>
                )}
                {slowest.map((c) => (
                  <div key={c.chapter_id} className="flex justify-between gap-2 text-xs">
                    <span className="truncate text-muted-foreground" title={c.chapter_title || undefined}>
                      第{c.chapter_num}章 {c.chapter_title}
                    </span>
                    <span className="font-mono flex-shrink-0">{formatDuration(c.extraction_ms)}</span>
                  </div>
                ))}
              </div>
            </div>
            {hasCost && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">最贵的 5 章</div>
                <div className="space-y-0.5">
                  {expensive.map((c) => (
                    <div key={c.chapter_id} className="flex justify-between gap-2 text-xs">
                      <span className="truncate text-muted-foreground" title={c.chapter_title || undefined}>
                        第{c.chapter_num}章 {c.chapter_title}
                      </span>
                      <span className="font-mono flex-shrink-0">¥{c.cost_cny.toFixed(3)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <p className="text-[11px] text-muted-foreground">
            模型: {data.model || "本地"} · 完整逐章明细见 设置 → 成本明细
          </p>
        </>
      )}
    </div>
  )
}
