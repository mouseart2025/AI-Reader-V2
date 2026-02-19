import { useCallback, useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import {
  ArrowDownAZ,
  BookOpen,
  Library,
  Search,
  Trash2,
  Upload,
  User,
} from "lucide-react"
import { fetchNovels, deleteNovel, checkEnvironment, fetchActiveAnalyses } from "@/api/client"
import type { Novel } from "@/api/types"
import { useNovelStore } from "@/stores/novelStore"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { SetupGuide } from "@/components/shared/SetupGuide"
import { ThemeToggle } from "@/components/shared/ThemeToggle"
import { UploadDialog } from "@/components/shared/UploadDialog"

type SortKey = "recent" | "title" | "chapters"

function formatWordCount(count: number): string {
  if (count >= 10000) return `${(count / 10000).toFixed(1)}万字`
  return `${count}字`
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return ""
  const d = new Date(dateStr + "Z")
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffDays = Math.floor(diffMs / 86400000)
  if (diffDays === 0) return "今天"
  if (diffDays === 1) return "昨天"
  if (diffDays < 30) return `${diffDays}天前`
  return d.toLocaleDateString("zh-CN")
}

// Generate a stable cover gradient from the title string
function coverColor(title: string): string {
  const colors = [
    "from-rose-500 to-orange-400",
    "from-violet-500 to-purple-400",
    "from-blue-500 to-cyan-400",
    "from-emerald-500 to-teal-400",
    "from-amber-500 to-yellow-400",
    "from-pink-500 to-fuchsia-400",
    "from-indigo-500 to-blue-400",
    "from-lime-500 to-green-400",
  ]
  let hash = 0
  for (let i = 0; i < title.length; i++) {
    hash = (hash * 31 + title.charCodeAt(i)) | 0
  }
  return colors[Math.abs(hash) % colors.length]
}

function NovelCard({
  novel,
  analysisStatus,
  onDelete,
  onClick,
  onNavigate,
}: {
  novel: Novel
  analysisStatus: "running" | "paused" | null
  onDelete: (novel: Novel) => void
  onClick: (novel: Novel) => void
  onNavigate: (path: string) => void
}) {
  return (
    <Card
      className="group cursor-pointer transition-shadow hover:shadow-md"
      onClick={() => onClick(novel)}
    >
      <CardHeader className="pb-0">
        {/* Book Cover */}
        <div
          className={`bg-gradient-to-br ${coverColor(novel.title)} relative mb-3 flex h-36 items-center justify-center rounded-lg`}
        >
          {novel.is_sample && (
            <div className="absolute top-2 left-2 rounded-full bg-white/20 px-2 py-0.5 backdrop-blur-sm">
              <span className="text-[10px] font-medium text-white/90">内置样本</span>
            </div>
          )}
          {analysisStatus === "running" && (
            <div className="absolute top-2 right-2 flex items-center gap-1.5 rounded-full bg-black/40 px-2 py-0.5 backdrop-blur-sm">
              <span className="inline-block size-1.5 animate-pulse rounded-full bg-green-400" />
              <span className="text-[10px] font-medium text-white/90">分析中</span>
            </div>
          )}
          {analysisStatus === "paused" && (
            <div className="absolute top-2 right-2 flex items-center gap-1.5 rounded-full bg-black/40 px-2 py-0.5 backdrop-blur-sm">
              <span className="inline-block size-1.5 rounded-full bg-yellow-400" />
              <span className="text-[10px] font-medium text-white/90">已暂停</span>
            </div>
          )}
          <div className="px-4 text-center text-white">
            <p className="text-lg font-bold leading-tight drop-shadow">
              {novel.title}
            </p>
            {novel.author && (
              <p className="mt-1 text-sm opacity-80">{novel.author}</p>
            )}
          </div>
        </div>
        <CardTitle className="truncate text-base">{novel.title}</CardTitle>
        {novel.author && (
          <div className="text-muted-foreground flex items-center gap-1 text-sm">
            <User className="h-3 w-3" />
            <span className="truncate">{novel.author}</span>
          </div>
        )}
      </CardHeader>

      <CardContent className="space-y-2 text-sm">
        <div className="text-muted-foreground flex items-center justify-between">
          <span>{novel.total_chapters} 章</span>
          <span>{formatWordCount(novel.total_words)}</span>
        </div>

        {/* Analysis progress */}
        <div className="space-y-1">
          <div className="text-muted-foreground flex justify-between text-xs">
            <span>分析进度</span>
            <span>{Math.round(novel.analysis_progress * 100)}%</span>
          </div>
          <Progress value={novel.analysis_progress * 100} className="h-1.5" />
        </div>

        {/* Reading progress */}
        <div className="space-y-1">
          <div className="text-muted-foreground flex justify-between text-xs">
            <span>阅读进度</span>
            <span>{Math.round(novel.reading_progress * 100)}%</span>
          </div>
          <Progress value={novel.reading_progress * 100} className="h-1.5" />
        </div>
      </CardContent>

      <CardFooter className="flex flex-col gap-2">
        {/* Quick-access buttons */}
        <div className="flex w-full gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          {[
            { label: "分析", path: `/analysis/${novel.id}` },
            { label: "关系图", path: `/graph/${novel.id}` },
            { label: "百科", path: `/encyclopedia/${novel.id}` },
            { label: "问答", path: `/chat/${novel.id}` },
          ].map((link) => (
            <Button
              key={link.label}
              variant="outline"
              size="xs"
              className="flex-1 text-[10px] h-6"
              onClick={(e) => {
                e.stopPropagation()
                onNavigate(link.path)
              }}
            >
              {link.label}
            </Button>
          ))}
        </div>

        <div className="flex w-full items-center justify-between">
          <span className="text-muted-foreground text-xs">
            {novel.last_opened
              ? formatDate(novel.last_opened)
              : formatDate(novel.created_at)}
          </span>
          <Button
            variant="ghost"
            size="icon"
            className="text-muted-foreground hover:text-destructive h-8 w-8 opacity-0 transition-opacity group-hover:opacity-100"
            onClick={(e) => {
              e.stopPropagation()
              onDelete(novel)
            }}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </CardFooter>
    </Card>
  )
}

