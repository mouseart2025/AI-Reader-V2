import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import {
  ArrowDownAZ,
  BookOpen,
  CheckCircle,
  Database,
  Download,
  Library,
  MoreHorizontal,
  Search,
  Trash2,
  Upload,
  User,
} from "lucide-react"
import {
  deleteNovel,
  fetchActiveAnalyses,
  exportNovelUrl,
  previewImport,
  downloadBackupExport,
  previewBackupImport,
  confirmBackupImport,
} from "@/api/client"
import type { Novel } from "@/api/types"
import { useNovelStore } from "@/stores/novelStore"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
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
import { ThemeToggle } from "@/components/shared/ThemeToggle"
import { UploadDialog } from "@/components/shared/UploadDialog"
import { WelcomeBanner } from "@/components/shared/WelcomeBanner"
import { ContextualGuideCard } from "@/components/shared/ContextualGuideCard"
import { useI18n, type Locale } from "@/i18n"

type SortKey = "recent" | "title" | "chapters" | "words"

function getDateLocale(locale: Locale): string {
  if (locale === "vi") return "vi-VN"
  return locale
}

function formatWordCount(count: number, t: ReturnType<typeof useI18n>["t"]): string {
  if (count >= 10000) return t("bookshelf.wordCountWan", { count: (count / 10000).toFixed(1) })
  return t("bookshelf.wordCount", { count })
}

function formatDate(dateStr: string | null, locale: Locale, t: ReturnType<typeof useI18n>["t"]): string {
  if (!dateStr) return ""
  const d = new Date(dateStr + "Z")
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffDays = Math.floor(diffMs / 86400000)
  if (diffDays === 0) return t("date.today")
  if (diffDays === 1) return t("date.yesterday")
  if (diffDays < 30) return t("date.daysAgo", { count: diffDays })
  return d.toLocaleDateString(getDateLocale(locale))
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
  const { locale, t } = useI18n()

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
              <span className="text-[10px] font-medium text-white/90">{t("bookshelf.builtInSample")}</span>
            </div>
          )}
          {analysisStatus === "running" && (
            <div className="absolute top-2 right-2 flex items-center gap-1.5 rounded-full bg-black/40 px-2 py-0.5 backdrop-blur-sm">
              <span className="inline-block size-1.5 animate-pulse rounded-full bg-green-400" />
              <span className="text-[10px] font-medium text-white/90">{t("analysis.running")}</span>
            </div>
          )}
          {analysisStatus === "paused" && (
            <div className="absolute top-2 right-2 flex items-center gap-1.5 rounded-full bg-black/40 px-2 py-0.5 backdrop-blur-sm">
              <span className="inline-block size-1.5 rounded-full bg-yellow-400" />
              <span className="text-[10px] font-medium text-white/90">{t("analysis.paused")}</span>
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
          <span>{t("common.chapterCount", { count: novel.total_chapters })}</span>
          <span>{formatWordCount(novel.total_words, t)}</span>
        </div>

        {/* Analysis progress */}
        {novel.analysis_progress >= 1 && !novel.failed_count ? (
          <div className="flex items-center gap-1.5 text-xs text-green-600 dark:text-green-400">
            <CheckCircle className="h-3.5 w-3.5" />
            <span>{t("analysis.completed")}</span>
          </div>
        ) : novel.analysis_progress >= 1 && novel.failed_count > 0 ? (
          <div className="flex items-center gap-1.5 text-xs text-yellow-600 dark:text-yellow-400">
            <CheckCircle className="h-3.5 w-3.5" />
            <span>{t("analysis.completedWithFailures", { count: novel.failed_count })}</span>
          </div>
        ) : (
          <div className="space-y-1">
            <div className="text-muted-foreground flex justify-between text-xs">
              <span>{t("analysis.progress")}</span>
              <span>
                {Math.round(novel.analysis_progress * 100)}%
                {novel.failed_count > 0 && (
                  <span className="text-yellow-600 dark:text-yellow-400 ml-1">
                    {t("analysis.failedChapters", { count: novel.failed_count })}
                  </span>
                )}
              </span>
            </div>
            <Progress value={novel.analysis_progress * 100} className="h-1.5" />
          </div>
        )}

        {/* Reading progress */}
        <div className="space-y-1">
          <div className="text-muted-foreground flex justify-between text-xs">
            <span>{t("bookshelf.readingProgress")}</span>
            {novel.reading_progress > 0 ? (
              <span>
                {t("bookshelf.readingChapterProgress", {
                  current: Math.round(novel.reading_progress * novel.total_chapters),
                  total: novel.total_chapters,
                })}
              </span>
            ) : (
              <span className="opacity-60">{t("bookshelf.notStartedReading")}</span>
            )}
          </div>
          {novel.reading_progress > 0 && (
            <Progress value={novel.reading_progress * 100} className="h-1.5" />
          )}
        </div>
      </CardContent>

      <CardFooter className="flex flex-col gap-2">
        {/* Quick-access buttons */}
        <div className="flex w-full gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          {[
            { label: t("nav.analysis"), path: `/analysis/${novel.id}` },
            { label: t("nav.relationGraph"), path: `/graph/${novel.id}` },
            { label: t("nav.encyclopedia"), path: `/encyclopedia/${novel.id}` },
            { label: t("nav.chat"), path: `/chat/${novel.id}` },
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
              ? t("bookshelf.lastRead", { date: formatDate(novel.last_opened, locale, t) })
              : t("bookshelf.importedAt", { date: formatDate(novel.created_at, locale, t) })}
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
  const { t } = useI18n()

  return (
    <div className="flex flex-col items-center justify-center py-32">
      <Library className="text-muted-foreground/40 mb-6 h-20 w-20" />
      <h2 className="text-muted-foreground mb-2 text-xl font-semibold">
        {t("bookshelf.emptyTitle")}
      </h2>
      <p className="text-muted-foreground/60 mb-8 text-sm">
        {t("bookshelf.emptyDescription")}
      </p>
      <Button size="lg" onClick={onUpload}>
        <Upload className="mr-2 h-5 w-5" />
        {t("bookshelf.uploadNovel")}
      </Button>
    </div>
  )
}

