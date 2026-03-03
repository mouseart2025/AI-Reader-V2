/**
 * DemoEntityCardDrawer — entity card drawer for demo mode.
 * Builds simplified profiles from static demo data (graph + encyclopedia)
 * instead of fetching from backend API.
 */
import { useCallback, useEffect, useMemo } from "react"
import { useNavigate, useParams } from "react-router-dom"
import type {
  EntityProfile,
  EntityType,
  PersonProfile,
  LocationProfile,
  ItemProfile,
  OrgProfile,
} from "@/api/types"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { useDemoData } from "@/app/DemoContext"
import { PersonCard } from "./PersonCard"
import { LocationCard } from "./LocationCard"
import { ItemCard } from "./ItemCard"
import { OrgCard } from "./OrgCard"

interface GraphNode {
  id: string
  name: string
  type: string
  chapter_count: number
  org: string
  aliases?: string[]
}

interface GraphEdge {
  source: string | GraphNode
  target: string | GraphNode
  relation_type: string
  all_types?: string[]
  weight: number
  chapters: number[]
  category?: string
}

interface EncEntry {
  name: string
  type: string
  category: string
  definition: string
  first_chapter: number
  chapter_count?: number
  parent?: string | null
  tier?: string
}

/** Build a simplified entity profile from demo data */
function buildDemoProfile(
  name: string,
  type: EntityType,
  graphNodes: GraphNode[],
  graphEdges: GraphEdge[],
  encEntries: EncEntry[],
): EntityProfile | null {
  const encEntry = encEntries.find((e) => e.name === name && e.type === type)
    ?? encEntries.find((e) => e.name === name)
  const graphNode = graphNodes.find((n) => n.id === name || n.name === name)

  if (type === "person") {
    // Build relations from graph edges
    const edges = graphEdges.filter((e) => {
      const src = typeof e.source === "string" ? e.source : e.source.id
      const tgt = typeof e.target === "string" ? e.target : e.target.id
      return src === name || tgt === name
    })
    const relations = edges.map((e) => {
      const src = typeof e.source === "string" ? e.source : e.source.id
      const tgt = typeof e.target === "string" ? e.target : e.target.id
      return {
        other_person: src === name ? tgt : src,
        stages: [{
          chapters: e.chapters ?? [],
          relation_type: e.relation_type ?? "相关",
          evidences: [] as string[],
          evidence: "",
        }],
        category: e.category ?? "other",
      }
    })

    return {
      name,
      type: "person",
      aliases: graphNode?.aliases?.map((a) => ({ name: a, first_chapter: 0 })) ?? [],
      appearances: encEntry ? [{ chapters: [], description: encEntry.definition }] : [],
      abilities: [],
      relations,
      items: [],
      experiences: [],
      stats: {
        chapter_count: graphNode?.chapter_count ?? encEntry?.chapter_count ?? 0,
        first_chapter: encEntry?.first_chapter ?? 0,
        relation_count: relations.length,
      },
    } satisfies PersonProfile
  }

  if (type === "location") {
    const children = encEntries
      .filter((e) => e.type === "location" && e.parent === name)
      .map((e) => e.name)

    return {
      name,
      type: "location",
      location_type: encEntry?.tier ?? encEntry?.category ?? "",
      parent: encEntry?.parent ?? null,
      children,
      descriptions: encEntry
        ? [{ chapter: encEntry.first_chapter, description: encEntry.definition }]
        : [],
      visitors: [],
      events: [],
      stats: {
        chapter_count: encEntry?.chapter_count ?? 0,
        first_chapter: encEntry?.first_chapter ?? 0,
      },
    } satisfies LocationProfile
  }

  if (type === "item") {
    return {
      name,
      type: "item",
      item_type: encEntry?.category ?? "",
      flow: [],
      related_items: [],
      stats: {
        chapter_count: encEntry?.chapter_count ?? 0,
        first_chapter: encEntry?.first_chapter ?? 0,
      },
    } satisfies ItemProfile
  }

  if (type === "org") {
    return {
      name,
      type: "org",
      org_type: encEntry?.category ?? "",
      member_events: [],
      org_relations: [],
      stats: {
        chapter_count: encEntry?.chapter_count ?? 0,
        first_chapter: encEntry?.first_chapter ?? 0,
      },
    } satisfies OrgProfile
  }

  return null
}

