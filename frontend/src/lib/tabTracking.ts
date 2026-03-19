const TABS_VISITED_KEY = "ai-reader-tabs-visited"

/** Track that a visualization tab has been visited */
export function recordTabVisit(tabName: string) {
  try {
    const raw = localStorage.getItem(TABS_VISITED_KEY)
    const existing: string[] = raw ? JSON.parse(raw) : []
    const updated = Array.from(new Set([...existing, tabName]))
    localStorage.setItem(TABS_VISITED_KEY, JSON.stringify(updated))
  } catch {
    // ignore
  }
}

/** Check if user has visited >= N distinct tabs */
export function hasVisitedTabs(minCount: number): boolean {
  try {
    const raw = localStorage.getItem(TABS_VISITED_KEY)
    if (!raw) return false
    const tabs: string[] = JSON.parse(raw)
    return tabs.length >= minCount
  } catch {
    return false
  }
}
