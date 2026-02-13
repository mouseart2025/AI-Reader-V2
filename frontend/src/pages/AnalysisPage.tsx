import { useCallback, useEffect, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import {
  clearAnalysisData,
  fetchNovel,
  getLatestAnalysisTask,
  patchAnalysisTask,
  startAnalysis,
} from "@/api/client"
import type { Novel } from "@/api/types"
import { useAnalysisStore } from "@/stores/analysisStore"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
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

  // Load novel and latest task
  useEffect(() => {
    if (!novelId) return
    let cancelled = false

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
  }, [novelId, setTask, connectWs, disconnectWs])

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