function EmptyState({ onUpload }: { onUpload: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-32">
      <Library className="text-muted-foreground/40 mb-6 h-20 w-20" />
      <h2 className="text-muted-foreground mb-2 text-xl font-semibold">
        还没有导入小说
      </h2>
      <p className="text-muted-foreground/60 mb-8 text-sm">
        上传 .txt 或 .md 文件开始阅读和分析
      </p>
      <Button size="lg" onClick={onUpload}>
        <Upload className="mr-2 h-5 w-5" />
        上传小说
      </Button>
    </div>
  )
}

export default function BookshelfPage() {
  const navigate = useNavigate()
  const { novels, setNovels } = useNovelStore()
  const [search, setSearch] = useState("")
  const [sortKey, setSortKey] = useState<SortKey>("recent")
  const [loading, setLoading] = useState(true)
  const [deleteTarget, setDeleteTarget] = useState<Novel | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [setupNeeded, setSetupNeeded] = useState<boolean | null>(null)
  const [activeAnalysisMap, setActiveAnalysisMap] = useState<Map<string, "running" | "paused">>(new Map())

  // Check Ollama environment on first load
  useEffect(() => {
    const stored = sessionStorage.getItem("setup_skipped")
    if (stored === "1") {
      setSetupNeeded(false)
      return
    }
    checkEnvironment()
      .then((env) => {
        if (env.llm_provider === "openai") {
          setSetupNeeded(false) // Cloud mode — no local setup needed
        } else {
          setSetupNeeded(!env.ollama_running || !env.model_available)
        }
      })
      .catch(() => {
        setSetupNeeded(false) // Backend unreachable, skip setup guide
      })
  }, [])

  const handleSetupReady = useCallback(() => {
    sessionStorage.setItem("setup_skipped", "1")
    setSetupNeeded(false)
  }, [])

  const loadNovels = useCallback(async () => {
    try {
      setLoading(true)
      const [data, active] = await Promise.all([
        fetchNovels(),
        fetchActiveAnalyses().catch(() => ({ items: [] })),
      ])
      setNovels(data.novels)
      setActiveAnalysisMap(new Map(active.items.map((a) => [a.novel_id, a.status])))
    } catch (err) {
      console.error("Failed to load novels:", err)
    } finally {
      setLoading(false)
    }
  }, [setNovels])

  useEffect(() => {
    loadNovels()
  }, [loadNovels])

  const filtered = useMemo(() => {
    let result = novels
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      result = result.filter(
        (n) =>
          n.title.toLowerCase().includes(q) ||
          (n.author && n.author.toLowerCase().includes(q))
      )
    }
    // Sort
    return [...result].sort((a, b) => {
      switch (sortKey) {
        case "title":
          return a.title.localeCompare(b.title, "zh-CN")
        case "chapters":
          return b.total_chapters - a.total_chapters
        case "recent":
        default: {
          const ta = a.last_opened ?? a.updated_at
          const tb = b.last_opened ?? b.updated_at
          return tb.localeCompare(ta)
        }
      }
    })
  }, [novels, search, sortKey])

  const handleClick = (novel: Novel) => {
    navigate(`/read/${novel.id}`)
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      setDeleting(true)
      await deleteNovel(deleteTarget.id)
      setNovels(novels.filter((n) => n.id !== deleteTarget.id))
    } catch (err) {
      console.error("Failed to delete novel:", err)
    } finally {
      setDeleting(false)
      setDeleteTarget(null)
    }
  }

  // Show setup guide if environment not ready
  if (setupNeeded === null) {
    return (
      <div className="text-muted-foreground flex min-h-screen items-center justify-center text-sm">
        加载中...
      </div>
    )
  }
  if (setupNeeded) {
    return <SetupGuide onReady={handleSetupReady} />
  }

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      {/* Header */}
      <div className="mb-8 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <BookOpen className="text-primary h-7 w-7" />
          <h1 className="text-2xl font-bold">书架</h1>
          <span className="text-[10px] text-muted-foreground/50 tabular-nums self-end mb-0.5">
            v{__APP_VERSION__}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <Button variant="outline" size="sm" onClick={() => navigate("/settings")}>
            设置
          </Button>
          <Button onClick={() => setUploadOpen(true)}>
            <Upload className="mr-2 h-4 w-4" />
            上传小说
          </Button>
        </div>
      </div>

      {/* Search + Sort */}
      {novels.length > 0 && (
        <div className="mb-6 flex items-center gap-3">
          <div className="relative flex-1">
            <Search className="text-muted-foreground absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2" />
            <Input
              placeholder="搜索书名或作者..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9"
            />
          </div>
          <Select
            value={sortKey}
            onValueChange={(v) => setSortKey(v as SortKey)}
          >
            <SelectTrigger className="w-40">
              <ArrowDownAZ className="mr-2 h-4 w-4" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="recent">最近打开</SelectItem>
              <SelectItem value="title">按书名</SelectItem>
              <SelectItem value="chapters">按章节数</SelectItem>
            </SelectContent>
          </Select>
        </div>
      )}

      {/* Content */}
      {loading ? (
        <div className="text-muted-foreground flex justify-center py-32 text-sm">
          加载中...
        </div>
      ) : novels.length === 0 ? (
        <EmptyState onUpload={() => setUploadOpen(true)} />
      ) : filtered.length === 0 ? (
        <div className="text-muted-foreground flex flex-col items-center py-32 text-sm">
          <Search className="text-muted-foreground/40 mb-4 h-12 w-12" />
          <p>没有找到匹配的小说</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
          {filtered.map((novel) => (
            <NovelCard
              key={novel.id}
              novel={novel}
              analysisStatus={activeAnalysisMap.get(novel.id) ?? null}
              onDelete={setDeleteTarget}
              onClick={handleClick}
              onNavigate={(path) => navigate(path)}
            />
          ))}
        </div>
      )}

      {/* Upload Dialog */}
      <UploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onImported={loadNovels}
      />

      {/* Delete Confirmation Dialog */}
      <AlertDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除</AlertDialogTitle>
            <AlertDialogDescription>
              确定要删除《{deleteTarget?.title}
              》吗？删除后将移除该小说及所有关联的分析数据，此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={handleDelete}
              disabled={deleting}
            >
              {deleting ? "删除中..." : "删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