export function DemoEntityCardDrawer() {
  const { novelSlug } = useParams()
  const navigate = useNavigate()
  const { data } = useDemoData()

  const {
    open, loading, profile, error: cardError,
    breadcrumbs, conceptPopup,
    setProfile, setError,
    navigateTo, goBack, close,
    closeConceptPopup,
  } = useEntityCardStore()

  const currentCrumb = breadcrumbs[breadcrumbs.length - 1]

  // Extract graph and encyclopedia data
  const graphData = data.graph as { nodes: GraphNode[]; edges: GraphEdge[] }
  const encEntries = useMemo(() => {
    const enc = data.encyclopedia as { entries: EncEntry[] } | null
    return enc?.entries ?? []
  }, [data.encyclopedia])

  // Build profile from demo data instead of API fetch
  useEffect(() => {
    if (!open || !currentCrumb) return

    const built = buildDemoProfile(
      currentCrumb.name,
      currentCrumb.type,
      graphData.nodes ?? [],
      graphData.edges ?? [],
      encEntries,
    )

    if (built) {
      setProfile(built)
    } else {
      setError("未找到该实体的 Demo 数据")
    }
  }, [open, currentCrumb?.name, currentCrumb?.type, graphData, encEntries, setProfile, setError])

  const handleEntityClick = useCallback(
    (name: string, type: string) => {
      if (type === "concept") return
      navigateTo(name, type as EntityType)
    },
    [navigateTo],
  )

  // No-op in demo mode — there's no reading page
  const handleChapterClick = useCallback(() => {}, [])

  // Close on Escape
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") close()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open, close])

  if (!open) return null

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-40 bg-black/20" onClick={close} />

      {/* Drawer */}
      <div className="fixed top-0 right-0 z-50 flex h-screen w-full flex-col border-l bg-white shadow-lg sm:w-[420px]">
        {/* Header with breadcrumbs */}
        <div className="flex items-center gap-2 border-b px-4 py-3">
          <div className="flex-1 overflow-hidden">
            <div className="flex items-center gap-1 text-sm">
              {breadcrumbs.map((crumb, i) => (
                <span key={i} className="flex items-center gap-1">
                  {i > 0 && <span className="text-gray-400">&gt;</span>}
                  {i < breadcrumbs.length - 1 ? (
                    <button
                      className="truncate text-blue-600 hover:underline"
                      onClick={() => goBack(i)}
                    >
                      {crumb.name}
                    </button>
                  ) : (
                    <span className="truncate font-medium">{crumb.name}</span>
                  )}
                </span>
              ))}
            </div>
          </div>
          <button onClick={close} className="text-gray-400 hover:text-gray-600">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="size-4">
              <path d="M18 6 6 18" /><path d="m6 6 12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-4">
          {loading && (
            <div className="flex h-32 items-center justify-center">
              <p className="text-sm text-gray-400">加载中...</p>
            </div>
          )}

          {!loading && cardError && (
            <div className="flex h-32 flex-col items-center justify-center gap-2">
              <p className="text-sm text-red-500">{cardError}</p>
            </div>
          )}

          {!loading && !cardError && profile && (
            <>
              {/* Render the appropriate card component — no novelId → disables EntityScenes */}
              {profile.type === "person" && (
                <PersonCard profile={profile} onEntityClick={handleEntityClick} onChapterClick={handleChapterClick} />
              )}
              {profile.type === "location" && (
                <LocationCard profile={profile} onEntityClick={handleEntityClick} onChapterClick={handleChapterClick} />
              )}
              {profile.type === "item" && (
                <ItemCard profile={profile} onEntityClick={handleEntityClick} onChapterClick={handleChapterClick} />
              )}
              {profile.type === "org" && (
                <OrgCard profile={profile} onEntityClick={handleEntityClick} onChapterClick={handleChapterClick} />
              )}

              {/* Demo CTA */}
              <div className="my-4 rounded-lg border border-blue-200 bg-blue-50 p-3 text-center">
                <p className="text-sm text-blue-700">
                  下载完整版体验场景索引、阅读页跳转等更多功能
                </p>
              </div>

              {/* Cross-page navigation — points to demo routes */}
              <div className="flex flex-wrap gap-2 border-t py-3">
                {profile.type === "person" && (
                  <>
                    <button className="rounded border px-2 py-1 text-xs text-gray-600 hover:bg-gray-50" onClick={() => { close(); navigate(`/demo/${novelSlug}/graph`) }}>
                      关系图
                    </button>
                    <button className="rounded border px-2 py-1 text-xs text-gray-600 hover:bg-gray-50" onClick={() => { close(); navigate(`/demo/${novelSlug}/timeline`) }}>
                      时间线
                    </button>
                    <button className="rounded border px-2 py-1 text-xs text-gray-600 hover:bg-gray-50" onClick={() => { close(); navigate(`/demo/${novelSlug}/encyclopedia`) }}>
                      百科
                    </button>
                  </>
                )}
                {profile.type === "location" && (
                  <>
                    <button className="rounded border px-2 py-1 text-xs text-gray-600 hover:bg-gray-50" onClick={() => { close(); navigate(`/demo/${novelSlug}/map`) }}>
                      地图
                    </button>
                    <button className="rounded border px-2 py-1 text-xs text-gray-600 hover:bg-gray-50" onClick={() => { close(); navigate(`/demo/${novelSlug}/encyclopedia`) }}>
                      百科
                    </button>
                  </>
                )}
                {(profile.type === "item" || profile.type === "org") && (
                  <button className="rounded border px-2 py-1 text-xs text-gray-600 hover:bg-gray-50" onClick={() => { close(); navigate(`/demo/${novelSlug}/encyclopedia`) }}>
                    百科
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Concept Popup */}
      {conceptPopup && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center" onClick={closeConceptPopup}>
          <div className="w-80 rounded-lg border bg-white p-4 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 flex items-center justify-between">
              <h4 className="font-bold">{conceptPopup.name}</h4>
              <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-400">
                {conceptPopup.category}
              </span>
            </div>
            <p className="mb-3 text-sm">{conceptPopup.definition}</p>
            {conceptPopup.related.length > 0 && (
              <div className="text-xs text-gray-400">
                <span>相关：</span>{conceptPopup.related.join("、")}
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}
