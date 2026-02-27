import { useEffect, useState } from "react"
import { fetchEntityScenes } from "@/api/client"
import { CardSection, ChapterTag } from "./CardSection"

interface SceneItem {
  chapter: number
  index: number
  title: string
  location: string
  emotional_tone: string
  summary: string
  role: string
}

const TONE_COLORS: Record<string, string> = {
  "战斗": "text-red-600 dark:text-red-400",
  "紧张": "text-orange-600 dark:text-orange-400",
  "悲伤": "text-blue-600 dark:text-blue-400",
  "欢乐": "text-yellow-600 dark:text-yellow-400",
  "平静": "text-green-600 dark:text-green-400",
}

interface EntityScenesProps {
  novelId: string
  entityName: string
  onChapterClick?: (ch: number) => void
}

export function EntityScenes({ novelId, entityName, onChapterClick }: EntityScenesProps) {
  const [scenes, setScenes] = useState<SceneItem[]>([])
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    setLoaded(false)
    fetchEntityScenes(novelId, entityName)
      .then((data) => setScenes(data))
      .finally(() => setLoaded(true))
  }, [novelId, entityName])

  if (!loaded || scenes.length === 0) return null

  return (
    <CardSection title="参与场景" defaultLimit={5}>
      {scenes.map((s, i) => (
        <div key={i} className="text-sm">
          <ChapterTag chapter={s.chapter} onClick={onChapterClick} />
          <span className="ml-1.5 font-medium">{s.title || `场景${s.index + 1}`}</span>
          {s.emotional_tone && (
            <span className={`ml-1 text-[10px] ${TONE_COLORS[s.emotional_tone] ?? "text-muted-foreground"}`}>
              {s.emotional_tone}
            </span>
          )}
          {s.role && s.role !== "提及" && (
            <span className="ml-1 text-[10px] text-muted-foreground">
              ({s.role})
            </span>
          )}
          {s.summary && (
            <p className="text-xs text-muted-foreground mt-0.5 truncate pl-[52px]">{s.summary}</p>
          )}
        </div>
      ))}
    </CardSection>
  )
}