export default function BookshelfPage() {
  const navigate = useNavigate()
  const { locale, t } = useI18n()
  const { novels, loading, fetchNovels, removeNovel } = useNovelStore()
  const [search, setSearch] = useState("")
  const [sortKey, setSortKey] = useState<SortKey>("recent")
  const [deleteTarget, setDeleteTarget] = useState<Novel | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [activeAnalysisMap, setActiveAnalysisMap] = useState<Map<string, "running" | "paused">>(new Map())

  // Drag-to-upload state
  const [isDragOver, setIsDragOver] = useState(false)
  const [dragFile, setDragFile] = useState<File | undefined>(undefined)

  // External import state (for .air/.json import via UploadDialog)
  const [importPreviewData, setImportPreviewData] = useState<import("@/api/types").ImportPreview | null>(null)
  const [importFile, setImportFile] = useState<File | null>(null)

  // Refs
  const searchRef = useRef<HTMLInputElement>(null)
  const importFileRef = useRef<HTMLInputElement>(null)
  const backupImportRef = useRef<HTMLInputElement>(null)

  const loadNovels = useCallback(async () => {
    await Promise.all([
      fetchNovels(),
      fetchActiveAnalyses()
        .then((active) => setActiveAnalysisMap(new Map(active.items.map((a) => [a.novel_id, a.status]))))
        .catch(() => {}),
    ])
  }, [fetchNovels])

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
    return [...result].sort((a, b) => {
      switch (sortKey) {
        case "title":
          return a.title.localeCompare(b.title, getDateLocale(locale))
        case "chapters":
          return b.total_chapters - a.total_chapters
        case "words":
          return b.total_words - a.total_words
        case "recent":
        default: {
          const ta = a.last_opened ?? a.updated_at
          const tb = b.last_opened ?? b.updated_at
          return tb.localeCompare(ta)
        }
      }
    })
  }, [novels, search, sortKey, locale])

  const sampleNovels = useMemo(
    () => novels.filter((n) => n.is_sample),
    [novels]
  )

  const handleClick = (novel: Novel) => {
    navigate(`/read/${novel.id}`)
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      setDeleting(true)
      await deleteNovel(deleteTarget.id)
      removeNovel(deleteTarget.id)
    } catch (err) {
      console.error("Failed to delete novel:", err)
    } finally {
      setDeleting(false)
      setDeleteTarget(null)
    }
  }

  // Keyboard shortcuts
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === "n" || e.key === "N") {
        e.preventDefault()
        setUploadOpen(true)
      } else if (e.key === "/") {
        e.preventDefault()
        searchRef.current?.focus()
      } else if (e.key === "Escape") {
        if (search) {
          setSearch("")
        }
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [search])

  // Drag-to-upload handlers
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    // Only set false if we're leaving the container (not entering a child)
    if (e.currentTarget === e.target || !e.currentTarget.contains(e.relatedTarget as Node)) {
      setIsDragOver(false)
    }
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(false)
    const file = e.dataTransfer.files[0]
    if (!file) return
    const ext = file.name.includes(".") ? "." + file.name.split(".").pop()!.toLowerCase() : ""
    if (ext !== ".txt" && ext !== ".md") return
    setDragFile(file)
    setUploadOpen(true)
  }, [])

  // Clear dragFile and import state after dialog closes
  const handleUploadOpenChange = useCallback((open: boolean) => {
    setUploadOpen(open)
    if (!open) {
      setDragFile(undefined)
      setImportPreviewData(null)
      setImportFile(null)
    }
  }, [])

  // Import/Export handlers
  const handleExportNovel = useCallback(() => {
    if (novels.length === 0) return
    // If only one novel, export it directly
    if (novels.length === 1) {
      window.open(exportNovelUrl(novels[0].id), "_blank")
      return
    }
    // For multiple novels, let user pick (using a simple prompt for now)
    const names = novels.map((n, i) => `${i + 1}. ${n.title}`).join("\n")
    const choice = window.prompt(t("bookshelf.exportPrompt", { names }))
    if (!choice) return
    const idx = parseInt(choice) - 1
    if (idx >= 0 && idx < novels.length) {
      window.open(exportNovelUrl(novels[idx].id), "_blank")
    }
  }, [novels, t])

  const handleImportData = useCallback(async (file: File) => {
    try {
      const preview = await previewImport(file)
      setImportPreviewData(preview)
      setImportFile(file)
      setUploadOpen(true)
    } catch (err) {
      alert(err instanceof Error ? err.message : t("bookshelf.importPreviewFailed"))
    }
  }, [t])

  const handleBackupExport = useCallback(async () => {
    try {
      await downloadBackupExport()
    } catch (err) {
      alert(err instanceof Error ? err.message : t("bookshelf.backupExportFailed"))
    }
  }, [t])

  const handleBackupImport = useCallback(async (file: File) => {
    try {
      const preview = await previewBackupImport(file)
      const conflictLine = preview.conflict_count > 0
        ? "\n" + t("bookshelf.backupRestoreConflictLine", { count: preview.conflict_count })
        : ""
      const msg = t("bookshelf.backupRestoreConfirm", {
        count: preview.novel_count,
        conflictLine,
      })
      if (!window.confirm(msg)) return
      const result = await confirmBackupImport(file, "skip")
      alert(t("bookshelf.backupRestoreComplete", {
        imported: result.imported,
        skipped: result.skipped,
      }))
      await loadNovels()
    } catch (err) {
      alert(err instanceof Error ? err.message : t("bookshelf.backupRestoreFailed"))
    }
  }, [loadNovels, t])

  return (
    <div
      className="relative mx-auto max-w-6xl px-6 py-8"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Drag overlay */}
      {isDragOver && (
        <div className="pointer-events-none absolute inset-0 z-50 flex items-center justify-center rounded-lg border-2 border-dashed border-primary/50 bg-primary/5">
          <div className="flex flex-col items-center gap-2 text-primary">
            <Upload className="h-12 w-12" />
            <p className="text-lg font-medium">{t("bookshelf.dropToUpload")}</p>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="mb-8 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <BookOpen className="text-primary h-7 w-7" />
          <h1 className="text-2xl font-bold">{t("nav.bookshelf")}</h1>
          <span className="text-[10px] text-muted-foreground/50 tabular-nums self-end mb-0.5">
            v{__APP_VERSION__}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <Button variant="outline" size="sm" onClick={() => navigate("/settings")}>
            {t("settings.open")}
          </Button>
          <Button onClick={() => setUploadOpen(true)}>
            <Upload className="mr-2 h-4 w-4" />
            {t("bookshelf.uploadNovel")}
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="icon">
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={handleExportNovel} disabled={novels.length === 0}>
                <Download className="mr-2 h-4 w-4" />
                {t("bookshelf.exportNovelData")}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => importFileRef.current?.click()}>
                <Upload className="mr-2 h-4 w-4" />
                {t("bookshelf.importNovelData")}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={handleBackupExport}>
                <Database className="mr-2 h-4 w-4" />
                {t("bookshelf.backupAllData")}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => backupImportRef.current?.click()}>
                <Database className="mr-2 h-4 w-4" />
                {t("bookshelf.restoreBackup")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          {/* Hidden file inputs for import */}
          <input
            ref={importFileRef}
            type="file"
            accept=".json,.air,.zip"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) handleImportData(file)
              e.target.value = ""
            }}
          />
          <input
            ref={backupImportRef}
            type="file"
            accept=".zip"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) handleBackupImport(file)
              e.target.value = ""
            }}
          />
        </div>
      </div>

      {/* Search + Sort */}
      {novels.length > 0 && (
        <div className="mb-6 flex items-center gap-3">
          <div className="relative flex-1">
            <Search className="text-muted-foreground absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2" />
            <Input
              ref={searchRef}
              placeholder={t("bookshelf.searchPlaceholder")}
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
              <SelectItem value="recent">{t("bookshelf.sort.recent")}</SelectItem>
              <SelectItem value="title">{t("bookshelf.sort.title")}</SelectItem>
              <SelectItem value="chapters">{t("bookshelf.sort.chapters")}</SelectItem>
              <SelectItem value="words">{t("bookshelf.sort.words")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
      )}

      {/* Welcome Banner for first-time users */}
      {!loading && sampleNovels.length > 0 && (
        <WelcomeBanner sampleNovels={sampleNovels} />
      )}

      {/* Content */}
      {loading ? (
        <div className="text-muted-foreground flex justify-center py-32 text-sm">
          {t("common.loading")}
        </div>
      ) : novels.length === 0 ? (
        <EmptyState onUpload={() => setUploadOpen(true)} />
      ) : filtered.length === 0 ? (
        <div className="col-span-full flex flex-col items-center gap-2 py-12 text-muted-foreground">
          <Search className="size-8 opacity-40" />
          <p className="text-sm">{t("bookshelf.noMatches")}</p>
          <Button variant="ghost" size="sm" onClick={() => setSearch("")}>{t("bookshelf.clearSearch")}</Button>
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

      {/* Contextual guide card for users who've explored sample novels */}
      {!loading && novels.length > 0 && (
        <ContextualGuideCard onUpload={() => setUploadOpen(true)} />
      )}

      {/* Upload Dialog */}
      <UploadDialog
        open={uploadOpen}
        onOpenChange={handleUploadOpenChange}
        onImported={loadNovels}
        initialFile={dragFile}
        externalImportPreview={importPreviewData}
        externalImportFile={importFile}
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
            <AlertDialogTitle>{t("bookshelf.confirmDeleteTitle")}</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div>
                <p>{t("bookshelf.confirmDeleteDescription", { title: deleteTarget?.title ?? "" })}</p>
                <p className="mt-1 text-xs">
                  {t("bookshelf.confirmDeleteRelatedData", {
                    chapters: deleteTarget?.total_chapters ?? 0,
                    analysisData: deleteTarget && deleteTarget.analysis_progress > 0
                      ? t("bookshelf.deleteAnalysisDataSuffix")
                      : "",
                  })}
                </p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={handleDelete}
              disabled={deleting}
            >
              {deleting ? t("common.deleting") : t("common.delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
