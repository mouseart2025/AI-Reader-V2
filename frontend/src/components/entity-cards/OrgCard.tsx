import { memo } from "react"
import type { OrgProfile } from "@/api/types"
import { CardSection, ChapterTag, EntityLink } from "./CardSection"

interface OrgCardProps {
  profile: OrgProfile
  onEntityClick: (name: string, type: string) => void
}

export const OrgCard = memo(function OrgCard({ profile, onEntityClick }: OrgCardProps) {
  const { member_events, org_relations, stats } = profile

  // Group member events by member
  const memberMap = new Map<string, typeof member_events>()
  for (const me of member_events) {
    const list = memberMap.get(me.member) ?? []
    list.push(me)
    memberMap.set(me.member, list)
  }

  return (
    <div className="space-y-0">
      {/* Basic Info */}
      <div className="border-b py-3">
        <div className="mb-1 flex items-center gap-3">
          <div className="bg-purple-100 dark:bg-purple-900/30 flex size-12 items-center justify-center rounded-full text-xl">
            &#x1F3DB;
          </div>
          <div>
            <h3 className="text-lg font-bold">{profile.name}</h3>
            {profile.org_type && (
              <span className="text-muted-foreground text-xs">{profile.org_type}</span>
            )}
          </div>
        </div>
      </div>

      {/* Members */}
      <CardSection title="成员变动" defaultLimit={10}>
        {Array.from(memberMap.entries()).map(([member, events]) => (
          <div key={member} className="text-sm">
            <EntityLink name={member} type="person" onClick={onEntityClick} />
            <span className="text-muted-foreground ml-1 text-xs">
              {events.map((e) => `${e.action}${e.role ? `(${e.role})` : ""}`).join(" → ")}
            </span>
            <span className="ml-1">
              <ChapterTag chapter={events[events.length - 1].chapter} />
            </span>
          </div>
        ))}
      </CardSection>

      {/* Org Relations */}
      <CardSection title="组织关系" defaultLimit={10}>
        {org_relations.map((rel, i) => (
          <div key={i} className="text-sm">
            <ChapterTag chapter={rel.chapter} />
            <span className="ml-1.5">
              <EntityLink name={rel.other_org} type="org" onClick={onEntityClick} />
              <span className="text-muted-foreground ml-1">({rel.relation_type})</span>
            </span>
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
})

function formatStatLabel(key: string): string {
  const labels: Record<string, string> = {
    chapter_count: "相关章节",
    first_chapter: "首次出现",
    member_event_count: "成员变动数",
  }
  return labels[key] ?? key
}
