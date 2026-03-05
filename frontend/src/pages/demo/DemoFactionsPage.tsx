/**
 * DemoFactionsPage — displays organization/faction list with member info.
 */
import { useMemo, useState } from "react"
import { useDemoData } from "@/app/DemoContext"
import { useEntityCardStore } from "@/stores/entityCardStore"

interface Org {
  id: string
  name: string
  type: string
  member_count: number
}

interface MemberEntry {
  person: string
  role?: string
  status?: string
}

interface FactionsData {
  orgs: Org[]
  relations: unknown[]
  members: Record<string, MemberEntry[]>
  analyzed_range?: unknown
}

const TYPE_COLORS: Record<string, string> = {
  家族: "#8b5cf6",
  院落: "#10b981",
  寺庙: "#f59e0b",
  官府: "#3b82f6",
  帮派: "#ef4444",
  门派: "#ec4899",
  军队: "#06b6d4",
  商铺: "#84cc16",
  宗教: "#f97316",
}

export default function DemoFactionsPage() {
  const { data } = useDemoData()
  const factionsData = data.factions as unknown as FactionsData
  const [typeFilter, setTypeFilter] = useState<string | null>(null)
  const [expandedOrg, setExpandedOrg] = useState<string | null>(null)
  const openCard = useEntityCardStore((s) => s.openCard)

  const orgTypes = useMemo(() => {
    const types = new Map<string, number>()
    factionsData.orgs.forEach((o) => {
      types.set(o.type, (types.get(o.type) || 0) + 1)
    })
    return [...types.entries()].sort((a, b) => b[1] - a[1])
  }, [factionsData.orgs])

  const filtered = useMemo(() => {
    let orgs = factionsData.orgs
    if (typeFilter) orgs = orgs.filter((o) => o.type === typeFilter)
    return orgs.sort((a, b) => b.member_count - a.member_count)
  }, [factionsData.orgs, typeFilter])

  return (
    <div className="flex h-full flex-col bg-slate-950">
      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-800 bg-slate-900/80 px-4 py-2">
        <span className="text-xs text-slate-400">
          {filtered.length} 组织 / {factionsData.orgs.length} 总计
        </span>
        <div className="flex flex-wrap gap-1">
          <button
            onClick={() => setTypeFilter(null)}
            className={`rounded px-2 py-0.5 text-xs font-medium transition ${
              !typeFilter ? "bg-blue-500/20 text-blue-400" : "text-slate-500 hover:bg-slate-800"
            }`}
          >
            全部
          </button>
          {orgTypes.map(([type, count]) => (
            <button
              key={type}
              onClick={() => setTypeFilter(typeFilter === type ? null : type)}
              className="rounded px-2 py-0.5 text-xs font-medium transition"
              style={{
                backgroundColor: typeFilter === type ? (TYPE_COLORS[type] || "#6b7280") + "20" : "#1e293b",
                color: typeFilter === type ? TYPE_COLORS[type] || "#6b7280" : "#64748b",
                border: `1px solid ${typeFilter === type ? (TYPE_COLORS[type] || "#6b7280") + "40" : "#334155"}`,
              }}
            >
              {type} ({count})
            </button>
          ))}
        </div>
      </div>

      {/* Org list */}
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-4xl space-y-3">
          {filtered.map((org) => {
            const members = factionsData.members[org.name] || []
            const isExpanded = expandedOrg === org.id
            return (
              <div key={org.id} className="rounded-lg border border-slate-700/50 bg-slate-900">
                <button
                  className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-slate-800/50 transition"
                  onClick={() => setExpandedOrg(isExpanded ? null : org.id)}
                >
                  <span
                    className="inline-block h-3 w-3 rounded-full flex-shrink-0"
                    style={{ backgroundColor: TYPE_COLORS[org.type] || "#6b7280" }}
                  />
                  <span className="font-medium flex-1 text-slate-200">{org.name}</span>
                  <span className="rounded-full bg-slate-800 px-2 py-0.5 text-xs text-slate-400">
                    {org.type}
                  </span>
                  <span className="text-xs text-slate-500">{org.member_count} 人</span>
                  <span className="text-slate-500 text-xs">{isExpanded ? "▲" : "▼"}</span>
                </button>
                {isExpanded && members.length > 0 && (
                  <div className="border-t border-slate-800 px-4 py-3">
                    <div className="flex flex-wrap gap-2">
                      {members.slice(0, 30).map((m, i) => (
                        <button
                          key={i}
                          onClick={() => openCard(m.person, "person")}
                          className="rounded-full bg-blue-500/15 px-3 py-1 text-xs text-blue-400 hover:bg-blue-500/25 transition"
                        >
                          {m.person}
                          {m.role && <span className="ml-1 text-blue-500/60">({m.role})</span>}
                        </button>
                      ))}
                      {members.length > 30 && (
                        <span className="rounded-full bg-slate-800 px-3 py-1 text-xs text-slate-500">
                          +{members.length - 30} 更多
                        </span>
                      )}
                    </div>
                  </div>
                )}
                {isExpanded && members.length === 0 && (
                  <div className="border-t border-slate-800 px-4 py-3 text-xs text-slate-500">暂无成员数据</div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
