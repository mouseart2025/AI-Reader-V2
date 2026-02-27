import { memo } from "react"
import type { OrgProfile } from "@/api/types"
import { cn } from "@/lib/utils"
import { CardSection, ChapterTag, EntityLink } from "./CardSection"
import { EntityScenes } from "./EntityScenes"

interface OrgCardProps {
  profile: OrgProfile
  onEntityClick: (name: string, type: string) => void
  onChapterClick?: (ch: number) => void
  novelId?: string
}

const LEAVE_ACTIONS = new Set(["离开", "阵亡", "叛出", "逐出", "退出", "离去", "战死"])

const ORG_REL_COLORS: Record<string, string> = {
  "盟友": "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  "联盟": "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  "友好": "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  "敌对": "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  "对立": "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  "战争": "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  "从属": "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  "隶属": "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  "上下级": "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  "竞争": "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300",
}

export const OrgCard = memo(function OrgCard({ profile, onEntityClick, onChapterClick, novelId }: OrgCardProps) {
  const { member_events, org_relations, stats } = profile

  // Group member events by member
  const memberMap = new Map<string, typeof member_events>()
  for (const me of member_events) {
    const list = memberMap.get(me.member) ?? []
    list.push(me)
    memberMap.set(me.member, list)
  }

  // Current members: latest action is not a leave action
  const currentMembers = Array.from(memberMap.entries()).filter(([, events]) => {
    const lastAction = events[events.length - 1].action
    return !LEAVE_ACTIONS.has(lastAction)
  })

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

      {/* Current Members */}
      {currentMembers.length > 0 && (
        <CardSection title="当前成员" defaultLimit={10}>
          {currentMembers.map(([member, events]) => {
            const lastEvent = events[events.length - 1]
            return (
              <div key={member} className="text-sm flex items-center gap-1.5">
                <EntityLink name={member} type="person" onClick={onEntityClick} />
                {lastEvent.role && (
                  <span className="text-[10px] px-1 py-0.5 rounded bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400">
                    {lastEvent.role}
                  </span>
                )}
              </div>
            )
          })}
        </CardSection>
      )}

      {/* Member History */}
      <CardSection title="成员变动" defaultLimit={10}>
        {Array.from(memberMap.entries()).map(([member, events]) => (
          <div key={member} className="text-sm">
            <EntityLink name={member} type="person" onClick={onEntityClick} />
            <span className="text-muted-foreground ml-1 text-xs">
              {events.map((e) => `${e.action}${e.role ? `(${e.role})` : ""}`).join(" → ")}
            </span>
            <span className="ml-1">
              <ChapterTag chapter={events[events.length - 1].chapter} onClick={onChapterClick} />
            </span>
          </div>
        ))}
      </CardSection>

      {/* Org Relations */}
      <CardSection title="组织关系" defaultLimit={10}>
        {org_relations.map((rel, i) => (
          <div key={i} className="text-sm">
            <ChapterTag chapter={rel.chapter} onClick={onChapterClick} />
            <span className="ml-1.5">
              <EntityLink name={rel.other_org} type="org" onClick={onEntityClick} />
              <span className={cn(
                "ml-1 text-[10px] px-1 py-0.5 rounded",
                ORG_REL_COLORS[rel.relation_type] ?? "bg-muted text-muted-foreground",
              )}>
                {rel.relation_type}
              </span>
            </span>
          </div>
        ))}
      </CardSection>

      {/* Scenes */}
      {novelId && <EntityScenes novelId={novelId} entityName={profile.name} onChapterClick={onChapterClick} />}

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
