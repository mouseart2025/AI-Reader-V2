import { memo, useEffect, useState } from "react"
import type { LocationProfile } from "@/api/types"
import { fetchLocationSpatialSummary } from "@/api/client"
import { CardSection, ChapterTag, EntityLink } from "./CardSection"
import { EntityScenes } from "./EntityScenes"
import { LocationMiniMap } from "./LocationMiniMap"

interface LocationCardProps {
  profile: LocationProfile
  onEntityClick: (name: string, type: string) => void
  onChapterClick?: (ch: number) => void
  novelId?: string
}

interface SpatialRelation {
  source: string
  target: string
  relation_type: string
  value: string
  chapters: number[]
}

export const LocationCard = memo(function LocationCard({ profile, onEntityClick, onChapterClick, novelId }: LocationCardProps) {
  const { descriptions, visitors, events, stats } = profile

  const residents = visitors.filter((v) => v.is_resident)
  const passersby = visitors.filter((v) => !v.is_resident)

  // Lazy-load spatial relationships
  const [spatialRels, setSpatialRels] = useState<SpatialRelation[]>([])
  useEffect(() => {
    if (!novelId) return
    fetchLocationSpatialSummary(novelId, profile.name).then(setSpatialRels)
  }, [novelId, profile.name])

  return (
    <div className="space-y-0">
      {/* A. Basic Info */}
      <div className="border-b py-3">
        <div className="mb-1 flex items-center gap-3">
          <div className="bg-green-100 dark:bg-green-900/30 flex size-12 items-center justify-center rounded-full text-xl">
            📍
          </div>
          <div>
            <h3 className="text-lg font-bold">{profile.name}</h3>
            {profile.location_type && (
              <span className="text-muted-foreground text-xs">{profile.location_type}</span>
            )}
          </div>
        </div>
      </div>

      {/* Mini position map */}
      <LocationMiniMap profile={profile} onEntityClick={onEntityClick} />

      {/* B. Spatial Hierarchy */}
      <div className="border-b py-3">
        <h4 className="text-muted-foreground mb-2 text-xs font-medium uppercase tracking-wide">
          空间层级
        </h4>
        <div className="text-sm">
          {profile.parent && (
            <div className="mb-1">
              <span className="text-muted-foreground">上级：</span>
              <EntityLink name={profile.parent} type="location" onClick={onEntityClick} />
            </div>
          )}
          {profile.children.length > 0 && (
            <div>
              <span className="text-muted-foreground">下辖：</span>
              {profile.children.map((child, i) => (
                <span key={child}>
                  {i > 0 && <span className="text-muted-foreground">、</span>}
                  <EntityLink name={child} type="location" onClick={onEntityClick} />
                </span>
              ))}
            </div>
          )}
          {!profile.parent && profile.children.length === 0 && (
            <p className="text-muted-foreground">无层级关系</p>
          )}
        </div>
      </div>

      {/* B2. Spatial Relationships */}
      {spatialRels.length > 0 && (
        <CardSection title="空间关系" defaultLimit={5}>
          {spatialRels.map((rel, i) => {
            const other = rel.source === profile.name ? rel.target : rel.source
            return (
              <div key={i} className="text-sm">
                <span className="text-muted-foreground text-xs mr-1">{rel.relation_type}</span>
                <EntityLink name={other} type="location" onClick={onEntityClick} />
                {rel.value && <span className="text-muted-foreground text-xs ml-1">({rel.value})</span>}
                <span className="text-muted-foreground text-[10px] ml-1">{rel.chapters.length}章</span>
              </div>
            )
          })}
        </CardSection>
      )}

      {/* C. Descriptions */}
      <CardSection title="环境描写" defaultLimit={3}>
        {descriptions.map((d, i) => (
          <div key={i} className="text-sm">
            <ChapterTag chapter={d.chapter} onClick={onChapterClick} />
            <span className="ml-1.5">{d.description}</span>
          </div>
        ))}
      </CardSection>

      {/* D. Visitors */}
      <CardSection title="到访人物" defaultLimit={10}>
        {[...residents, ...passersby].map((v) => (
          <div key={v.name} className="flex items-center gap-2 text-sm">
            <EntityLink name={v.name} type="person" onClick={onEntityClick} />
            {v.is_resident && (
              <span className="rounded bg-green-100 px-1 text-[10px] text-green-700 dark:bg-green-900/30 dark:text-green-400">
                常驻
              </span>
            )}
            <span className="text-muted-foreground text-xs">
              {v.chapters.length}章
            </span>
          </div>
        ))}
      </CardSection>

      {/* E. Events */}
      <CardSection title="发生事件" defaultLimit={5}>
        {events.map((ev, i) => (
          <div key={i} className="text-sm">
            <ChapterTag chapter={ev.chapter} onClick={onChapterClick} />
            <span className="ml-1.5">{ev.summary}</span>
          </div>
        ))}
      </CardSection>

      {/* F. Scenes */}
      {novelId && <EntityScenes novelId={novelId} entityName={profile.name} onChapterClick={onChapterClick} />}

      {/* G. Stats */}
      <div className="py-3">
        <details>
          <summary className="text-muted-foreground cursor-pointer text-xs font-medium uppercase tracking-wide">
            数据统计
          </summary>
          <div className="mt-2 grid grid-cols-2 gap-2">
            {Object.entries(stats).map(([k, v]) => (
              <div key={k} className="rounded-md bg-muted/50 px-2 py-1 text-sm">
                <span className="text-muted-foreground text-xs">{formatStatLabel(k)}</span>
                <div className="font-medium">{v}</div>
              </div>
            ))}
          </div>
        </details>
      </div>
    </div>
  )
})

function formatStatLabel(key: string): string {
  const labels: Record<string, string> = {
    chapter_count: "提及章节",
    first_chapter: "首次出现",
    visitor_count: "到访人数",
    event_count: "事件数",
  }
  return labels[key] ?? key
}
