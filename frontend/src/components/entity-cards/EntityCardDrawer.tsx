import { useCallback, useEffect } from "react"
import { fetchEntityProfile } from "@/api/client"
import type { EntityProfile, EntityType } from "@/api/types"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { useReadingStore } from "@/stores/readingStore"
import { Button } from "@/components/ui/button"
import { PersonCard } from "./PersonCard"
import { LocationCard } from "./LocationCard"
import { ItemCard } from "./ItemCard"
import { OrgCard } from "./OrgCard"

interface EntityCardDrawerProps {
  novelId: string
}

export function EntityCardDrawer({ novelId }: EntityCardDrawerProps) {
  const {
    open,
    loading,
    profile,
    breadcrumbs,
    conceptPopup,
    setProfile,
    setLoading,
    navigateTo,
    goBack,
    close,
    closeConceptPopup,
  } = useEntityCardStore()

  const aliasMap = useReadingStore((s) => s.aliasMap)
  const currentCrumb = breadcrumbs[breadcrumbs.length - 1]

  // Fetch profile when breadcrumbs change (resolve alias to canonical name)
  useEffect(() => {
    if (!open || !currentCrumb) return
    let cancelled = false

    async function load() {
      try {
        const resolvedName = aliasMap[currentCrumb.name] ?? currentCrumb.name
        const data = await fetchEntityProfile(
          novelId,
          resolvedName,
          currentCrumb.type,
        )
        if (!cancelled) {
          setProfile(data as unknown as EntityProfile)
        }
      } catch {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [novelId, open, currentCrumb?.name, currentCrumb?.type, aliasMap, setProfile, setLoading])

  const handleEntityClick = useCallback(
    (name: string, type: string) => {
      if (type === "concept") {
        // Concepts don't have full profiles, skip for now
        return
      }
      navigateTo(name, type as EntityType)
    },
    [navigateTo],
  )

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
      <div
        className="fixed inset-0 z-40 bg-black/20"
        onClick={close}
      />

      {/* Drawer */}
      <div className="fixed top-0 right-0 z-50 flex h-screen w-[420px] flex-col border-l bg-background shadow-lg">
        {/* Header with breadcrumbs */}
        <div className="flex items-center gap-2 border-b px-4 py-3">
          <div className="flex-1 overflow-hidden">
            <div className="flex items-center gap-1 text-sm">
              {breadcrumbs.map((crumb, i) => (
                <span key={i} className="flex items-center gap-1">
                  {i > 0 && <span className="text-muted-foreground">&gt;</span>}
                  {i < breadcrumbs.length - 1 ? (
                    <button
                      className="text-primary truncate hover:underline"
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
          <Button variant="ghost" size="icon-xs" onClick={close}>
            <XIcon className="size-4" />
          </Button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-4">
          {loading && (
            <div className="flex h-32 items-center justify-center">
              <p className="text-muted-foreground text-sm">Loading...</p>
            </div>
          )}

          {!loading && profile && (
            <>
              {profile.type === "person" && (
                <PersonCard profile={profile} onEntityClick={handleEntityClick} />
              )}
              {profile.type === "location" && (
                <LocationCard profile={profile} onEntityClick={handleEntityClick} />
              )}
              {profile.type === "item" && (
                <ItemCard profile={profile} onEntityClick={handleEntityClick} />
              )}
              {profile.type === "org" && (
                <OrgCard profile={profile} onEntityClick={handleEntityClick} />
              )}
            </>
          )}
        </div>
      </div>

      {/* Concept Popup */}
      {conceptPopup && (
        <ConceptPopup
          data={conceptPopup}
          onClose={closeConceptPopup}
        />
      )}
    </>
  )
}

function ConceptPopup({
  data,
  onClose,
}: {
  data: { name: string; definition: string; category: string; related: string[] }
  onClose: () => void
}) {
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center" onClick={onClose}>
      <div
        className="w-80 rounded-lg border bg-background p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-2 flex items-center justify-between">
          <h4 className="font-bold">{data.name}</h4>
          <span className="text-muted-foreground rounded bg-muted px-1.5 py-0.5 text-[10px]">
            {data.category}
          </span>
        </div>
        <p className="mb-3 text-sm">{data.definition}</p>
        {data.related.length > 0 && (
          <div className="text-muted-foreground text-xs">
            <span>相关：</span>
            {data.related.join("、")}
          </div>
        )}
      </div>
    </div>
  )
}

function XIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  )
}
