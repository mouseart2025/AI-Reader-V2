import { useCallback, useEffect, useState } from "react"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import type { AnalysisPass, PassStatus } from "@/api/types"
import { useLlmInfoStore, formatLlmLabel } from "@/stores/llmInfoStore"
import { usePassStore } from "@/stores/passStore"
import { PassDiffView } from "./PassDiffView"

const STATUS_LABELS: Record<PassStatus, string> = {
  running: "进行中",
  paused: "已暂停",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
}

const STATUS_DOT_COLORS: Record<PassStatus, string> = {
  running: "bg-green-500 animate-pulse",
  paused: "bg-yellow-500",
  completed: "bg-blue-500",
  failed: "bg-red-500",
  cancelled: "bg-gray-400",
}

/** SQLite datetime('now') 是 UTC "YYYY-MM-DD HH:MM:SS",补 Z 解析 */
function parseDbTime(s: string | null | undefined): number | null {
  if (!s) return null
  const t = Date.parse(s.includes("T") ? s : s.replace(" ", "T") + "Z")
  return Number.isNaN(t) ? null : t
}

function formatDateTime(s: string | null | undefined): string {
  const t = parseDbTime(s)
  if (t === null) return "-"
  const d = new Date(t)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`
}

function formatPassDuration(p: AnalysisPass): string | null {
  const start = parseDbTime(p.created_at)
  const end = parseDbTime(p.completed_at)
  if (start === null || end === null) return null
  const s = Math.max(0, Math.round((end - start) / 1000))
  if (s < 60) return `${s}秒`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}分${s % 60}秒`
  return `${Math.floor(m / 60)}小时${m % 60}分`
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`
  return String(n)
}

/** 本 pass 成本分账文案(Epic 5 Story 5.2);云端模式附费用,本地模式只示 token */
function formatPassCost(p: AnalysisPass): string | null {
  const cs = p.cost_summary
  if (!cs || cs.billed_chapters === 0) return null
  const tokens = `输入 ${formatTokens(cs.input_tokens)} / 输出 ${formatTokens(cs.output_tokens)} tokens`
  return cs.cost_cny > 0 ? `${tokens} · 花费 ¥${cs.cost_cny.toFixed(2)}` : tokens
}

/** 单条 pass 列表项:状态/模型/时间/耗时/进度 + 暂停/继续/取消/删除 */
function PassRow({
  pass,
  selected,
  onToggleDiff,
}: {
  pass: AnalysisPass
  selected: boolean
  onToggleDiff: (pass: AnalysisPass) => void
}) {
  const progress = usePassStore((s) => s.progressById[pass.id])
  const { pause, resume, cancel, remove } = usePassStore()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [busy, setBusy] = useState(false)

  const isRunning = pass.status === "running"
  const isPaused = pass.status === "paused"
  const isActive = isRunning || isPaused
  const total = pass.chapter_end - pass.chapter_start + 1
  // WS 实时进度优先;无 WS 时用 REST 的 current_chapter 兜底
  const done = progress
    ? progress.done
    : Math.max(0, pass.current_chapter - pass.chapter_start + (isActive ? 0 : 1))
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
  const duration = formatPassDuration(pass)
  const costText = formatPassCost(pass)
  const coveredCount = Object.keys(pass.history_json?.chapters ?? {}).length
  const canViewDiff = coveredCount > 0

  const act = useCallback(
    async (fn: () => Promise<void>) => {
      setBusy(true)
      try {
        await fn()
      } finally {
        setBusy(false)
      }
    },
    [],
  )

  return (
    <li className="rounded-md border p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span
          className={`inline-block size-2 shrink-0 rounded-full ${STATUS_DOT_COLORS[pass.status]}`}
        />
        <span className="font-medium">{STATUS_LABELS[pass.status]}</span>
        {pass.model_name && (
          <Badge variant="outline" className="text-xs font-normal">
            {pass.model_name}
            {pass.config_json?.model_override ? "（二审指定）" : ""}
          </Badge>
        )}
        <span className="text-muted-foreground text-xs">
          第 {pass.chapter_start}-{pass.chapter_end} 章 · 创建于{" "}
          {formatDateTime(pass.created_at)}
          {duration ? ` · 耗时 ${duration}` : ""}
          {costText ? ` · ${costText}` : ""}
        </span>
        <span className="flex-1" />
        <div className="flex items-center gap-1.5">
          {isRunning && (
            <Button
              variant="outline"
              size="xs"
              disabled={busy}
              onClick={() => act(() => pause(pass.id))}
            >
              暂停
            </Button>
          )}
          {isPaused && (
            <Button
              variant="outline"
              size="xs"
              disabled={busy}
              onClick={() => act(() => resume(pass.id))}
            >
              继续
            </Button>
          )}
          {isActive && (
            <Button
              variant="destructive"
              size="xs"
              disabled={busy}
              onClick={() => act(() => cancel(pass.id))}
            >
              取消
            </Button>
          )}
          {canViewDiff && (
            <Button
              variant={selected ? "secondary" : "outline"}
              size="xs"
              onClick={() => onToggleDiff(pass)}
            >
              {selected ? "收起差异" : "查看差异"}
            </Button>
          )}
          {!isActive && (
            <Button
              variant="ghost"
              size="xs"
              className="text-destructive"
              disabled={busy}
              onClick={() => setConfirmDelete(true)}
            >
              删除
            </Button>
          )}
        </div>
      </div>

      {isActive && (
        <div>
          <div className="mb-1 flex justify-between text-xs text-muted-foreground">
            <span>
              第 {progress?.chapter ?? pass.current_chapter} 章 / 共 {total} 章
            </span>
            <span>{pct}%</span>
          </div>
          <Progress value={pct} />
          {progress && (
            <div className="mt-1 flex gap-x-3 text-xs text-muted-foreground">
              <span>实体 {progress.stats.entities}</span>
              <span>关系 {progress.stats.relations}</span>
              <span>事件 {progress.stats.events}</span>
            </div>
          )}
        </div>
      )}

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除本次二审</AlertDialogTitle>
            <AlertDialogDescription>
              将删除本次二审的全部影子数据(章节结果与裁决记录),正式分析结果不受影响。此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
            <AlertDialogAction
              disabled={busy}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => act(() => remove(pass.id))}
            >
              {busy ? "删除中..." : "确认删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </li>
  )
}

/**
 * 二审(独立重读)面板:opt-in 入口 + pass 列表 + 进度 + diff 查看。
 * multi-pass MVP (issue #70 Epic 4 Story 4.1)。
 */
export function PassPanel({
  novelId,
  mainAnalysisCompleted,
  mainAnalysisActive,
}: {
  novelId: string
  /** 一审已完成(completed / completed_with_errors)才允许启动二审 */
  mainAnalysisCompleted: boolean
  /** 一审进行中/暂停中(单活互斥,启动按钮禁用) */
  mainAnalysisActive: boolean
}) {
  const {
    passes, loading, error,
    load, start, disconnectWs, clearError,
  } = usePassStore()
  const llmInfo = useLlmInfoStore()
  const [startOpen, setStartOpen] = useState(false)
  const [modelOverride, setModelOverride] = useState("")
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  const [diffPassId, setDiffPassId] = useState<string | null>(null)

  useEffect(() => {
    load(novelId)
    return () => disconnectWs()
  }, [novelId, load, disconnectWs])

  const activePass = passes.find(
    (p) => p.status === "running" || p.status === "paused",
  )
  const startDisabled =
    !mainAnalysisCompleted || mainAnalysisActive || !!activePass || starting

  const startDisabledReason = !mainAnalysisCompleted
    ? "一审分析完成后才能启动二审"
    : mainAnalysisActive
      ? "一审分析进行中,二审不可同时运行"
      : activePass
        ? "已有进行中的二审,请先完成或取消"
        : null

  const handleStart = useCallback(async () => {
    setStarting(true)
    setStartError(null)
    try {
      await start(novelId, modelOverride.trim() || null)
      setStartOpen(false)
      setModelOverride("")
    } catch (e) {
      setStartError(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(false)
    }
  }, [novelId, modelOverride, start])

  const diffPass = passes.find((p) => p.id === diffPassId) ?? null

  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>二审(独立重读)</span>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              disabled={loading}
              onClick={() => load(novelId)}
            >
              刷新
            </Button>
            <Button
              size="sm"
              disabled={startDisabled}
              title={startDisabledReason ?? undefined}
              onClick={() => {
                clearError()
                setStartError(null)
                setStartOpen(true)
              }}
            >
              启动二审
            </Button>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-muted-foreground text-sm">
          用 LLM 对全书做一次<b>完全独立</b>的重读(读不到一审结果),再逐章对比两次结果暴露分歧。
          成本约等于再跑一遍分析;二审产物单独存放,不会改动正式分析结果。
          {startDisabledReason && (
            <span className="text-yellow-600 dark:text-yellow-400">
              {" "}（{startDisabledReason}）
            </span>
          )}
        </p>

        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400">
            {error}
          </div>
        )}

        {passes.length === 0 && !loading && (
          <p className="text-muted-foreground text-sm">还没有二审记录。</p>
        )}

        {passes.length > 0 && (
          <ul className="space-y-2">
            {passes.map((p) => (
              <PassRow
                key={p.id}
                pass={p}
                selected={diffPassId === p.id}
                onToggleDiff={(pass) =>
                  setDiffPassId((cur) => (cur === pass.id ? null : pass.id))
                }
              />
            ))}
          </ul>
        )}

        {diffPass && (
          <PassDiffView
            novelId={novelId}
            pass={diffPass}
            onClose={() => setDiffPassId(null)}
          />
        )}
      </CardContent>

      {/* 启动二审对话框:成本明示 + opt-in + 可选模型 override (D3) */}
      <Dialog open={startOpen} onOpenChange={setStartOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>启动二审(独立重读)</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <p className="text-muted-foreground">
              二审将对全书再做一遍独立分析,期间读不到一审的任何结果;
              完成后可逐章对比两次结果的差异。
            </p>
            <p className="rounded-md border border-yellow-200 bg-yellow-50 p-2 text-xs text-yellow-800 dark:border-yellow-800 dark:bg-yellow-900/20 dark:text-yellow-300">
              ⚠ 成本约等于再跑一遍一审分析(Token/耗时 ×1),仅在你主动确认后启动。
              二审与一审不能同时进行。
            </p>
            <div className="space-y-1">
              <Label htmlFor="pass-model-override">二审模型(可选)</Label>
              <Input
                id="pass-model-override"
                placeholder={
                  llmInfo.model
                    ? `默认沿用当前:${formatLlmLabel(llmInfo.model, llmInfo.provider)}`
                    : "默认沿用当前模型"
                }
                value={modelOverride}
                onChange={(e) => setModelOverride(e.target.value)}
              />
              <p className="text-muted-foreground text-xs">
                换一个模型做二审可以增强两次抽取的错误差异(推荐);留空则沿用当前配置。
              </p>
            </div>
          </div>
          {startError && (
            <p className="text-destructive text-sm">{startError}</p>
          )}
          <DialogFooter>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setStartOpen(false)}
              disabled={starting}
            >
              取消
            </Button>
            <Button size="sm" onClick={handleStart} disabled={starting}>
              {starting ? "启动中..." : "确认启动二审"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
