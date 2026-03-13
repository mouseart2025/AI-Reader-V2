import {
  type ReactNode,
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react"
import { Search, X } from "lucide-react"
import { Input } from "@/components/ui/input"

export interface TextPreviewPanelHandle {
  scrollToChapter: (index: number) => void
}

interface ChapterBoundary {
  /** Character offset in raw text where this chapter starts */
  charOffset: number
  title: string
}

interface Props {
  /** The full raw text of the novel */
  rawText: string
  /** Chapter boundaries (start offsets + titles) derived from current split */
  chapterBoundaries: ChapterBoundary[]
  /** Character offsets where user has placed manual split markers */
  splitPoints: number[]
  onSplitPointsChange: (points: number[]) => void
}

const LINE_HEIGHT = 22

/**
 * Right-side panel showing the full original text with chapter boundaries,
 * manual split markers, and search.
 */
export const TextPreviewPanel = forwardRef<TextPreviewPanelHandle, Props>(
  function TextPreviewPanel({ rawText, chapterBoundaries, splitPoints, onSplitPointsChange }, ref) {
    const scrollRef = useRef<HTMLDivElement>(null)
    const [searchQuery, setSearchQuery] = useState("")
    const [activeMatchIndex, setActiveMatchIndex] = useState(0)

    // Split raw text into lines
    const lines = useMemo(() => rawText.split("\n"), [rawText])

    // Build line offset map: lineIndex -> charOffset
    const lineOffsets = useMemo(() => {
      const offsets: number[] = []
      let offset = 0
      for (const line of lines) {
        offsets.push(offset)
        offset += line.length + 1 // +1 for \n
      }
      return offsets
    }, [lines])

    // Map chapter boundaries to line indices
    const chapterBoundaryLines = useMemo(() => {
      const result: Map<number, string> = new Map()
      for (const b of chapterBoundaries) {
        // Find the line closest to this offset
        let lineIdx = 0
        for (let i = 0; i < lineOffsets.length; i++) {
          if (lineOffsets[i] <= b.charOffset) lineIdx = i
          else break
        }
        result.set(lineIdx, b.title)
      }
      return result
    }, [chapterBoundaries, lineOffsets])

    // Map split points to line indices
    const splitPointLines = useMemo(() => {
      const result: Map<number, number> = new Map() // lineIdx -> charOffset
      for (const sp of splitPoints) {
        let lineIdx = 0
        for (let i = 0; i < lineOffsets.length; i++) {
          if (lineOffsets[i] <= sp) lineIdx = i
          else break
        }
        result.set(lineIdx, sp)
      }
      return result
    }, [splitPoints, lineOffsets])

    // Windowed rendering
    const [visibleRange, setVisibleRange] = useState<[number, number]>([0, 200])

    const handleScroll = useCallback(() => {
      const el = scrollRef.current
      if (!el) return
      const scrollTop = el.scrollTop
      const clientHeight = el.clientHeight
      const buffer = Math.ceil(clientHeight / LINE_HEIGHT)
      const startIdx = Math.max(0, Math.floor(scrollTop / LINE_HEIGHT) - buffer)
      const endIdx = Math.min(lines.length, Math.ceil((scrollTop + clientHeight) / LINE_HEIGHT) + buffer)
      setVisibleRange([startIdx, endIdx])
    }, [lines.length])

    useEffect(() => {
      const el = scrollRef.current
      if (!el) return
      el.addEventListener("scroll", handleScroll, { passive: true })
      handleScroll()
      return () => el.removeEventListener("scroll", handleScroll)
    }, [handleScroll])

    // Search
    const searchMatches = useMemo(() => {
      if (!searchQuery.trim()) return []
      const q = searchQuery.trim().toLowerCase()
      const matches: number[] = []
      for (let i = 0; i < lines.length; i++) {
        if (lines[i].toLowerCase().includes(q)) {
          matches.push(i)
        }
      }
      return matches
    }, [searchQuery, lines])

    const scrollToLine = useCallback((lineIdx: number) => {
      const el = scrollRef.current
      if (!el) return
      el.scrollTop = lineIdx * LINE_HEIGHT - el.clientHeight / 3
    }, [])

    useEffect(() => {
      if (searchMatches.length === 0) return
      const idx = searchMatches[activeMatchIndex % searchMatches.length]
      scrollToLine(idx)
    }, [activeMatchIndex, searchMatches, scrollToLine])

    // Expose scrollToChapter
    useImperativeHandle(ref, () => ({
      scrollToChapter(index: number) {
        if (index < chapterBoundaries.length) {
          const offset = chapterBoundaries[index].charOffset
          let lineIdx = 0
          for (let i = 0; i < lineOffsets.length; i++) {
            if (lineOffsets[i] <= offset) lineIdx = i
            else break
          }
          scrollToLine(lineIdx)
        }
      },
    }), [chapterBoundaries, lineOffsets, scrollToLine])

    // Toggle split point
    const handleGapClick = (lineIdx: number) => {
      const offset = lineOffsets[lineIdx]
      if (!offset && offset !== 0) return
      const existingIdx = splitPoints.indexOf(offset)
      if (existingIdx >= 0) {
        onSplitPointsChange(splitPoints.filter((p) => p !== offset))
      } else {
        onSplitPointsChange([...splitPoints, offset].sort((a, b) => a - b))
      }
    }

    const highlightText = (text: string) => {
      if (!searchQuery.trim()) return text
      const q = searchQuery.trim()
      const lower = text.toLowerCase()
      const qLower = q.toLowerCase()
      const parts: ReactNode[] = []
      let lastIdx = 0
      let idx = lower.indexOf(qLower)
      let key = 0
      while (idx !== -1) {
        if (idx > lastIdx) parts.push(text.slice(lastIdx, idx))
        parts.push(
          <mark key={key++} className="rounded-sm bg-yellow-300 px-0.5 dark:bg-yellow-700">
            {text.slice(idx, idx + q.length)}
          </mark>,
        )
        lastIdx = idx + q.length
        idx = lower.indexOf(qLower, lastIdx)
      }
      if (lastIdx < text.length) parts.push(text.slice(lastIdx))
      return <>{parts}</>
    }

    const totalHeight = lines.length * LINE_HEIGHT
    const [startIdx, endIdx] = visibleRange
    const searchMatchSet = useMemo(() => new Set(searchMatches), [searchMatches])
    const activeSearchLine = searchMatches.length > 0
      ? searchMatches[activeMatchIndex % searchMatches.length]
      : -1

    return (
      <div className="flex h-full flex-col">
        {/* Search bar */}
        <div className="flex items-center gap-2 border-b px-3 py-2">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
          <Input
            value={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value)
              setActiveMatchIndex(0)
            }}
            placeholder="搜索原文..."
            className="h-7 text-sm"
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault()
                if (searchMatches.length > 0) {
                  setActiveMatchIndex((prev) => (prev + 1) % searchMatches.length)
                }
              }
            }}
          />
          {searchMatches.length > 0 && (
            <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
              {(activeMatchIndex % searchMatches.length) + 1}/{searchMatches.length}
            </span>
          )}
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery("")}
              className="shrink-0 text-muted-foreground hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        {/* Text content - virtualized */}
        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto"
        >
          <div style={{ height: totalHeight, position: "relative" }}>
            {lines.slice(startIdx, endIdx).map((line, i) => {
              const lineIdx = startIdx + i
              const isChapterBoundary = chapterBoundaryLines.has(lineIdx)
              const chapterTitle = chapterBoundaryLines.get(lineIdx)
              const isSplitPoint = splitPointLines.has(lineIdx)
              const isActiveMatch = lineIdx === activeSearchLine
              const isMatch = searchMatchSet.has(lineIdx)

              return (
                <div
                  key={lineIdx}
                  style={{
                    position: "absolute",
                    top: lineIdx * LINE_HEIGHT,
                    left: 0,
                    right: 0,
                    height: LINE_HEIGHT,
                  }}
                  className="group"
                >
                  {/* Chapter boundary marker */}
                  {isChapterBoundary && lineIdx > 0 && (
                    <div
                      className="absolute left-0 right-0 flex items-center gap-2 px-3"
                      style={{ top: -3, height: 6, zIndex: 2 }}
                    >
                      <div className="h-[2px] flex-1 bg-blue-400 dark:bg-blue-600" />
                      <span className="shrink-0 rounded bg-blue-100 px-1.5 text-[9px] font-medium text-blue-600 dark:bg-blue-900 dark:text-blue-300">
                        {chapterTitle}
                      </span>
                      <div className="h-[2px] flex-1 bg-blue-400 dark:bg-blue-600" />
                    </div>
                  )}
                  {/* Manual split point marker */}
                  {isSplitPoint && !isChapterBoundary && (
                    <div
                      className="absolute left-0 right-0 flex items-center gap-1 px-3"
                      style={{ top: -3, height: 6, zIndex: 2 }}
                    >
                      <div className="h-0 flex-1 border-t-2 border-dashed border-red-500" />
                      <button
                        type="button"
                        className="shrink-0 rounded-full bg-red-100 p-0.5 text-red-500 hover:bg-red-200 dark:bg-red-900 dark:hover:bg-red-800"
                        onClick={(e) => {
                          e.stopPropagation()
                          handleGapClick(lineIdx)
                        }}
                        title="移除分割线"
                      >
                        <X className="h-2.5 w-2.5" />
                      </button>
                      <div className="h-0 flex-1 border-t-2 border-dashed border-red-500" />
                    </div>
                  )}
                  {/* Hover area for adding split points */}
                  {!isChapterBoundary && !isSplitPoint && lineIdx > 0 && (
                    <div
                      className="absolute -top-1.5 left-3 right-3 h-3 cursor-pointer opacity-0 transition-opacity hover:opacity-100"
                      onClick={() => handleGapClick(lineIdx)}
                      title="点击插入分割线"
                      style={{ zIndex: 1 }}
                    >
                      <div className="mt-1 h-0 border-t border-dashed border-red-300 dark:border-red-700" />
                    </div>
                  )}
                  {/* Line text */}
                  <div
                    className={`h-full truncate px-4 text-[13px] leading-[22px] ${
                      isActiveMatch
                        ? "bg-yellow-200 dark:bg-yellow-800/50"
                        : isMatch
                          ? "bg-yellow-100/60 dark:bg-yellow-900/20"
                          : ""
                    }`}
                  >
                    {line ? highlightText(line) : <>&nbsp;</>}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    )
  },
)
