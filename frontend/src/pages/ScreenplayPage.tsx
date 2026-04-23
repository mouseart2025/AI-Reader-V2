import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useParams } from "react-router-dom"
import { fetchChapters, fetchChapterContent, fetchChapterScenes, fetchEntities } from "@/api/client"
import type { Chapter, ChapterContent, ChapterEntity, EntityType, Scene } from "@/api/types"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { EntityCardDrawer } from "@/components/entity-cards/EntityCardDrawer"
import { highlightText } from "@/lib/entityHighlight"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { useI18n, type TranslationKey } from "@/i18n"
import {
  sceneEventTypeLabel,
  sceneEventTypeStyle,
  sceneTimeOfDayLabel,
  sceneToneLabel,
  sceneToneStyle,
  shouldDisplaySceneTone,
} from "@/lib/domainLabels"

// ── Scene border colors (one per scene, cycling) ─

const SCENE_BORDER_COLORS = [
  "border-l-blue-500",
  "border-l-emerald-500",
  "border-l-amber-500",
  "border-l-purple-500",
  "border-l-rose-500",
  "border-l-cyan-500",
  "border-l-indigo-500",
  "border-l-lime-500",
]

const SCREENPLAY_TIME_LABEL_KEYS: Record<string, TranslationKey> = {
  morning: "screenplay.time.morningScene",
  noon: "screenplay.time.noonScene",
  dusk: "screenplay.time.duskScene",
  night: "screenplay.time.nightScene",
}

const TIME_OF_DAY_IDS: Record<string, string> = {
  "早": "morning",
  "晨": "morning",
  "午": "noon",
  "晚": "dusk",
  "暮": "dusk",
  "夜": "night",
}

function screenplayTimeLabel(
  t: (key: TranslationKey, params?: Record<string, number | string>) => string,
  scene: Scene,
) {
  const timeId = scene.time_of_day_id || TIME_OF_DAY_IDS[scene.time_of_day || ""]
  return timeId && SCREENPLAY_TIME_LABEL_KEYS[timeId] ? t(SCREENPLAY_TIME_LABEL_KEYS[timeId]) : ""
}

// ── Side-by-side view (N10.2) + Fullscreen view (N10.3) ────────────

type ViewMode = "split" | "fullscreen"

