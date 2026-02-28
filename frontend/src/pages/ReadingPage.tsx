import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useParams, useSearchParams } from "react-router-dom"
import {
  fetchChapterContent,
  fetchChapterScenes,
  fetchChapters,
  fetchEntities,
  fetchNovel,
  fetchUserState,
  saveUserState,
  searchChapters,
  type SearchResult,
  fetchBookmarks,
  addBookmark,
  deleteBookmark,
} from "@/api/client"
import type { Bookmark, Chapter, ChapterEntity, EntityType, Novel, Scene } from "@/api/types"
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
import { ScenePanel, SCENE_BORDER_COLORS } from "@/components/shared/ScenePanel"
import { GuidedTourBubble } from "@/components/shared/GuidedTourBubble"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import { highlightText } from "@/lib/entityHighlight"
import { useTourStore, TOUR_STEPS, TOTAL_TOUR_STEPS } from "@/stores/tourStore"

// ── Entity type colors for filter chips ──────────
const ENTITY_TYPE_LABELS: { type: string; label: string; color: string }[] = [
  { type: "person", label: "人物", color: "bg-blue-500" },
  { type: "location", label: "地点", color: "bg-green-500" },
  { type: "item", label: "物品", color: "bg-orange-500" },
  { type: "org", label: "组织", color: "bg-purple-500" },
  { type: "concept", label: "概念", color: "bg-gray-500" },
]

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
  currentChapterNum,
  bookmarks,
  onSelect,
  onClose,
  onBookmarkDelete,
}: {
  currentChapterNum: number
  bookmarks: Bookmark[]
  onSelect: (num: number) => void
  onClose: () => void
  onBookmarkDelete: (id: number) => void
}) {
  const chapters = useReadingStore((s) => s.chapters)
  const search = useReadingStore((s) => s.tocSearch)
  const onSearchChange = useReadingStore((s) => s.setTocSearch)

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
  const [showBookmarks, setShowBookmarks] = useState(false)

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

  // Reading progress
  const readProgress = chapters.length > 0
    ? Math.round((currentChapterNum / chapters.length) * 100)
    : 0

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

      {/* Search result count (1.6) */}
      {search.trim() && (
        <p className="px-3 py-1 text-xs text-muted-foreground">
          {filtered.length}/{chapters.length} 章
        </p>
      )}

      {/* Bookmarks toggle */}
      {bookmarks.length > 0 && (
        <button
          className="flex items-center gap-1.5 border-b px-3 py-1.5 text-xs text-muted-foreground hover:bg-accent"
          onClick={() => setShowBookmarks(!showBookmarks)}
        >
          <BookmarkIcon className="size-3" />
          <span>{bookmarks.length} 个书签</span>
          <ChevronIcon expanded={showBookmarks} />
        </button>
      )}

      {/* Bookmark list */}
      {showBookmarks && (
        <div className="max-h-40 overflow-y-auto border-b">
          {bookmarks.map((bm) => (
            <div key={bm.id} className="flex items-center gap-1 px-3 py-1 text-xs hover:bg-accent">
              <button
                className="flex-1 truncate text-left text-primary hover:underline"
                onClick={() => onSelect(bm.chapter_num)}
              >
                第{bm.chapter_num}章 {bm.note && `- ${bm.note}`}
              </button>
              <button
                className="shrink-0 text-muted-foreground hover:text-destructive"
                onClick={() => onBookmarkDelete(bm.id)}
              >
                <XSmallIcon className="size-3" />
              </button>
            </div>
          ))}
        </div>
      )}

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

      {/* Reading progress (2.5) */}
      <div className="border-t px-3 py-2">
        <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
          <span>已读 {readProgress}%</span>
          <span>{currentChapterNum}/{chapters.length}</span>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${readProgress}%` }}
          />
        </div>
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

// ── Tour bubble for reading page (Step 1) ────────

function ReadingTourBubble({ isSample, hasContent }: { isSample: boolean; hasContent: boolean }) {
  const { currentStep, dismissed, nextStep, dismiss } = useTourStore()
  if (!isSample || currentStep !== 0 || dismissed || !hasContent) return null
  return (
    <div className="relative mb-4">
      <GuidedTourBubble
        step={0}
        totalSteps={TOTAL_TOUR_STEPS}
        message={TOUR_STEPS[0].message}
        onNext={nextStep}
        onDismiss={dismiss}
        position="bottom"
      />
    </div>
  )
}

// ── Main ReadingPage ─────────────────────────────

export default function ReadingPage() {
  const { novelId } = useParams<{ novelId: string }>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [novel, setNovel] = useState<Novel | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const contentRef = useRef<HTMLDivElement>(null)

  const chapters = useReadingStore((s) => s.chapters)
  const currentChapter = useReadingStore((s) => s.currentChapter)
  const currentChapterNum = useReadingStore((s) => s.currentChapterNum)
  const entities = useReadingStore((s) => s.entities)
  const aliasMap = useReadingStore((s) => s.aliasMap)
  const sidebarOpen = useReadingStore((s) => s.sidebarOpen)
  const setChapters = useReadingStore((s) => s.setChapters)
  const setCurrentChapter = useReadingStore((s) => s.setCurrentChapter)
  const setCurrentChapterNum = useReadingStore((s) => s.setCurrentChapterNum)
  const setEntities = useReadingStore((s) => s.setEntities)
  const setAliasMap = useReadingStore((s) => s.setAliasMap)
  const toggleSidebar = useReadingStore((s) => s.toggleSidebar)
  const setSidebarOpen = useReadingStore((s) => s.setSidebarOpen)
  const reset = useReadingStore((s) => s.reset)

  const openEntityCard = useEntityCardStore((s) => s.openCard)
  const {
    fontSize, lineHeight, setFontSize, setLineHeight,
    highlightEnabled, setHighlightEnabled,
    hiddenEntityTypes, toggleEntityType,
  } = useReadingSettingsStore()
  const [showSettings, setShowSettings] = useState(false)
  const [showSearch, setShowSearch] = useState(false)
  const [searchQuery, setSearchQuery] = useState("")
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)

  // Scene panel state
  const [scenePanelOpen, setScenePanelOpen] = useState(false)
  const [scenes, setScenes] = useState<Scene[]>([])
  const [activeSceneIndex, setActiveSceneIndex] = useState(0)
  const [scenesLoading, setScenesLoading] = useState(false)
  const [sceneError, setSceneError] = useState(false)

  // Scene cache (2.3)
  const sceneCacheRef = useRef(new Map<number, Scene[]>())

  // Chapter preload cache (3.2)
  const preloadCacheRef = useRef(new Map<number, { title: string; content: string; word_count?: number }>())

  // Scroll progress (1.2)
  const [scrollProgress, setScrollProgress] = useState(0)

  // Bookmarks (3.1)
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([])

  // Current chapter's analysis status
  const currentAnalysisStatus = useMemo(() => {
    const ch = chapters.find((c) => c.chapter_num === currentChapterNum)
    return ch?.analysis_status ?? "pending"
  }, [chapters, currentChapterNum])

  const handleGoAnalysis = useCallback(() => {
    if (novelId) navigate(`/analysis/${novelId}`)
  }, [novelId, navigate])

  const handleEntityClick = useCallback(
    (name: string, type: string) => {
      const canonical = aliasMap[name] ?? name
      openEntityCard(canonical, type as EntityType)
    },
    [openEntityCard, aliasMap],
  )

  // Filtered entities for highlight (2.6)
  const filteredEntities = useMemo(
    () => entities.filter((e) => !hiddenEntityTypes.includes(e.type)),
    [entities, hiddenEntityTypes],
  )

  // Load novel, chapters, and user state on mount
  useEffect(() => {
    if (!novelId) return
    let cancelled = false

    async function load() {
      try {
        const [n, { chapters: chs }, userState, { entities: allEnts, alias_map: aliasData }] = await Promise.all([
          fetchNovel(novelId!),
          fetchChapters(novelId!),
          fetchUserState(novelId!),
          fetchEntities(novelId!),
        ])
        if (cancelled) return

        setNovel(n)
        setChapters(chs)
        setAliasMap(aliasData ?? {})

        const entityList = allEnts.map((e) => ({ name: e.name, type: e.type as ChapterEntity["type"] }))
        if (aliasData) {
          const canonicalTypeMap = new Map<string, ChapterEntity["type"]>()
          for (const e of allEnts) {
            canonicalTypeMap.set(e.name, e.type as ChapterEntity["type"])
          }
          for (const [alias, canonical] of Object.entries(aliasData)) {
            const type = canonicalTypeMap.get(canonical)
            if (type) {
              entityList.push({ name: alias, type })
            }
          }
        }
        setEntities(entityList)

        const startChapter = userState.last_chapter ?? 1
        setCurrentChapterNum(startChapter)

        const content = await fetchChapterContent(novelId!, startChapter)
        if (cancelled) return
        setCurrentChapter(content)

        if (userState.scroll_position && contentRef.current) {
          requestAnimationFrame(() => {
            if (contentRef.current) {
              contentRef.current.scrollTop =
                userState.scroll_position *
                contentRef.current.scrollHeight
            }
          })
        }

        // Load bookmarks
        fetchBookmarks(novelId!).then((bms) => {
          if (!cancelled) setBookmarks(bms)
        }).catch(() => {})
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

  // Load scenes when panel is opened or chapter changes (with cache)
  const loadScenes = useCallback(() => {
    if (!novelId || !currentChapterNum) return
    setScenesLoading(true)
    setSceneError(false)

    const cached = sceneCacheRef.current.get(currentChapterNum)
    if (cached) {
      setScenes(cached)
      setActiveSceneIndex(0)
      setScenesLoading(false)
      return
    }

    fetchChapterScenes(novelId, currentChapterNum)
      .then((resp) => {
        setScenes(resp.scenes)
        setActiveSceneIndex(0)
        setSceneError(false)
        sceneCacheRef.current.set(currentChapterNum, resp.scenes)
      })
      .catch(() => {
        setSceneError(true)
        setScenes([])
      })
      .finally(() => {
        setScenesLoading(false)
      })
  }, [novelId, currentChapterNum])

  useEffect(() => {
    if (!scenePanelOpen) return
    loadScenes()
  }, [scenePanelOpen, loadScenes])

  // Build paragraph -> scene index map
  const paraSceneMap = useMemo(() => {
    const map = new Map<number, number>()
    for (const scene of scenes) {
      if (!scene.paragraph_range) continue
      for (let p = scene.paragraph_range[0]; p <= scene.paragraph_range[1]; p++) {
        map.set(p, scene.index)
      }
    }
    return map
  }, [scenes])

  // Split content into paragraphs (for scene-marked rendering)
  const paragraphs = useMemo(() => {
    if (!currentChapter?.content) return []
    return currentChapter.content.split("\n").filter((p) => p.trim())
  }, [currentChapter])

  // Scroll to scene paragraph
  const scrollToScene = useCallback((scene: Scene, index: number) => {
    setActiveSceneIndex(index)
    if (!scene.paragraph_range || !contentRef.current) return
    const paraEl = contentRef.current.querySelector(`[data-para="${scene.paragraph_range[0]}"]`)
    if (paraEl) paraEl.scrollIntoView({ behavior: "smooth", block: "start" })
  }, [])

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
        // Check preload cache first (3.2)
        const preloaded = preloadCacheRef.current.get(chapterNum)
        const content = preloaded ?? await fetchChapterContent(novelId, chapterNum)
        if (preloaded) preloadCacheRef.current.delete(chapterNum)

        setCurrentChapter(content)
        setCurrentChapterNum(chapterNum)

        if (contentRef.current) {
          contentRef.current.scrollTop = 0
        }

        saveUserState(novelId, {
          last_chapter: chapterNum,
          scroll_position: 0,
        }).catch(() => {})

        // Preload next chapter (3.2)
        const next = chapterNum + 1
        if (next <= chapters.length && !preloadCacheRef.current.has(next)) {
          if (typeof requestIdleCallback !== "undefined") {
            requestIdleCallback(() => {
              fetchChapterContent(novelId, next)
                .then((c) => preloadCacheRef.current.set(next, c))
                .catch(() => {})
            })
          }
        }
      } catch (err) {
        setError(String(err))
      } finally {
        setLoading(false)
      }
    },
    [novelId, savePosition, setCurrentChapter, setCurrentChapterNum, chapters.length],
  )

  // Handle ?chapter=N query parameter (from entity card chapter clicks)
  useEffect(() => {
    const chParam = searchParams.get("chapter")
    if (!chParam || !chapters.length) return
    const ch = parseInt(chParam, 10)
    if (isNaN(ch) || ch < 1 || ch > chapters.length) return
    searchParams.delete("chapter")
    setSearchParams(searchParams, { replace: true })
    goToChapter(ch)
  }, [searchParams, chapters.length, goToChapter, setSearchParams])

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

  // Scroll progress handler (1.2)
  const handleContentScroll = useCallback(() => {
    if (!contentRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = contentRef.current
    const max = scrollHeight - clientHeight
    setScrollProgress(max > 0 ? scrollTop / max : 0)
  }, [])

  // Keyboard shortcuts (2.4)
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return

      if (e.key === "ArrowLeft" && canPrev) {
        e.preventDefault()
        goToChapter(currentChapterNum - 1)
      } else if (e.key === "ArrowRight" && canNext) {
        e.preventDefault()
        goToChapter(currentChapterNum + 1)
      } else if (e.key === "Escape") {
        if (scenePanelOpen) setScenePanelOpen(false)
        else if (showSettings) setShowSettings(false)
        else if (showSearch) {
          setShowSearch(false)
          setSearchQuery("")
          setSearchResults([])
        }
      } else if (e.code === "KeyS" && !showSearch && !showSettings) {
        e.preventDefault()
        setScenePanelOpen((v) => !v)
      } else if (e.code === "KeyH" && !showSearch && !showSettings) {
        e.preventDefault()
        setHighlightEnabled(!highlightEnabled)
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [canPrev, canNext, currentChapterNum, goToChapter, scenePanelOpen, showSettings, showSearch, highlightEnabled, setHighlightEnabled])

  // Bookmark handlers (3.1)
  const handleAddBookmark = useCallback(async () => {
    if (!novelId) return
    const scrollPos = contentRef.current
      ? contentRef.current.scrollTop / (contentRef.current.scrollHeight || 1)
      : 0
    try {
      const bm = await addBookmark(novelId, currentChapterNum, scrollPos)
      setBookmarks((prev) => [...prev, bm])
    } catch { /* ignore duplicate */ }
  }, [novelId, currentChapterNum])

  const handleDeleteBookmark = useCallback(async (id: number) => {
    try {
      await deleteBookmark(id)
      setBookmarks((prev) => prev.filter((b) => b.id !== id))
    } catch { /* ignore */ }
  }, [])

  // Helper to render text with optional highlighting
  const renderText = useCallback(
    (text: string) => {
      if (highlightEnabled) {
        return highlightText(text, filteredEntities, handleEntityClick)
      }
      return text
    },
    [highlightEnabled, filteredEntities, handleEntityClick],
  )

  if (loading && !currentChapter) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-muted-foreground">Loading...</p>
      </div>
    )
  }

  if (!novel) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4">
        <p className="text-muted-foreground">Novel not found</p>
        <Button variant="outline" onClick={() => navigate("/")}>
          Back to Bookshelf
        </Button>
      </div>
    )
  }

  return (
    <div className="flex h-full">
      {/* Sidebar */}
      {sidebarOpen && (
        <div className="w-64 shrink-0">
          <TocSidebar
            currentChapterNum={currentChapterNum}
            bookmarks={bookmarks}
            onSelect={goToChapter}
            onClose={() => setSidebarOpen(false)}
            onBookmarkDelete={handleDeleteBookmark}
          />
        </div>
      )}

      {/* Main content */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Reading toolbar */}
        <header className="flex items-center gap-3 border-b px-4 py-1.5">
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

          <div className="flex-1" />

          <span className="text-muted-foreground text-xs">
            第 {currentChapterNum}/{totalChapters} 回
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

          {/* Highlight toggle (1.1) */}
          <Button
            variant={highlightEnabled ? "default" : "ghost"}
            size="icon-xs"
            onClick={() => setHighlightEnabled(!highlightEnabled)}
            title={highlightEnabled ? "关闭高亮 (H)" : "开启高亮 (H)"}
          >
            <HighlighterIcon className="size-4" />
          </Button>

          {/* Bookmark button (3.1) */}
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={handleAddBookmark}
            title="添加书签"
          >
            <BookmarkIcon className="size-4" />
          </Button>

          <Button
            variant={scenePanelOpen ? "default" : "ghost"}
            size="xs"
            onClick={() => setScenePanelOpen(!scenePanelOpen)}
            title={scenePanelOpen ? "收起剧本面板 (S)" : "展开剧本面板 (S)"}
          >
            <ClapperboardIcon className="mr-1 size-3.5" />
            剧本
          </Button>

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
                hiddenEntityTypes={hiddenEntityTypes}
                onFontSizeChange={setFontSize}
                onLineHeightChange={setLineHeight}
                onToggleEntityType={toggleEntityType}
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

        {/* Scroll progress bar (1.2) */}
        <div className="relative h-0.5 w-full bg-transparent">
          <div
            className="absolute top-0 left-0 h-full bg-primary transition-[width] duration-150"
            style={{ width: `${scrollProgress * 100}%` }}
          />
        </div>

        {/* Chapter content */}
        <div ref={contentRef} className="flex-1 overflow-y-auto" onScroll={handleContentScroll}>
          <article className="mx-auto max-w-3xl px-8 py-8">
            {/* Guided tour bubble — Step 1: entity highlight */}
            <ReadingTourBubble isSample={!!novel?.is_sample} hasContent={!!currentChapter} />
            {currentChapter && (
              <>
                <h2 className="mb-2 text-center text-xl font-bold">
                  {currentChapter.title}
                </h2>
                {/* Word count (1.3) */}
                {currentChapter.word_count != null && (
                  <p className="mb-6 text-center text-sm text-muted-foreground">
                    {currentChapter.word_count.toLocaleString()} 字
                  </p>
                )}
                {scenePanelOpen && scenes.length > 0 ? (
                  /* Paragraph-level rendering with scene border markers */
                  <div className={cn(FONT_SIZE_MAP[fontSize], LINE_HEIGHT_MAP[lineHeight])}>
                    {scenesLoading ? (
                      <p className="text-sm text-muted-foreground">加载场景...</p>
                    ) : (
                      paragraphs.map((p, i) => {
                        const sceneIdx = paraSceneMap.get(i)
                        const isActive = sceneIdx === activeSceneIndex
                        const borderColor = sceneIdx != null
                          ? SCENE_BORDER_COLORS[sceneIdx % SCENE_BORDER_COLORS.length]
                          : ""
                        return (
                          <p
                            key={i}
                            data-para={i}
                            className={cn(
                              "mb-2 transition-colors",
                              sceneIdx != null && `border-l-3 pl-3 ${borderColor}`,
                              isActive && "bg-accent/30 rounded-r",
                            )}
                          >
                            {renderText(p)}
                          </p>
                        )
                      })
                    )}
                  </div>
                ) : (
                  /* Normal whole-block rendering */
                  <div className={cn("whitespace-pre-wrap", FONT_SIZE_MAP[fontSize], LINE_HEIGHT_MAP[lineHeight])}>
                    {renderText(currentChapter.content)}
                  </div>
                )}
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

      {/* Scene Panel (right sidebar) */}
      {scenePanelOpen && (
        <ScenePanel
          scenes={scenes}
          activeSceneIndex={activeSceneIndex}
          analysisStatus={currentAnalysisStatus}
          loading={scenesLoading}
          error={sceneError}
          onSceneClick={scrollToScene}
          onClose={() => setScenePanelOpen(false)}
          onGoAnalysis={handleGoAnalysis}
          onRetry={loadScenes}
        />
      )}

      {/* Entity Card Drawer */}
      {novelId && <EntityCardDrawer novelId={novelId} />}
    </div>
  )
}

// ── Reading Settings Panel ───────────────────────

function ReadingSettingsPanel({
  fontSize,
  lineHeight,
  hiddenEntityTypes,
  onFontSizeChange,
  onLineHeightChange,
  onToggleEntityType,
  onClose,
}: {
  fontSize: FontSize
  lineHeight: LineHeight
  hiddenEntityTypes: string[]
  onFontSizeChange: (s: FontSize) => void
  onLineHeightChange: (h: LineHeight) => void
  onToggleEntityType: (type: string) => void
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
      <div className="absolute top-full right-0 z-40 mt-1 w-64 rounded-lg border bg-background p-3 shadow-lg">
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

        <div className="mb-3">
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

        {/* Entity type filter (2.6) */}
        <div>
          <span className="text-muted-foreground mb-1 block text-xs">实体高亮</span>
          <div className="flex flex-wrap gap-1">
            {ENTITY_TYPE_LABELS.map(({ type, label, color }) => {
              const hidden = hiddenEntityTypes.includes(type)
              return (
                <button
                  key={type}
                  className={cn(
                    "flex items-center gap-1 rounded px-2 py-1 text-xs transition-colors",
                    hidden
                      ? "bg-muted text-muted-foreground line-through"
                      : "bg-accent text-foreground",
                  )}
                  onClick={() => onToggleEntityType(type)}
                >
                  <span className={cn("inline-block size-2 rounded-full", color)} />
                  {label}
                </button>
              )
            })}
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

function ClapperboardIcon({ className }: { className?: string }) {
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
      <path d="M20.2 6 3 11l-.9-2.4c-.3-1.1.3-2.2 1.3-2.5l13.5-4c1.1-.3 2.2.3 2.5 1.3Z" />
      <path d="m6.2 5.3 3.1 3.9" />
      <path d="m12.4 3.4 3.1 4" />
      <path d="M3 11h18v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
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

function HighlighterIcon({ className }: { className?: string }) {
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
      <path d="m9 11-6 6v3h9l3-3" />
      <path d="m22 12-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4" />
    </svg>
  )
}

function BookmarkIcon({ className }: { className?: string }) {
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
      <path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z" />
    </svg>
  )
}

function XSmallIcon({ className }: { className?: string }) {
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
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  )
}
