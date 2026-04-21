import { memo } from "react"
import type { ItemProfile } from "@/api/types"
import { useI18n, type TranslationKey } from "@/i18n"
import { CardSection, ChapterTag, EntityLink } from "./CardSection"
import { EntityScenes } from "./EntityScenes"

interface ItemCardProps {
  profile: ItemProfile
  onEntityClick: (name: string, type: string) => void
  onChapterClick?: (ch: number) => void
  novelId?: string
}

export const ItemCard = memo(function ItemCard({ profile, onEntityClick, onChapterClick, novelId }: ItemCardProps) {
  const { t } = useI18n()
  const { flow, related_items, stats } = profile

  return (
    <div className="space-y-0">
      {/* Basic Info */}
      <div className="border-b py-3">
        <div className="mb-1 flex items-center gap-3">
          <div className="bg-orange-100 dark:bg-orange-900/30 flex size-12 items-center justify-center rounded-full text-xl">
            &#x2728;
          </div>
          <div>
            <h3 className="text-lg font-bold">{profile.name}</h3>
            {profile.item_type && (
              <span className="text-muted-foreground text-xs">{profile.item_type}</span>
            )}
          </div>
        </div>
      </div>

      {/* Flow Chain */}
      <CardSection title={t("entity.item.flow")} defaultLimit={10}>
        {flow.map((f, i) => (
          <div key={i} className="text-sm">
            <ChapterTag chapter={f.chapter} onClick={onChapterClick} />
            <span className="ml-1.5">
              <EntityLink name={f.actor} type="person" onClick={onEntityClick} />
              <span className="mx-1">{f.action}</span>
              {f.recipient && (
                <>
                  <span className="text-muted-foreground">→ </span>
                  <EntityLink name={f.recipient} type="person" onClick={onEntityClick} />
                </>
              )}
            </span>
            {f.description && (
              <span className="text-muted-foreground ml-1 text-xs">({f.description})</span>
            )}
          </div>
        ))}
      </CardSection>

      {/* Related Items */}
      <CardSection title={t("entity.item.relatedItems")} defaultLimit={10}>
        {related_items.map((name) => (
          <div key={name} className="text-sm">
            <EntityLink name={name} type="item" onClick={onEntityClick} />
          </div>
        ))}
      </CardSection>

      {/* Scenes */}
      {novelId && <EntityScenes novelId={novelId} entityName={profile.name} onChapterClick={onChapterClick} />}

      {/* Stats */}
      <div className="py-3">
        <details>
          <summary className="text-muted-foreground cursor-pointer text-xs font-medium uppercase tracking-wide">
            {t("entity.dataStats")}
          </summary>
          <div className="mt-2 grid grid-cols-2 gap-2">
            {Object.entries(stats).map(([k, v]) => (
              <div key={k} className="rounded-md bg-muted/50 px-2 py-1 text-sm">
                <span className="text-muted-foreground text-xs">{t(getItemStatLabelKey(k))}</span>
                <div className="font-medium">{v}</div>
              </div>
            ))}
          </div>
        </details>
      </div>
    </div>
  )
})

function getItemStatLabelKey(key: string): TranslationKey {
  const labels: Record<string, TranslationKey> = {
    chapter_count: "entity.stat.appearanceChapters",
    first_chapter: "entity.stat.firstAppearance",
    flow_count: "entity.stat.flowCount",
  }
  return labels[key] ?? "entity.stat.unknown"
}
