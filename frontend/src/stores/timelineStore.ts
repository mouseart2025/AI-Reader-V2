/**
 * Timeline state store — persists filter/view state across page navigations.
 *
 * When user navigates from Timeline → Reading → back to Timeline,
 * the filters, view mode, and scroll position are preserved.
 */
import { create } from "zustand"

export type FilterType =
  | "battle"
  | "growth"
  | "social"
  | "travel"
  | "character_appearance"
  | "item_transfer"
  | "org_change"
  | "relation_change"
  | "other"

interface TimelineState {
  filterTypes: Set<FilterType>
  filterImportance: "all" | "high" | "medium"
  viewMode: "list" | "storyline"
  autoCollapseLow: boolean
  minSwimlaneEvents: number
  scrollTop: number

  setFilterTypes: (types: Set<FilterType>) => void
  setFilterImportance: (v: "all" | "high" | "medium") => void
  setViewMode: (v: "list" | "storyline") => void
  setAutoCollapseLow: (v: boolean) => void
  setMinSwimlaneEvents: (v: number) => void
  setScrollTop: (v: number) => void
}

const DEFAULT_FILTERS = new Set<FilterType>([
  "battle", "growth", "social", "travel", "org_change", "relation_change", "other",
])

export const useTimelineStore = create<TimelineState>((set) => ({
  filterTypes: new Set(DEFAULT_FILTERS),
  filterImportance: "all",
  viewMode: "list",
  autoCollapseLow: true,
  minSwimlaneEvents: 5,
  scrollTop: 0,

  setFilterTypes: (types) => set({ filterTypes: types }),
  setFilterImportance: (v) => set({ filterImportance: v }),
  setViewMode: (v) => set({ viewMode: v }),
  setAutoCollapseLow: (v) => set({ autoCollapseLow: v }),
  setMinSwimlaneEvents: (v) => set({ minSwimlaneEvents: v }),
  setScrollTop: (v) => set({ scrollTop: v }),
}))
