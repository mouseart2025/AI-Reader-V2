import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import {
  fetchChapterContent,
  fetchChapters,
  fetchEntities,
  fetchNovel,
  fetchUserState,
  saveUserState,
  searchChapters,
  type SearchResult,
} from "@/api/client"
import type { Chapter, ChapterEntity, EntityType, Novel } from "@/api/types"
import { useReadingStore } from "@/stores/readingStore"
import { useEntityCardStore } from "@/stores/entityCardStore"
import {
  useReadingSettingsStore,
  FONT_SIZE_MAP,
  LINE_HEIGHT_MAP,
  type FontSize,
  type LineHeight,
} from "@/stores/readingSettingsStore"
import { EntityCardDrawer } from "@/components/entity-cards/EntityCardDrawer"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

// ── Analysis status icon ─────────────────────────

function StatusDot({ status }: { status: string }) {
  const color =
    status === "completed"
      ? "bg-green-500"
      : status === "analyzing"
        ? "bg-yellow-500 animate-pulse"
        : status === "failed"
          ? "bg-red-500"
          : "bg-gray-300 dark:bg-gray-600"
  return <span className={cn("inline-block size-2 shrink-0 rounded-full", color)} />
}

// ── Entity highlighting colors ───────────────────

const ENTITY_COLORS: Record<string, string> = {
  person: "text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-950/40",
  location: "text-green-700 dark:text-green-400 bg-green-50 dark:bg-green-950/40",
  item: "text-orange-600 dark:text-orange-400 bg-orange-50 dark:bg-orange-950/40",
  org: "text-purple-600 dark:text-purple-400 bg-purple-50 dark:bg-purple-950/40",
  concept: "text-gray-600 dark:text-gray-400 bg-gray-100 dark:bg-gray-800/50",
}

// ── Highlight chapter text with entities ─────────

