import { useCallback, useEffect, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import {
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
    } catch (err) {
      setError(String(err))
    }
  }, [task])

  const handleResume = useCallback(async () => {
    if (!task || !novelId) return
    try {
      await patchAnalysisTask(task.id, "running")
      connectWs(novelId)
    } catch (err) {
      setError(String(err))
    }
  }, [task, novelId, connectWs])

  const handleCancel = useCallback(async () => {
    if (!task) return
    try {
      await patchAnalysisTask(task.id, "cancelled")
    } catch (err) {
      setError(String(err))
    }
  }, [task])

  const handleReanalyze = useCallback(() => {
    setShowReanalyzeConfirm(true)
  }, [])

  const confirmReanalyze = useCallback(() => {
    setShowReanalyzeConfirm(false)
    handleStartAnalysis(true)
  }, [handleStartAnalysis])

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <p className="text-muted-foreground">Loading...</p>
      </div>
    )
  }

  if (!novel) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-4">
        <p className="text-muted-foreground">Novel not found</p>
        <Button variant="outline" onClick={() => navigate("/")}>
          Back to Bookshelf
        </Button>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-3xl p-8">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <button
            className="text-muted-foreground mb-1 text-sm hover:underline"
            onClick={() => navigate("/")}
          >
            &larr; Back to Bookshelf
          </button>
          <h1 className="text-2xl font-bold">{novel.title}</h1>
          <p className="text-muted-foreground text-sm">
            {novel.total_chapters} chapters &middot;{" "}
            {novel.total_words.toLocaleString()} words
          </p>
        </div>
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
              {isRunning ? "Analyzing..." : "Paused"}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <div className="mb-1 flex justify-between text-sm">
                <span>
                  Chapter {currentChapter} / {totalChapters}
                </span>
                <span>{progress}%</span>
              </div>
              <Progress value={progress} />
            </div>

            <div className="grid grid-cols-3 gap-4 text-center">
              <StatCard label="Entities" value={stats.entities} />
              <StatCard label="Relations" value={stats.relations} />
              <StatCard label="Events" value={stats.events} />
            </div>

            <div className="flex gap-2">
              {isRunning && (
                <>
                  <Button variant="outline" onClick={handlePause}>
                    Pause
                  </Button>
                  <Button variant="destructive" onClick={handleCancel}>
                    Cancel
                  </Button>
                </>
              )}
              {isPaused && (
                <>
                  <Button onClick={handleResume}>Resume</Button>
                  <Button variant="destructive" onClick={handleCancel}>
                    Cancel
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
              Analysis Complete
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-3 gap-4 text-center">
              <StatCard label="Entities" value={stats.entities} />
              <StatCard label="Relations" value={stats.relations} />
              <StatCard label="Events" value={stats.events} />
            </div>
            <p className="text-muted-foreground text-sm">
              Analyzed chapters {task?.chapter_start} - {task?.chapter_end}
            </p>
            <Button variant="outline" onClick={handleReanalyze}>
              Re-analyze
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Cancelled state */}
      {isCancelled && (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <span className="inline-block size-2 rounded-full bg-gray-400" />
              Analysis Cancelled
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground mb-4 text-sm">
              Stopped at chapter {task?.current_chapter}. Already analyzed data
              is preserved.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Failed chapters */}
      {failedChapters.length > 0 && (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle>Failed Chapters ({failedChapters.length})</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2">
              {failedChapters.map((fc) => (
                <li
                  key={fc.chapter}
                  className="flex items-center justify-between rounded-md border p-2 text-sm"
                >
                  <span>Chapter {fc.chapter}</span>
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
            <CardTitle>Start Analysis</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-muted-foreground text-sm">
              Analyze chapters using AI to extract characters, relationships,
              locations, events, and more. Already completed chapters will be
              skipped.
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
                Specify chapter range
              </label>
            </div>

            {showRangeMode && (
              <div className="flex items-end gap-3">
                <div className="space-y-1">
                  <Label htmlFor="range-start">From</Label>
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
                  <Label htmlFor="range-end">To</Label>
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
                  / {novel.total_chapters}
                </span>
              </div>
            )}

            <div className="flex gap-2">
              <Button
                onClick={() => handleStartAnalysis(false)}
                disabled={starting || isActive}
              >
                {starting ? "Starting..." : "Start Analysis"}
              </Button>
              {(isCompleted || isCancelled) && (
                <Button variant="outline" onClick={handleReanalyze}>
                  Force Re-analyze
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
            <AlertDialogTitle>Confirm Re-analysis</AlertDialogTitle>
            <AlertDialogDescription>
              This will re-analyze all chapters in the selected range, including
              already completed ones. Existing analysis data will be overwritten.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmReanalyze}>
              Re-analyze
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