export default function ScreenplayPage() {
  const { t } = useI18n()
  const { novelId } = useParams<{ novelId: string }>()

  const [chapters, setChapters] = useState<Chapter[]>([])
  const [currentChapter, setCurrentChapter] = useState<ChapterContent | null>(null)
  const [currentChapterNum, setCurrentChapterNum] = useState(1)
  const [scenes, setScenes] = useState<Scene[]>([])
  const [loading, setLoading] = useState(false)
  const [activeSceneIndex, setActiveSceneIndex] = useState(0)
  const [viewMode, setViewMode] = useState<ViewMode>("split")
  const [entities, setEntities] = useState<ChapterEntity[]>([])
  const [aliasMap, setAliasMap] = useState<Record<string, string>>({})

  const openEntityCard = useEntityCardStore((s) => s.openCard)
  const textRef = useRef<HTMLDivElement>(null)

  const handleEntityClick = useCallback(
    (name: string, type: string) => {
      const canonical = aliasMap[name] ?? name
      openEntityCard(canonical, type as EntityType)
    },
    [openEntityCard, aliasMap],
  )

  // Load chapter list + entities on mount
  useEffect(() => {
    if (!novelId) return
    fetchChapters(novelId).then(({ chapters: chs }) => {
      setChapters(chs)
      if (chs.length > 0) setCurrentChapterNum(chs[0].chapter_num)
    })
    // Load all entities for highlighting
    fetchEntities(novelId).then(({ entities: allEnts, alias_map: aliasData }) => {
      const entityList: ChapterEntity[] = allEnts.map((e) => ({
        name: e.name,
        type: e.type as ChapterEntity["type"],
      }))
      if (aliasData) {
        for (const [alias, canonical] of Object.entries(aliasData)) {
          const ent = allEnts.find((e) => e.name === canonical)
          if (ent) {
            entityList.push({ name: alias, type: ent.type as ChapterEntity["type"] })
          }
        }
        setAliasMap(aliasData)
      }
      setEntities(entityList)
    })
  }, [novelId])

  // Load chapter content + scenes when chapter changes
  useEffect(() => {
    if (!novelId || !currentChapterNum) return
    setLoading(true)
    Promise.all([
      fetchChapterContent(novelId, currentChapterNum),
      fetchChapterScenes(novelId, currentChapterNum),
    ]).then(([content, scenesResp]) => {
      setCurrentChapter(content)
      setScenes(scenesResp.scenes)
      setActiveSceneIndex(0)
    }).finally(() => setLoading(false))
  }, [novelId, currentChapterNum])

  // Split content into paragraphs
  const paragraphs = useMemo(() => {
    if (!currentChapter?.content) return []
    return currentChapter.content.split("\n").filter((p) => p.trim())
  }, [currentChapter])

  // Build a map: paragraph index -> scene index for left-side border coloring
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

  // Jump to paragraph range when clicking a scene
  const scrollToScene = useCallback((scene: Scene, index: number) => {
    setActiveSceneIndex(index)
    if (viewMode === "fullscreen") return
    if (!scene.paragraph_range || !textRef.current) return
    const paraEl = textRef.current.querySelector(`[data-para="${scene.paragraph_range[0]}"]`)
    if (paraEl) paraEl.scrollIntoView({ behavior: "smooth", block: "start" })
  }, [viewMode])

  // Keyboard navigation for fullscreen mode
  useEffect(() => {
    if (viewMode !== "fullscreen") return
    const handler = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        setActiveSceneIndex((i) => Math.max(0, i - 1))
      } else if (e.key === "ArrowRight" || e.key === "ArrowDown") {
        setActiveSceneIndex((i) => Math.min(scenes.length - 1, i + 1))
      } else if (e.key === "Escape") {
        setViewMode("split")
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [viewMode, scenes.length])

  if (!novelId) return null

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-2 border-b px-4 py-1.5">
        {/* Chapter selector */}
        <select
          className="rounded border bg-background px-2 py-1 text-sm"
          value={currentChapterNum}
          onChange={(e) => setCurrentChapterNum(Number(e.target.value))}
        >
          {chapters.map((ch) => (
            <option key={ch.chapter_num} value={ch.chapter_num}>
              {ch.title || t("screenplay.chapterFallback", { chapter: ch.chapter_num })}
            </option>
          ))}
        </select>

        <div className="flex-1" />

        <span className="text-xs text-muted-foreground">
          {t("screenplay.sceneCount", { count: scenes.length })}
        </span>

        <div className="flex rounded-md border">
          <Button
            variant={viewMode === "split" ? "default" : "ghost"}
            size="xs"
            onClick={() => setViewMode("split")}
          >
            {t("screenplay.mode.split")}
          </Button>
          <Button
            variant={viewMode === "fullscreen" ? "default" : "ghost"}
            size="xs"
            onClick={() => setViewMode("fullscreen")}
          >
            {t("screenplay.mode.fullscreen")}
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
          {t("common.loading")}
        </div>
      ) : viewMode === "split" ? (
        <SplitView
          paragraphs={paragraphs}
          scenes={scenes}
          activeSceneIndex={activeSceneIndex}
          paraSceneMap={paraSceneMap}
          textRef={textRef}
          entities={entities}
          onEntityClick={handleEntityClick}
          onSceneClick={scrollToScene}
        />
      ) : (
        <FullscreenView
          scenes={scenes}
          paragraphs={paragraphs}
          activeSceneIndex={activeSceneIndex}
          entities={entities}
          onEntityClick={handleEntityClick}
          onSceneClick={(_, i) => setActiveSceneIndex(i)}
        />
      )}

      {/* Entity Card Drawer */}
      {novelId && <EntityCardDrawer novelId={novelId} />}
    </div>
  )
}

// ── Split View (N10.2) ──────────────────────────

function SplitView({
  paragraphs,
  scenes,
  activeSceneIndex,
  paraSceneMap,
  textRef,
  entities,
  onEntityClick,
  onSceneClick,
}: {
  paragraphs: string[]
  scenes: Scene[]
  activeSceneIndex: number
  paraSceneMap: Map<number, number>
  textRef: React.RefObject<HTMLDivElement | null>
  entities: ChapterEntity[]
  onEntityClick: (name: string, type: string) => void
  onSceneClick: (scene: Scene, index: number) => void
}) {
  const { t } = useI18n()

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Left: original text with entity highlighting + scene border bars */}
      <div ref={textRef} className="flex-1 overflow-y-auto border-r p-4">
        {paragraphs.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("screenplay.noContent")}</p>
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
                  "mb-2 text-sm leading-relaxed transition-colors",
                  sceneIdx != null && `border-l-3 pl-3 ${borderColor}`,
                  isActive && "bg-accent/30 rounded-r",
                )}
              >
                {highlightText(p, entities, onEntityClick)}
              </p>
            )
          })
        )}
      </div>

      {/* Right: scene list */}
      <div className="w-[22rem] shrink-0 overflow-y-auto p-3">
        {scenes.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("screenplay.noScenesDetected")}</p>
        ) : (
          scenes.map((scene, i) => (
            <SceneCard
              key={i}
              scene={scene}
              active={i === activeSceneIndex}
              borderColor={SCENE_BORDER_COLORS[i % SCENE_BORDER_COLORS.length]}
              onClick={() => onSceneClick(scene, i)}
            />
          ))
        )}
      </div>
    </div>
  )
}

