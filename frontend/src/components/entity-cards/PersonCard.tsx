import type { PersonProfile } from "@/api/types"
import { CardSection, ChapterTag, EntityLink } from "./CardSection"

interface PersonCardProps {
  profile: PersonProfile
  onEntityClick: (name: string, type: string) => void
}

export function PersonCard({ profile, onEntityClick }: PersonCardProps) {
  const { aliases, appearances, abilities, relations, items, experiences, stats } = profile

  // Group abilities by dimension
  const abilityGroups = new Map<string, typeof abilities>()
  for (const ab of abilities) {
    const group = abilityGroups.get(ab.dimension) ?? []
    group.push(ab)
    abilityGroups.set(ab.dimension, group)
  }

  return (
    <div className="space-y-0">
      {/* A. Basic Info */}
      <div className="border-b py-3">
        <div className="mb-1 flex items-center gap-3">
          <div className="bg-muted flex size-12 items-center justify-center rounded-full text-xl font-bold">
            {profile.name[0]}
          </div>
          <div>
            <h3 className="text-lg font-bold">{profile.name}</h3>
            {aliases.length > 0 && (
              <div className="text-muted-foreground text-xs">
                {aliases.map((a) => (
                  <span key={a.name} className="mr-2">
                    {a.name}
                    <span className="text-muted-foreground/50 ml-0.5">(Ch.{a.first_chapter})</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* B. Appearances — merged by description, latest first */}
      <CardSection title="外貌特征" defaultLimit={3}>
        {[...appearances].reverse().map((a, i) => (
          <div key={i} className="text-sm">
            <span className="text-muted-foreground text-xs">
              {a.chapters.length === 1
                ? `Ch.${a.chapters[0]}`
                : a.chapters.length <= 3
                  ? `Ch.${a.chapters.join(",")}`
                  : `Ch.${a.chapters[0]}–${a.chapters[a.chapters.length - 1]}(${a.chapters.length}次)`
              }
            </span>
            <span className="ml-1.5">{a.description}</span>
          </div>
        ))}
      </CardSection>

      {/* C. Relations */}
      <CardSection title="人物关系" defaultLimit={10}>
        {relations.map((rel) => {
          const latest = rel.stages[rel.stages.length - 1]
          return (
            <div key={rel.other_person} className="text-sm">
              <EntityLink
                name={rel.other_person}
                type="person"
                onClick={onEntityClick}
              />
              <span className="text-muted-foreground mx-1">—</span>
              <span>{latest?.relation_type ?? "未知"}</span>
              {rel.stages.length > 1 && (
                <span className="text-muted-foreground ml-1 text-xs">
                  ({rel.stages.map((s) => s.relation_type).join(" → ")})
                </span>
              )}
            </div>
          )
        })}
      </CardSection>

      {/* D. Abilities */}
      <CardSection title="能力" defaultLimit={8}>
        {Array.from(abilityGroups.entries()).flatMap(([dim, abs]) => [
          <div key={`dim-${dim}`} className="text-muted-foreground mt-1 text-xs font-medium first:mt-0">
            {dim}
          </div>,
          ...abs.map((ab, i) => (
            <div key={`${dim}-${i}`} className="text-sm pl-2">
              <ChapterTag chapter={ab.chapter} />
              <span className="ml-1.5 font-medium">{ab.name}</span>
              {ab.description && (
                <span className="text-muted-foreground ml-1">{ab.description}</span>
              )}
            </div>
          )),
        ])}
      </CardSection>

      {/* E. Items */}
      <CardSection title="物品关系" defaultLimit={5}>
        {items.map((it, i) => (
          <div key={i} className="text-sm">
            <ChapterTag chapter={it.chapter} />
            <span className="ml-1.5">{it.action}</span>
            <EntityLink
              name={it.item_name}
              type="item"
              onClick={onEntityClick}
            />
            {it.description && (
              <span className="text-muted-foreground ml-1 text-xs">
                ({it.description})
              </span>
            )}
          </div>
        ))}
      </CardSection>

      {/* F. Experiences */}
      <CardSection title="经历" defaultLimit={5}>
        {[...experiences].reverse().map((exp, i) => (
          <div key={i} className="text-sm">
            <ChapterTag chapter={exp.chapter} />
            <span className="ml-1.5">{exp.summary}</span>
            {exp.location && (
              <span className="text-muted-foreground ml-1">
                @<EntityLink
                  name={exp.location}
                  type="location"
                  onClick={onEntityClick}
                />
              </span>
            )}
          </div>
        ))}
      </CardSection>

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
}

function formatStatLabel(key: string): string {
  const labels: Record<string, string> = {
    chapter_count: "出场章节",
    first_chapter: "首次出场",
    last_chapter: "最后出场",
    relation_count: "关系数",
  }
  return labels[key] ?? key
}