function highlightText(
  text: string,
  entities: ChapterEntity[],
  onEntityClick?: (name: string, type: string) => void,
) {
  if (entities.length === 0) return text

  // Sort by name length desc so longer names match first
  const sorted = [...entities].sort((a, b) => b.name.length - a.name.length)

  // Build regex pattern escaping special chars
  const pattern = sorted
    .map((e) => e.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("|")
  const regex = new RegExp(`(${pattern})`, "g")
  const entityMap = new Map(sorted.map((e) => [e.name, e.type]))

  const parts = text.split(regex)
  return parts.map((part, i) => {
    const type = entityMap.get(part)
    if (type) {
      return (
        <span
          key={i}
          className={cn(
            "cursor-pointer rounded-sm px-0.5 transition-colors hover:opacity-80",
            ENTITY_COLORS[type] ?? "",
          )}
          title={`${part} (${type})`}
          onClick={() => onEntityClick?.(part, type)}
        >
          {part}
        </span>
      )
    }
    return part
  })
}

// ── Volume/Chapter grouping ──────────────────────

interface VolumeGroup {
  volumeNum: number | null
  volumeTitle: string | null
  chapters: Chapter[]
}

function groupByVolume(chapters: Chapter[]): VolumeGroup[] {
  const groups: VolumeGroup[] = []
  let current: VolumeGroup | null = null

  for (const ch of chapters) {
    const vNum = ch.volume_num ?? null
    if (!current || current.volumeNum !== vNum) {
      current = {
        volumeNum: vNum,
        volumeTitle: ch.volume_title ?? null,
        chapters: [],
      }
      groups.push(current)
    }
    current.chapters.push(ch)
  }
  return groups
}

// ── TOC Sidebar ──────────────────────────────────

function TocSidebar({
  chapters,
  currentChapterNum,
  search,
  onSearchChange,
  onSelect,
  onClose,
}: {
  chapters: Chapter[]
  currentChapterNum: number
  search: string
  onSearchChange: (s: string) => void
  onSelect: (num: number) => void
  onClose: () => void
}) {
  const filtered = useMemo(() => {
    if (!search.trim()) return chapters
    const q = search.trim().toLowerCase()
    return chapters.filter(
      (ch) =>
        ch.title.toLowerCase().includes(q) ||
        String(ch.chapter_num).includes(q),
    )
  }, [chapters, search])

  const groups = useMemo(() => groupByVolume(filtered), [filtered])
  const hasVolumes = groups.some((g) => g.volumeNum !== null)

  // Track expanded volumes
  const [expandedVolumes, setExpandedVolumes] = useState<Set<number | null>>(
    new Set(),
  )

  // Auto-expand volume of current chapter
  useEffect(() => {
    const ch = chapters.find((c) => c.chapter_num === currentChapterNum)
    if (ch?.volume_num != null) {
      setExpandedVolumes((prev) => new Set([...prev, ch.volume_num]))
    }
  }, [currentChapterNum, chapters])

  const toggleVolume = (vNum: number | null) => {
    setExpandedVolumes((prev) => {
      const next = new Set(prev)
      if (next.has(vNum)) next.delete(vNum)
      else next.add(vNum)
      return next
    })
  }

  const currentRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    currentRef.current?.scrollIntoView({ block: "center", behavior: "smooth" })
  }, [currentChapterNum])

  return (
    <div className="flex h-full flex-col border-r">
      {/* Header */}
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <Input
          placeholder="搜索章节..."
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          className="h-8 text-sm"
        />
        <Button variant="ghost" size="icon-xs" onClick={onClose} title="收起目录">
          <PanelLeftClose className="size-4" />
        </Button>
      </div>

      {/* Chapter list */}
      <div className="flex-1 overflow-y-auto py-1">
        {groups.map((group, gi) => {
          const isExpanded =
            !hasVolumes ||
            group.volumeNum === null ||
            expandedVolumes.has(group.volumeNum)

          const analyzedCount = group.chapters.filter(
            (c) => c.analysis_status === "completed",
          ).length

          return (
            <div key={gi}>
              {/* Volume header */}
              {hasVolumes && group.volumeNum !== null && (
                <button
                  className="text-muted-foreground flex w-full items-center gap-1.5 px-3 py-1.5 text-left text-xs font-medium hover:bg-accent"
                  onClick={() => toggleVolume(group.volumeNum)}
                >
                  <ChevronIcon expanded={isExpanded} />
                  <span className="flex-1 truncate">
                    {group.volumeTitle || `第${group.volumeNum}卷`}
                  </span>
                  <span className="text-muted-foreground/60 text-[10px]">
                    {analyzedCount}/{group.chapters.length}
                  </span>
                </button>
              )}

              {/* Chapters */}
              {isExpanded &&
                group.chapters.map((ch) => {
                  const isCurrent = ch.chapter_num === currentChapterNum
                  return (
                    <button
                      key={ch.chapter_num}
                      ref={isCurrent ? currentRef : undefined}
                      className={cn(
                        "flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-accent",
                        hasVolumes && group.volumeNum !== null && "pl-6",
                        isCurrent && "bg-accent font-medium",
                      )}
                      onClick={() => onSelect(ch.chapter_num)}
                    >
                      <StatusDot status={ch.analysis_status} />
                      <span className="text-muted-foreground/60 shrink-0 text-xs tabular-nums">
                        {ch.chapter_num}
                      </span>
                      <span className="flex-1 truncate">{ch.title}</span>
                    </button>
                  )
                })}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Simple SVG icons ─────────────────────────────

function PanelLeftClose({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <rect width="18" height="18" x="3" y="3" rx="2" />
      <path d="M9 3v18" />
      <path d="m16 15-3-3 3-3" />
    </svg>
  )
}

function PanelLeftOpen({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <rect width="18" height="18" x="3" y="3" rx="2" />
      <path d="M9 3v18" />
      <path d="m14 9 3 3-3 3" />
    </svg>
  )
}

function ChevronIcon({ expanded }: { expanded: boolean }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn(
        "size-3 shrink-0 transition-transform",
        expanded && "rotate-90",
      )}
    >
      <path d="m9 18 6-6-6-6" />
    </svg>
  )
}

// ── Main ReadingPage ─────────────────────────────

export default function ReadingPage() {
  const { novelId } = useParams<{ novelId: string }>()
  const navigate = useNavigate()
  const [novel, setNovel] = useState<Novel | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const contentRef = useRef<HTMLDivElement>(null)

  const {
    chapters,
    currentChapter,
    currentChapterNum,
    entities,
    sidebarOpen,
    tocSearch,
    setChapters,
    setCurrentChapter,
    setCurrentChapterNum,
    setEntities,
    toggleSidebar,
    setSidebarOpen,
    setTocSearch,
    reset,
  } = useReadingStore()

  const openEntityCard = useEntityCardStore((s) => s.openCard)
  const { fontSize, lineHeight, setFontSize, setLineHeight } = useReadingSettingsStore()
  const [showSettings, setShowSettings] = useState(false)
  const [showSearch, setShowSearch] = useState(false)
  const [searchQuery, setSearchQuery] = useState("")
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)

  const handleEntityClick = useCallback(
    (name: string, type: string) => {
      openEntityCard(name, type as EntityType)
    },
    [openEntityCard],
  )

  // Load novel, chapters, and user state on mount
  useEffect(() => {
    if (!novelId) return
    let cancelled = false

    async function load() {
      try {
        const [n, { chapters: chs }, userState, { entities: allEnts }] = await Promise.all([
          fetchNovel(novelId!),
          fetchChapters(novelId!),
          fetchUserState(novelId!),
          // Load ALL entities for the novel once — used for highlighting across all chapters.
          // This avoids missing highlights for entities not extracted in the current chapter's fact.
          fetchEntities(novelId!),
        ])
        if (cancelled) return

        setNovel(n)
        setChapters(chs)

        // Set all entities for highlighting (name + type from every analyzed chapter)
        setEntities(allEnts.map((e) => ({ name: e.name, type: e.type })))

        // Restore reading position
        const startChapter = userState.last_chapter ?? 1
        setCurrentChapterNum(startChapter)

        // Load chapter content
        const content = await fetchChapterContent(novelId!, startChapter)
        if (cancelled) return
        setCurrentChapter(content)

        // Restore scroll position
        if (userState.scroll_position && contentRef.current) {
          requestAnimationFrame(() => {
            if (contentRef.current) {
              contentRef.current.scrollTop =
                userState.scroll_position *
                contentRef.current.scrollHeight
            }
          })
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
    }
  }, [novelId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      reset()
    }
  }, [reset])

  // Save reading position on chapter change and periodically
  const savePosition = useCallback(() => {
    if (!novelId || !currentChapterNum) return
    const scrollPos = contentRef.current
      ? contentRef.current.scrollTop / (contentRef.current.scrollHeight || 1)
      : 0
    saveUserState(novelId, {
      last_chapter: currentChapterNum,
      scroll_position: scrollPos,
    }).catch(() => {})
  }, [novelId, currentChapterNum])

  // Save position before leaving
  useEffect(() => {
    return () => {
      savePosition()
    }
  }, [savePosition])

  // Navigate to a chapter
  const goToChapter = useCallback(
    async (chapterNum: number) => {
      if (!novelId) return
      savePosition()

      setLoading(true)
      setError(null)
      try {
        const content = await fetchChapterContent(novelId, chapterNum)
        setCurrentChapter(content)
        setCurrentChapterNum(chapterNum)

        // Entities are already loaded for the whole novel — no per-chapter fetch needed

        // Scroll to top
        if (contentRef.current) {
          contentRef.current.scrollTop = 0
        }

        // Save new position
        saveUserState(novelId, {
          last_chapter: chapterNum,
          scroll_position: 0,
        }).catch(() => {})
      } catch (err) {
        setError(String(err))
      } finally {
        setLoading(false)
      }
    },
    [novelId, savePosition, setCurrentChapter, setCurrentChapterNum],
  )

  const handleSearch = useCallback(
    async (q: string) => {
      if (!novelId || !q.trim()) {
        setSearchResults([])
        return
      }
      setSearching(true)
      try {
        const { results } = await searchChapters(novelId, q.trim())
        setSearchResults(results)
      } catch {
        setSearchResults([])
      } finally {
        setSearching(false)
      }
    },
    [novelId],
  )

  const totalChapters = chapters.length
  const canPrev = currentChapterNum > 1
  const canNext = currentChapterNum < totalChapters

  if (loading && !currentChapter) {
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
    <div className="flex h-screen">
      {/* Sidebar */}
      {sidebarOpen && (
        <div className="w-64 shrink-0">
          <TocSidebar
            chapters={chapters}
            currentChapterNum={currentChapterNum}
            search={tocSearch}
            onSearchChange={setTocSearch}
            onSelect={goToChapter}
            onClose={() => setSidebarOpen(false)}
          />
        </div>
      )}

      {/* Main content */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex items-center gap-3 border-b px-4 py-2">
          {!sidebarOpen && (
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={toggleSidebar}
              title="展开目录"
            >
              <PanelLeftOpen className="size-4" />
            </Button>
          )}

          <button
            className="text-muted-foreground text-sm hover:underline"
            onClick={() => navigate("/")}
          >
            &larr; {novel.title}
          </button>

          {/* Quick nav links */}
          <div className="flex gap-0.5">
            {[
              { label: "图谱", path: `/graph/${novelId}` },
              { label: "地图", path: `/map/${novelId}` },
              { label: "时间线", path: `/timeline/${novelId}` },
              { label: "百科", path: `/encyclopedia/${novelId}` },
              { label: "问答", path: `/chat/${novelId}` },
            ].map((link) => (
              <Button
                key={link.label}
                variant="ghost"
                size="xs"
                className="text-muted-foreground h-6 px-1.5 text-[11px]"
                onClick={() => navigate(link.path)}
              >
                {link.label}
              </Button>
            ))}
          </div>

          <div className="flex-1" />

          <span className="text-muted-foreground text-xs">
            {currentChapterNum} / {totalChapters}
          </span>

          <div className="flex gap-1">
            <Button
              variant="outline"
              size="xs"
              disabled={!canPrev}
              onClick={() => goToChapter(currentChapterNum - 1)}
            >
              上一章
            </Button>
            <Button
              variant="outline"
              size="xs"
              disabled={!canNext}
              onClick={() => goToChapter(currentChapterNum + 1)}
            >
              下一章
            </Button>
          </div>

          <Button
            variant="ghost"
            size="icon-xs"
            onClick={() => {
              setShowSearch(!showSearch)
              if (showSearch) {
                setSearchQuery("")
                setSearchResults([])
              }
            }}
            title="全文搜索"
          >
            <SearchIcon className="size-4" />
          </Button>

          <div className="relative">
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => setShowSettings(!showSettings)}
              title="阅读设置"
            >
              <SettingsIcon className="size-4" />
            </Button>

            {showSettings && (
              <ReadingSettingsPanel
                fontSize={fontSize}
                lineHeight={lineHeight}
                onFontSizeChange={setFontSize}
                onLineHeightChange={setLineHeight}
                onClose={() => setShowSettings(false)}
              />
            )}
          </div>
        </header>

        {/* Search Bar */}
        {showSearch && (
          <div className="border-b px-4 py-2">
            <div className="flex items-center gap-2">
              <Input
                placeholder="搜索全书内容..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSearch(searchQuery)
                }}
                className="h-8 text-sm"
                autoFocus
              />
              <Button
                variant="outline"
                size="xs"
                onClick={() => handleSearch(searchQuery)}
                disabled={searching}
              >
                {searching ? "搜索中..." : "搜索"}
              </Button>
            </div>
            {searchResults.length > 0 && (
              <div className="mt-2 max-h-64 overflow-y-auto rounded-md border">
                {searchResults.map((r, i) => (
                  <button
                    key={i}
                    className="flex w-full flex-col px-3 py-2 text-left text-sm hover:bg-accent"
                    onClick={() => {
                      goToChapter(r.chapter_num)
                      setShowSearch(false)
                      setSearchQuery("")
                      setSearchResults([])
                    }}
                  >
                    <span className="font-medium">{r.title}</span>
                    <span className="text-muted-foreground line-clamp-2 text-xs">
                      {r.snippet}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="mx-4 mt-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400">
            {error}
          </div>
        )}

        {/* Chapter content */}
        <div ref={contentRef} className="flex-1 overflow-y-auto">
          <article className="mx-auto max-w-3xl px-8 py-8">
            {currentChapter && (
              <>
                <h2 className="mb-6 text-center text-xl font-bold">
                  {currentChapter.title}
                </h2>
                <div className={cn("whitespace-pre-wrap", FONT_SIZE_MAP[fontSize], LINE_HEIGHT_MAP[lineHeight])}>
                  {highlightText(currentChapter.content, entities, handleEntityClick)}
                </div>
              </>
            )}

            {/* Bottom navigation */}
            {currentChapter && (
              <div className="mt-12 mb-8 flex justify-between">
                <Button
                  variant="outline"
                  disabled={!canPrev}
                  onClick={() => goToChapter(currentChapterNum - 1)}
                >
                  &larr; 上一章
                </Button>
                <Button
                  variant="outline"
                  disabled={!canNext}
                  onClick={() => goToChapter(currentChapterNum + 1)}
                >
                  下一章 &rarr;
                </Button>
              </div>
            )}
          </article>
        </div>
      </div>

      {/* Entity Card Drawer */}
      {novelId && <EntityCardDrawer novelId={novelId} />}
    </div>
  )
}

// ── Reading Settings Panel ───────────────────────

function ReadingSettingsPanel({
  fontSize,
  lineHeight,
  onFontSizeChange,
  onLineHeightChange,
  onClose,
}: {
  fontSize: FontSize
  lineHeight: LineHeight
  onFontSizeChange: (s: FontSize) => void
  onLineHeightChange: (h: LineHeight) => void
  onClose: () => void
}) {
  const fontSizes: { value: FontSize; label: string }[] = [
    { value: "small", label: "小" },
    { value: "medium", label: "中" },
    { value: "large", label: "大" },
    { value: "xlarge", label: "特大" },
  ]
  const lineHeights: { value: LineHeight; label: string }[] = [
    { value: "compact", label: "紧凑" },
    { value: "normal", label: "正常" },
    { value: "loose", label: "宽松" },
  ]

  return (
    <>
      <div className="fixed inset-0 z-30" onClick={onClose} />
      <div className="absolute top-full right-0 z-40 mt-1 w-56 rounded-lg border bg-background p-3 shadow-lg">
        <h4 className="mb-2 text-sm font-medium">阅读设置</h4>

        <div className="mb-3">
          <span className="text-muted-foreground mb-1 block text-xs">字号</span>
          <div className="flex gap-1">
            {fontSizes.map((f) => (
              <button
                key={f.value}
                className={cn(
                  "flex-1 rounded px-2 py-1 text-xs transition-colors",
                  fontSize === f.value
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted hover:bg-accent",
                )}
                onClick={() => onFontSizeChange(f.value)}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        <div>
          <span className="text-muted-foreground mb-1 block text-xs">行距</span>
          <div className="flex gap-1">
            {lineHeights.map((l) => (
              <button
                key={l.value}
                className={cn(
                  "flex-1 rounded px-2 py-1 text-xs transition-colors",
                  lineHeight === l.value
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted hover:bg-accent",
                )}
                onClick={() => onLineHeightChange(l.value)}
              >
                {l.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </>
  )
}

function SearchIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.3-4.3" />
    </svg>
  )
}

function SettingsIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  )
}
