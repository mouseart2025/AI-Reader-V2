import { useState, useMemo } from "react"
import { MapPin, ArrowRight, X, Search } from "lucide-react"
import type { GeographyChapter } from "@/api/types"

interface GeographyPanelProps {
  context: GeographyChapter[]
  visible: boolean
  onClose: () => void
}

export function GeographyPanel({ context, visible, onClose }: GeographyPanelProps) {
  const [search, setSearch] = useState("")
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set())

  const filtered = useMemo(() => {
    if (!search.trim()) return context
    const q = search.trim().toLowerCase()
    return context
      .map((ch) => ({
        ...ch,
        entries: ch.entries.filter(
          (e) =>
            e.name.toLowerCase().includes(q) ||
            e.text.toLowerCase().includes(q),
        ),
      }))
      .filter((ch) => ch.entries.length > 0)
  }, [context, search])

  const toggleChapter = (ch: number) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(ch)) next.delete(ch)
      else next.add(ch)
      return next
    })
  }

  if (!visible) return null

  return (
    <div className="absolute right-0 top-0 bottom-0 w-80 bg-background/95 backdrop-blur border-l shadow-lg z-30 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b">
        <h3 className="text-sm font-medium">地理上下文</h3>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-muted transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Search */}
      <div className="px-3 py-2 border-b">
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索地点或描述..."
            className="w-full pl-7 pr-2 py-1.5 text-xs rounded-md border bg-background focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-2">
        {filtered.length === 0 ? (
          <p className="text-xs text-muted-foreground text-center py-4">
            {context.length === 0 ? "暂无地理描述数据" : "未找到匹配结果"}
          </p>
        ) : (
          filtered.map((ch) => (
            <div key={ch.chapter} className="border rounded-md">
              <button
                className="w-full flex items-center justify-between px-2 py-1.5 text-xs font-medium hover:bg-muted/50 transition-colors"
                onClick={() => toggleChapter(ch.chapter)}
              >
                <span>第 {ch.chapter} 章</span>
                <span className="text-muted-foreground">
                  {ch.entries.length} 条
                </span>
              </button>
              {!collapsed.has(ch.chapter) && (
                <div className="px-2 pb-2 space-y-1.5">
                  {ch.entries.map((entry, i) => (
                    <div
                      key={i}
                      className="flex gap-1.5 text-xs leading-relaxed"
                    >
                      <span className="mt-0.5 shrink-0">
                        {entry.type === "location" ? (
                          <MapPin className="h-3 w-3 text-green-600" />
                        ) : (
                          <ArrowRight className="h-3 w-3 text-blue-600" />
                        )}
                      </span>
                      <div>
                        <span className="font-medium">{entry.name}</span>
                        <span className="text-muted-foreground ml-1">
                          {entry.text}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
