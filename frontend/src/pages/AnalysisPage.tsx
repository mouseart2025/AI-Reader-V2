import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import {
  clearAnalysisData,
  fetchEntityDictionary,
  fetchNovel,
  fetchPrescanStatus,
  getLatestAnalysisTask,
  patchAnalysisTask,
  startAnalysis,
  triggerPrescan,
} from "@/api/client"
import type { EntityDictItem, Novel, PrescanStatus } from "@/api/types"
import { useAnalysisStore } from "@/stores/analysisStore"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"
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

const TYPE_LABELS: Record<string, string> = {
  person: "人物", location: "地点", item: "物品",
  org: "组织", concept: "概念", unknown: "未知",
}
const TYPE_BADGE_COLORS: Record<string, string> = {
  person: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  location: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  item: "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
  org: "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300",
  concept: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300",
  unknown: "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400",
}

export default function AnalysisPage() {
  const { novelId } = useParams<{ novelId: string }>()
  const navigate = useNavigate()
  const [novel, setNovel] = useState<Novel | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)

  // Range analysis
  const [rangeStart, setRangeStart] = useState(1)
  const [rangeEnd, setRangeEnd] = useState(1)
  const [showRangeMode, setShowRangeMode] = useState(false)

  // Re-analysis confirmation
  const [showReanalyzeConfirm, setShowReanalyzeConfirm] = useState(false)

  // Clear data confirmation
  const [showClearConfirm, setShowClearConfirm] = useState(false)
  const [clearing, setClearing] = useState(false)

  // Prescan state
  const [prescanStatus, setPrescanStatus] = useState<PrescanStatus>("pending")
  const [prescanEntityCount, setPrescanEntityCount] = useState(0)
  const [prescanEntities, setPrescanEntities] = useState<EntityDictItem[]>([])
  const [prescanLoading, setPrescanLoading] = useState(false)
  const [prescanExpanded, setPrescanExpanded] = useState(false)
  const prescanPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const {
    task,
    progress,
    currentChapter,
    totalChapters,
    stats,
    failedChapters,
    setTask,
    resetProgress,
    connectWs,
    disconnectWs,
  } = useAnalysisStore()

  const isRunning = task?.status === "running"
  const isPaused = task?.status === "paused"
  const isCompleted = task?.status === "completed"
  const isCancelled = task?.status === "cancelled"
  const isActive = isRunning || isPaused

  // Load prescan data (status + entities if completed)
  const loadPrescanData = useCallback(async (nId: string) => {
    try {
      const res = await fetchPrescanStatus(nId)
      setPrescanStatus(res.status)
      setPrescanEntityCount(res.entity_count)
      if (res.status === "completed" && res.entity_count > 0) {
        const dict = await fetchEntityDictionary(nId, undefined, 50)
        setPrescanEntities(dict.data)
      }
    } catch {
      // Prescan API may not exist yet — silently ignore
    }
  }, [])

  const handleTriggerPrescan = useCallback(async () => {
    if (!novelId) return
    setPrescanLoading(true)
    try {
      await triggerPrescan(novelId)
      setPrescanStatus("running")
      setPrescanEntities([])
      setPrescanEntityCount(0)
      setPrescanExpanded(false)
    } catch (err) {
      // 409 means already running
      if (String(err).includes("409")) {
        setPrescanStatus("running")
      }
    } finally {
      setPrescanLoading(false)
    }
  }, [novelId])

  // Load novel and latest task
  useEffect(() => {
    if (!novelId) return
    let cancelled = false

    // Reset stale state from previous novel immediately
    resetProgress()
    setTask(null)
    setLoading(true)
    setError(null)
    // Reset prescan state for new novel
    setPrescanStatus("pending")
    setPrescanEntityCount(0)
    setPrescanEntities([])
    setPrescanExpanded(false)

    async function load() {
      try {
        const [n, { task: latestTask }] = await Promise.all([
          fetchNovel(novelId!),
          getLatestAnalysisTask(novelId!),
        ])
        if (cancelled) return
        setNovel(n)
        setRangeEnd(n.total_chapters)
        if (latestTask) {
          setTask(latestTask)
          if (latestTask.status === "running" || latestTask.status === "paused") {
            connectWs(novelId!)
          }
        }
        // Non-blocking prescan load
        loadPrescanData(novelId!)
      } catch (err) {
        if (!cancelled) setError(String(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => {
      cancelled = true
      disconnectWs()
    }
  }, [novelId, setTask, connectWs, disconnectWs, resetProgress, loadPrescanData])

  // Poll prescan status when running
  useEffect(() => {
    if (prescanStatus !== "running" || !novelId) {
      if (prescanPollRef.current) {
        clearInterval(prescanPollRef.current)
        prescanPollRef.current = null
      }
      return
    }

    prescanPollRef.current = setInterval(async () => {
      try {
        const res = await fetchPrescanStatus(novelId)
        setPrescanStatus(res.status)
        setPrescanEntityCount(res.entity_count)
        if (res.status === "completed" || res.status === "failed") {
          if (prescanPollRef.current) {
            clearInterval(prescanPollRef.current)
            prescanPollRef.current = null
          }
          if (res.status === "completed" && res.entity_count > 0) {
            const dict = await fetchEntityDictionary(novelId, undefined, 50)
            setPrescanEntities(dict.data)
          }
        }
      } catch {
        // ignore polling errors
      }
    }, 3000)

    return () => {
      if (prescanPollRef.current) {
        clearInterval(prescanPollRef.current)
        prescanPollRef.current = null
      }
    }
  }, [prescanStatus, novelId])

  // Re-sync task status when page becomes visible (e.g. after laptop wake)
  useEffect(() => {
    if (!novelId) return

    function handleVisibilityChange() {
      if (document.visibilityState !== "visible") return
      // Re-fetch task status and reconnect WS if needed
      getLatestAnalysisTask(novelId!).then(({ task: latestTask }) => {
        if (latestTask) {
          setTask(latestTask)
          if (latestTask.status === "running" || latestTask.status === "paused") {
            connectWs(novelId!)
          }
        }
      }).catch(() => { /* ignore */ })
    }

    document.addEventListener("visibilitychange", handleVisibilityChange)
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange)
  }, [novelId, setTask, connectWs])

  const handleStartAnalysis = useCallback(
    async (force = false) => {
      if (!novelId || !novel) return
      setStarting(true)
      setError(null)
      resetProgress()
      try {
        const req = showRangeMode
          ? { chapter_start: rangeStart, chapter_end: rangeEnd, force }
          : { force }
        const { task_id } = await startAnalysis(novelId, req)
        setTask({
          id: task_id,
          novel_id: novelId,
          status: "running",
          chapter_start: showRangeMode ? rangeStart : 1,
          chapter_end: showRangeMode ? rangeEnd : novel.total_chapters,
          current_chapter: showRangeMode ? rangeStart : 1,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        })
        connectWs(novelId)
      } catch (err) {
        setError(String(err))
      } finally {
        setStarting(false)
      }
    },
    [novelId, novel, showRangeMode, rangeStart, rangeEnd, setTask, connectWs, resetProgress],
  )

  const handlePause = useCallback(async () => {
    if (!task) return
    try {
      await patchAnalysisTask(task.id, "paused")
      // Optimistic UI update — don't wait for WS broadcast
      setTask({ ...task, status: "paused" })
    } catch (err) {
      setError(String(err))
    }
  }, [task, setTask])

  const handleResume = useCallback(async () => {
    if (!task || !novelId) return
    try {
      await patchAnalysisTask(task.id, "running")
      setTask({ ...task, status: "running" })
      connectWs(novelId)
    } catch (err) {
      setError(String(err))
    }
  }, [task, novelId, setTask, connectWs])

  const handleCancel = useCallback(async () => {
    if (!task) return
    try {
      await patchAnalysisTask(task.id, "cancelled")
      setTask({ ...task, status: "cancelled" })
    } catch (err) {
      setError(String(err))
    }
  }, [task, setTask])

  const handleReanalyze = useCallback(() => {
    setShowReanalyzeConfirm(true)
  }, [])

  const confirmReanalyze = useCallback(() => {
    setShowReanalyzeConfirm(false)
    handleStartAnalysis(true)
  }, [handleStartAnalysis])

  const handleClearData = useCallback(async () => {
    if (!novelId) return
    setClearing(true)
    setError(null)
    try {
      await clearAnalysisData(novelId)
      // Reset all local state
      setTask(null)
      resetProgress()
      setShowClearConfirm(false)
    } catch (err) {
      setError(String(err))
    } finally {
      setClearing(false)
    }
  }, [novelId, setTask, resetProgress])

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-muted-foreground">加载中...</p>
      </div>
    )
  }

  if (!novel) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4">
        <p className="text-muted-foreground">未找到该小说</p>
        <Button variant="outline" onClick={() => navigate("/")}>
          返回书架
        </Button>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-3xl overflow-auto p-8">
      {/* Novel info */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold">{novel.title}</h1>
        <p className="text-muted-foreground text-sm">
          共 {novel.total_chapters} 章 &middot;{" "}
          {novel.total_words.toLocaleString()} 字
        </p>
      </div>

      {/* Prescan card */}
      <PrescanCard
        status={prescanStatus}
        entityCount={prescanEntityCount}
        entities={prescanEntities}
        loading={prescanLoading}
        expanded={prescanExpanded}
        onToggleExpand={() => setPrescanExpanded((v) => !v)}
        onTrigger={handleTriggerPrescan}
      />

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400">
          {error}
        </div>
      )}

      {/* Running / Paused state */}
      {isActive && (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              {isRunning && (
                <span className="inline-block size-2 animate-pulse rounded-full bg-green-500" />
              )}
              {isPaused && (
                <span className="inline-block size-2 rounded-full bg-yellow-500" />
              )}
              {isRunning ? "正在分析..." : "已暂停"}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <div className="mb-1 flex justify-between text-sm">
                <span>
                  第 {currentChapter} 章 / 共 {totalChapters} 章
                </span>
                <span>{progress}%</span>
              </div>
              <Progress value={progress} />
            </div>

            <div className="grid grid-cols-3 gap-4 text-center">
              <StatCard label="实体" value={stats.entities} />
              <StatCard label="关系" value={stats.relations} />
              <StatCard label="事件" value={stats.events} />
            </div>

            <div className="flex gap-2">
              {isRunning && (
                <>
                  <Button variant="outline" onClick={handlePause}>
                    暂停
                  </Button>
                  <Button variant="destructive" onClick={handleCancel}>
                    取消
                  </Button>
                </>
              )}
              {isPaused && (
                <>
                  <Button onClick={handleResume}>继续</Button>
                  <Button variant="destructive" onClick={handleCancel}>
                    取消
                  </Button>
                </>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Completed state */}
      {isCompleted && (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <span className="inline-block size-2 rounded-full bg-blue-500" />
              分析完成
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-3 gap-4 text-center">
              <StatCard label="实体" value={stats.entities} />
              <StatCard label="关系" value={stats.relations} />
              <StatCard label="事件" value={stats.events} />
            </div>
            <p className="text-muted-foreground text-sm">
              已分析第 {task?.chapter_start} - {task?.chapter_end} 章
            </p>
            <div className="flex gap-2">
              <Button variant="outline" onClick={handleReanalyze}>
                重新分析
              </Button>
              <Button
                variant="destructive"
                onClick={() => setShowClearConfirm(true)}
              >
                清除所有数据
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Cancelled state */}
      {isCancelled && (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <span className="inline-block size-2 rounded-full bg-gray-400" />
              分析已取消
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground mb-4 text-sm">
              已在第 {task?.current_chapter} 章停止。已完成的分析数据已保留。
            </p>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => setShowClearConfirm(true)}
            >
              清除所有数据
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Failed chapters */}
      {failedChapters.length > 0 && (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle>失败章节 ({failedChapters.length})</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2">
              {failedChapters.map((fc) => (
                <li
                  key={fc.chapter}
                  className="flex items-center justify-between rounded-md border p-2 text-sm"
                >
                  <span>第 {fc.chapter} 章</span>
                  <span className="text-muted-foreground max-w-[60%] truncate text-xs">
                    {fc.error}
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {/* Start analysis panel (shown when no active task) */}
      {!isActive && (
        <Card>
          <CardHeader>
            <CardTitle>开始分析</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-muted-foreground text-sm">
              使用 AI 逐章分析小说，提取人物、关系、地点、事件、物品、组织等结构化信息。已完成的章节将自动跳过。
            </p>

            {/* Range toggle */}
            <div className="flex items-center gap-2">
              <label className="text-sm">
                <input
                  type="checkbox"
                  checked={showRangeMode}
                  onChange={(e) => setShowRangeMode(e.target.checked)}
                  className="mr-2"
                />
                指定章节范围
              </label>
            </div>

            {showRangeMode && (
              <div className="flex items-end gap-3">
                <div className="space-y-1">
                  <Label htmlFor="range-start">起始章</Label>
                  <Input
                    id="range-start"
                    type="number"
                    min={1}
                    max={novel.total_chapters}
                    value={rangeStart}
                    onChange={(e) => setRangeStart(Number(e.target.value))}
                    className="w-24"
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="range-end">结束章</Label>
                  <Input
                    id="range-end"
                    type="number"
                    min={1}
                    max={novel.total_chapters}
                    value={rangeEnd}
                    onChange={(e) => setRangeEnd(Number(e.target.value))}
                    className="w-24"
                  />
                </div>
                <span className="text-muted-foreground pb-2 text-sm">
                  / 共 {novel.total_chapters} 章
                </span>
              </div>
            )}

            <div className="flex gap-2">
              <Button
                onClick={() => handleStartAnalysis(false)}
                disabled={starting || isActive}
              >
                {starting ? "启动中..." : "开始分析"}
              </Button>
              {(isCompleted || isCancelled) && (
                <Button variant="outline" onClick={handleReanalyze}>
                  强制重新分析
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Re-analyze confirmation dialog */}
      <AlertDialog
        open={showReanalyzeConfirm}
        onOpenChange={setShowReanalyzeConfirm}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认重新分析</AlertDialogTitle>
            <AlertDialogDescription>
              将重新分析所选范围内的所有章节（包括已完成的），现有分析数据将被覆盖。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={confirmReanalyze}>
              确认重新分析
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Clear data confirmation dialog */}
      <AlertDialog
        open={showClearConfirm}
        onOpenChange={setShowClearConfirm}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认清除所有分析数据</AlertDialogTitle>
            <AlertDialogDescription>
              此操作将删除该小说的所有分析结果，包括：章节提取数据、世界结构、地图布局缓存、用户坐标覆盖、分析任务记录。小说文本本身不会被删除。此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={clearing}>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleClearData}
              disabled={clearing}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {clearing ? "清除中..." : "确认清除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border p-3">
      <div className="text-2xl font-bold">{value.toLocaleString()}</div>
      <div className="text-muted-foreground text-xs">{label}</div>
    </div>
  )
}

function PrescanCard({
  status,
  entityCount,
  entities,
  loading,
  expanded,
  onToggleExpand,
  onTrigger,
}: {
  status: PrescanStatus
  entityCount: number
  entities: EntityDictItem[]
  loading: boolean
  expanded: boolean
  onToggleExpand: () => void
  onTrigger: () => void
}) {
  const typeCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const e of entities) {
      counts[e.entity_type] = (counts[e.entity_type] || 0) + 1
    }
    return counts
  }, [entities])

  const visibleEntities = expanded ? entities : entities.slice(0, 5)

  return (
    <Card className="mb-6">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          {status === "pending" && (
            <span className="inline-block size-2 rounded-full bg-gray-400" />
          )}
          {status === "running" && (
            <span className="inline-block size-2 animate-pulse rounded-full bg-green-500" />
          )}
          {status === "failed" && (
            <span className="inline-block size-2 rounded-full bg-red-500" />
          )}
          {status === "completed" && (
            <span className="inline-block size-2 rounded-full bg-blue-500" />
          )}
          实体预扫描
        </CardTitle>
      </CardHeader>
      <CardContent>
        {/* Pending */}
        {status === "pending" && (
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground text-sm">等待扫描</span>
            <Button size="sm" onClick={onTrigger} disabled={loading}>
              {loading ? "启动中..." : "开始扫描"}
            </Button>
          </div>
        )}

        {/* Running */}
        {status === "running" && (
          <div className="flex items-center gap-3">
            <svg
              className="size-4 animate-spin text-blue-500"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
            >
              <circle
                className="opacity-25"
                cx="12" cy="12" r="10"
                stroke="currentColor" strokeWidth="4"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
              />
            </svg>
            <span className="text-muted-foreground text-sm">正在扫描...</span>
          </div>
        )}

        {/* Failed */}
        {status === "failed" && (
          <div className="flex items-center justify-between">
            <span className="text-sm text-red-600 dark:text-red-400">扫描失败</span>
            <Button size="sm" variant="outline" onClick={onTrigger} disabled={loading}>
              重试
            </Button>
          </div>
        )}

        {/* Completed */}
        {status === "completed" && (
          <div className="space-y-3">
            {/* Summary: total + type badges */}
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium">
                共 {entityCount} 个实体
              </span>
              {Object.entries(typeCounts).map(([type, count]) => (
                <Badge
                  key={type}
                  variant="secondary"
                  className={cn(
                    "text-xs font-normal",
                    TYPE_BADGE_COLORS[type] || TYPE_BADGE_COLORS.unknown,
                  )}
                >
                  {TYPE_LABELS[type] || type} {count}
                </Badge>
              ))}
            </div>

            {/* Entity list */}
            {entities.length > 0 && (
              <div className={cn("space-y-1", expanded && "max-h-80 overflow-y-auto")}>
                {visibleEntities.map((e) => (
                  <div
                    key={`${e.entity_type}-${e.name}`}
                    className="flex items-center gap-2 rounded px-2 py-1 text-sm hover:bg-muted/50"
                  >
                    <Badge
                      variant="secondary"
                      className={cn(
                        "shrink-0 text-xs font-normal",
                        TYPE_BADGE_COLORS[e.entity_type] || TYPE_BADGE_COLORS.unknown,
                      )}
                    >
                      {TYPE_LABELS[e.entity_type] || e.entity_type}
                    </Badge>
                    <span className="font-medium">{e.name}</span>
                    {e.aliases.length > 0 && (
                      <span className="text-muted-foreground truncate text-xs">
                        ({e.aliases.join(", ")})
                      </span>
                    )}
                    <span className="text-muted-foreground ml-auto shrink-0 text-xs">
                      &times;{e.frequency}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {/* Expand / collapse */}
            {entities.length > 5 && (
              <button
                onClick={onToggleExpand}
                className="text-muted-foreground hover:text-foreground text-xs underline-offset-2 hover:underline"
              >
                {expanded ? "收起" : `展开全部 (${entities.length})`}
              </button>
            )}

            {/* Re-scan button */}
            <div className="pt-1">
              <Button size="sm" variant="outline" onClick={onTrigger} disabled={loading}>
                重新扫描
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