// ── Fullscreen View (N10.3) ──────────────────────

function FullscreenView({
  scenes,
  paragraphs,
  activeSceneIndex,
  entities,
  onEntityClick,
  onSceneClick,
}: {
  scenes: Scene[]
  paragraphs: string[]
  activeSceneIndex: number
  entities: ChapterEntity[]
  onEntityClick: (name: string, type: string) => void
  onSceneClick: (scene: Scene, index: number) => void
}) {
  const { t } = useI18n()
  const scene = scenes[activeSceneIndex]

  if (!scene) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        {t("screenplay.noSceneData")}
      </div>
    )
  }

  // Extract scene paragraphs
  const sceneParagraphs = scene.paragraph_range
    ? paragraphs.slice(scene.paragraph_range[0], scene.paragraph_range[1] + 1)
    : []

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Scene navigation tabs */}
      <div className="flex gap-1 overflow-x-auto border-b px-4 py-1.5">
        {scenes.map((s, i) => (
          <button
            key={i}
            className={cn(
              "whitespace-nowrap rounded px-2 py-0.5 text-xs transition-colors",
              i === activeSceneIndex
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground hover:bg-muted/80",
            )}
            onClick={() => onSceneClick(s, i)}
          >
            {i + 1}. {s.title.slice(0, 12)}
          </button>
        ))}
      </div>

      {/* Scene content */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-2xl">
          {/* Scene header — professional format */}
          <div className="mb-6">
            <div className="flex items-center gap-3">
              <h2 className="text-lg font-bold">
                {t("screenplay.sceneTitle", { number: scene.index + 1, title: scene.title })}
              </h2>
              {scene.time_of_day && (
                <span className="shrink-0 text-sm text-muted-foreground">
                  {screenplayTimeLabel(t, scene)}
                </span>
              )}
            </div>

            <div className="mt-2 flex flex-wrap gap-2 text-xs">
              {scene.location && (
                <span className="rounded bg-green-100 px-1.5 py-0.5 text-green-700 dark:bg-green-900/30 dark:text-green-300">
                  {scene.location}
                </span>
              )}
              {scene.event_type && (
                <span className={cn("rounded px-1.5 py-0.5", sceneEventTypeStyle(scene.event_type_id, scene.event_type))}>
                  {sceneEventTypeLabel(t, scene.event_type_id, scene.event_type)}
                </span>
              )}
              {scene.dialogue_count > 0 && (
                <span className="rounded bg-blue-100 px-1.5 py-0.5 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300">
                  {t("screenplay.dialogueParagraphCount", { count: scene.dialogue_count })}
                </span>
              )}
              {scene.emotional_tone && (
                <span className={cn("rounded px-1.5 py-0.5", sceneToneStyle(scene.emotional_tone_id, scene.emotional_tone))}>
                  {sceneToneLabel(t, scene.emotional_tone_id, scene.emotional_tone)}
                </span>
              )}
            </div>

            {/* Key dialogue */}
            {scene.key_dialogue && scene.key_dialogue.length > 0 && (
              <div className="mt-3 space-y-1">
                {scene.key_dialogue.map((d, i) => (
                  <p key={i} className="text-sm italic text-muted-foreground border-l-2 border-primary/30 pl-2">
                    {d}
                  </p>
                ))}
              </div>
            )}

            {/* Characters with roles */}
            {scene.character_roles && scene.character_roles.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-1">
                {scene.character_roles.map((cr) => (
                  <span
                    key={cr.name}
                    className={cn(
                      "rounded-full px-2 py-0.5 text-xs",
                      cr.role === "主"
                        ? "bg-blue-100 font-medium text-blue-700 dark:bg-blue-900/30 dark:text-blue-300"
                        : cr.role === "配"
                          ? "bg-muted text-foreground"
                          : "bg-muted text-muted-foreground",
                    )}
                  >
                    {cr.name}
                    {cr.role === "主" && <span className="ml-0.5 text-[10px]">{t("shared.scenePanel.mainRole")}</span>}
                  </span>
                ))}
              </div>
            ) : scene.characters.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {scene.characters.map((c) => (
                  <span
                    key={c}
                    className="rounded-full bg-muted px-2 py-0.5 text-xs"
                  >
                    {c}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Scene text with entity highlighting */}
          <div className="space-y-2">
            {sceneParagraphs.length > 0 ? (
              sceneParagraphs.map((p, i) => (
                <p key={i} className="text-sm leading-relaxed">
                  {highlightText(p, entities, onEntityClick)}
                </p>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">
                {t("screenplay.noParagraphMapping")}
              </p>
            )}
          </div>

          {/* Events */}
          {scene.events && scene.events.length > 0 && (
            <div className="mt-6 border-t pt-4">
              <h3 className="mb-2 text-xs font-medium text-muted-foreground">
                {t("screenplay.events")}
              </h3>
              <ul className="space-y-1">
                {scene.events.map((evt, i) => (
                  <li key={i} className="text-xs text-muted-foreground">
                    <span className="font-medium">{evt.type || t("screenplay.eventFallback")}</span>
                    {" — "}
                    {evt.summary}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>

      {/* Bottom nav hint */}
      <div className="border-t px-4 py-1 text-center text-xs text-muted-foreground">
        {t("screenplay.navHint", { current: activeSceneIndex + 1, total: scenes.length })}
      </div>
    </div>
  )
}

// ── Scene Card (Professional screenplay format) ──

function SceneCard({
  scene,
  active,
  borderColor,
  onClick,
}: {
  scene: Scene
  active: boolean
  borderColor: string
  onClick: () => void
}) {
  const { t } = useI18n()

  return (
    <button
      className={cn(
        "mb-2 w-full rounded-lg border-l-3 border p-3 text-left transition-colors",
        borderColor,
        active
          ? "border-r-primary border-y-primary bg-primary/5"
          : "border-r-border border-y-border hover:border-r-primary/50 hover:border-y-primary/50",
      )}
      onClick={onClick}
    >
      {/* Header: scene number + time */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-medium text-primary">
            {scene.index + 1}
          </span>
          <span className="truncate text-sm font-medium">{scene.title}</span>
        </div>
        {scene.time_of_day && (
          <span className="shrink-0 text-[10px] text-muted-foreground">
            {sceneTimeOfDayLabel(t, scene.time_of_day_id, scene.time_of_day)}
          </span>
        )}
      </div>

      {/* Location */}
      {scene.location && (
        <div className="mt-1.5">
          <span className="inline-block rounded bg-green-100 px-1 py-0.5 text-[11px] text-green-700 dark:bg-green-900/30 dark:text-green-300">
            {scene.location}
          </span>
        </div>
      )}

      {/* Key dialogue (first line only, if available) */}
      {scene.key_dialogue && scene.key_dialogue.length > 0 && (
        <p className="mt-1.5 truncate text-xs italic text-muted-foreground">
          {scene.key_dialogue[0]}
        </p>
      )}

      {/* Characters with roles */}
      {scene.character_roles && scene.character_roles.length > 0 ? (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {scene.character_roles.slice(0, 6).map((cr) => (
            <span
              key={cr.name}
              className={cn(
                "text-[11px]",
                cr.role === "主" ? "font-medium text-foreground" : "text-muted-foreground",
              )}
            >
              {cr.name}
              {cr.role === "主" && <span className="text-[9px]">{t("shared.scenePanel.mainRole")}</span>}
            </span>
          ))}
          {scene.character_roles.length > 6 && (
            <span className="text-[11px] text-muted-foreground">
              +{scene.character_roles.length - 6}
            </span>
          )}
        </div>
      ) : scene.characters.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {scene.characters.slice(0, 5).map((c) => (
            <span key={c} className="text-[11px] text-muted-foreground">
              {c}
            </span>
          ))}
          {scene.characters.length > 5 && (
            <span className="text-[11px] text-muted-foreground">
              +{scene.characters.length - 5}
            </span>
          )}
        </div>
      )}

      {/* Bottom: event_type + dialogue count + emotional tone */}
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {scene.event_type && (
          <span className={cn(
            "rounded px-1 py-0.5 text-[10px]",
            sceneEventTypeStyle(scene.event_type_id, scene.event_type),
          )}>
            {sceneEventTypeLabel(t, scene.event_type_id, scene.event_type)}
          </span>
        )}
        {scene.dialogue_count > 0 && (
          <span className="rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
            {t("shared.scenePanel.dialogueCount", { count: scene.dialogue_count })}
          </span>
        )}
        {scene.emotional_tone && shouldDisplaySceneTone(scene.emotional_tone_id, scene.emotional_tone) && (
          <span className={cn(
            "rounded px-1 py-0.5 text-[10px]",
            sceneToneStyle(scene.emotional_tone_id, scene.emotional_tone),
          )}>
            {sceneToneLabel(t, scene.emotional_tone_id, scene.emotional_tone)}
          </span>
        )}
      </div>
    </button>
  )
}
