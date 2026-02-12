import type { ItemProfile } from "@/api/types"
import { CardSection, ChapterTag, EntityLink } from "./CardSection"

interface ItemCardProps {
  profile: ItemProfile
  onEntityClick: (name: string, type: string) => void
}

export function ItemCard({ profile, onEntityClick }: ItemCardProps) {
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
      <CardSection title="持有流转" defaultLimit={10}>
        {flow.map((f, i) => (
          <div key={i} className="text-sm">
            <ChapterTag chapter={f.chapter} />
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
      <CardSection title="关联物品" defaultLimit={10}>
        {related_items.map((name) => (
          <div key={name} className="text-sm">
            <EntityLink name={name} type="item" onClick={onEntityClick} />
          </div>
        ))}
      </CardSection>

      {/* Stats */}
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
}

function formatStatLabel(key: string): string {
  const labels: Record<string, string> = {
    chapter_count: "出现章节",
    first_chapter: "首次出现",
    flow_count: "流转次数",
  }
  return labels[key] ?? key
}
