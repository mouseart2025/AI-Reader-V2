/**
 * Timeline state store — persists filter/view state across page navigations.
 *
 * When user navigates from Timeline → Reading → back to Timeline,
 * the filters, view mode, and scroll position are preserved.
 */
import { create } from "zustand"

type FilterType = "战斗" | "成长" | "社交" | "旅行" | "角色登场" | "物品交接" | "组织变动" | "关系变化" | "其他"

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
  "战斗", "成长", "社交", "旅行", "组织变动", "关系变化", "其他",
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
