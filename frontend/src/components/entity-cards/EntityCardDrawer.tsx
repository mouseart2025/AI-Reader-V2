import { useCallback, useEffect } from "react"
import { useNavigate } from "react-router-dom"
import { fetchEntityProfile } from "@/api/client"
import type { EntityProfile, EntityType } from "@/api/types"
import { useEntityCardStore } from "@/stores/entityCardStore"
import { useReadingStore } from "@/stores/readingStore"
import { novelPath } from "@/lib/novelPaths"
import { Button } from "@/components/ui/button"
import { useI18n } from "@/i18n"
import { PersonCard } from "./PersonCard"
import { LocationCard } from "./LocationCard"
import { ItemCard } from "./ItemCard"
import { OrgCard } from "./OrgCard"

interface EntityCardDrawerProps {
  novelId: string
}

export function EntityCardDrawer({ novelId }: EntityCardDrawerProps) {
  const { t } = useI18n()
  const {
    open,
    loading,
    profile,
    error: cardError,
    breadcrumbs,
    conceptPopup,
    setProfile,
    setLoading,
    setError,
    navigateTo,
    goBack,
    close,
    closeConceptPopup,
    getCachedProfile,
    setCachedProfile,
  } = useEntityCardStore()

  const navigate = useNavigate()
  const aliasMap = useReadingStore((s) => s.aliasMap)
  const currentCrumb = breadcrumbs[breadcrumbs.length - 1]

  // Fetch profile when breadcrumbs change (resolve alias to canonical name)
  useEffect(() => {
    if (!open || !currentCrumb) return
    let cancelled = false

    const resolvedName = aliasMap[currentCrumb.name] ?? currentCrumb.name
    // Check cache first
    const cached = getCachedProfile(currentCrumb.type, resolvedName)
    if (cached) {
      setProfile(cached)
      return
    }

    async function load() {
      try {
        const data = await fetchEntityProfile(
          novelId,
          resolvedName,
          currentCrumb.type,
        )
        if (!cancelled) {
          const p = data as unknown as EntityProfile
          setProfile(p)
          setCachedProfile(currentCrumb.type, resolvedName, p)
        }
      } catch {
        if (!cancelled) setError(t("entity.loadFailedRetry"))
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [novelId, open, currentCrumb?.name, currentCrumb?.type, aliasMap, setProfile, setLoading, setError, getCachedProfile, setCachedProfile, t])

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

  const handleChapterClick = useCallback(
    (ch: number) => {
      close()
      navigate(`${novelPath(novelId, "read")}?chapter=${ch}`)
    },
    [close, navigate, novelId],
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
      <div className="fixed top-0 right-0 z-50 flex h-screen w-full flex-col border-l bg-background shadow-lg sm:w-[420px]">
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
              <p className="text-muted-foreground text-sm">{t("common.loading")}</p>
            </div>
          )}

          {!loading && cardError && (
            <div className="flex h-32 flex-col items-center justify-center gap-2">
              <p className="text-sm text-destructive">{cardError}</p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setError(null)
                  setLoading(true)
                }}
              >
                {t("common.retry")}
              </Button>
            </div>
          )}

          {!loading && !cardError && profile && (
            <>
              {profile.type === "person" && (
                <PersonCard profile={profile} onEntityClick={handleEntityClick} onChapterClick={handleChapterClick} novelId={novelId} />
              )}
              {profile.type === "location" && (
                <LocationCard profile={profile} onEntityClick={handleEntityClick} onChapterClick={handleChapterClick} novelId={novelId} />
              )}
              {profile.type === "item" && (
                <ItemCard profile={profile} onEntityClick={handleEntityClick} onChapterClick={handleChapterClick} novelId={novelId} />
              )}
              {profile.type === "org" && (
                <OrgCard profile={profile} onEntityClick={handleEntityClick} onChapterClick={handleChapterClick} novelId={novelId} />
              )}

              {/* Cross-page navigation */}
              <div className="border-t py-3 flex flex-wrap gap-2">
                {profile.type === "location" && (
                  <>
                    <Button variant="outline" size="xs" onClick={() => { close(); navigate(novelPath(novelId, "map")) }}>
                      {t("nav.map")}
                    </Button>
                    <Button variant="outline" size="xs" onClick={() => { close(); navigate(novelPath(novelId, "encyclopedia")) }}>
                      {t("nav.encyclopedia")}
                    </Button>
                  </>
                )}
                {profile.type === "person" && (
                  <>
                    <Button variant="outline" size="xs" onClick={() => { close(); navigate(novelPath(novelId, "timeline")) }}>
                      {t("nav.timeline")}
                    </Button>
                    <Button variant="outline" size="xs" onClick={() => { close(); navigate(novelPath(novelId, "graph")) }}>
                      {t("nav.relationGraph")}
                    </Button>
                    <Button variant="outline" size="xs" onClick={() => { close(); navigate(novelPath(novelId, "encyclopedia")) }}>
                      {t("nav.encyclopedia")}
                    </Button>
                  </>
                )}
                {profile.type === "org" && (
                  <>
                    <Button variant="outline" size="xs" onClick={() => { close(); navigate(novelPath(novelId, "factions")) }}>
                      {t("nav.factions")}
                    </Button>
                    <Button variant="outline" size="xs" onClick={() => { close(); navigate(novelPath(novelId, "encyclopedia")) }}>
                      {t("nav.encyclopedia")}
                    </Button>
                  </>
                )}
                {profile.type === "item" && (
                  <Button variant="outline" size="xs" onClick={() => { close(); navigate(novelPath(novelId, "encyclopedia")) }}>
                    {t("nav.encyclopedia")}
                  </Button>
                )}
              </div>
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
  const { t } = useI18n()

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
            <span>{t("entity.related")}</span>
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
