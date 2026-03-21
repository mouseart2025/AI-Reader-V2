/**
 * Cross-module focus state for Map ↔ Timeline ↔ Reading linking.
 *
 * Pattern: same as entityCardStore — globally accessible, action-based.
 * Any page can set focus; any page can consume and react.
 */
import { create } from "zustand"

interface VisualizationFocusState {
  /** Location to highlight/fly-to on Map */
  focusLocation: string | null
  /** Chapter to scroll-to on Timeline */
  focusChapter: number | null
  /** Source page that triggered the focus (prevents feedback loops) */
  source: "map" | "timeline" | "reading" | null

  setFocusLocation: (name: string | null, source?: "map" | "timeline" | "reading" | null) => void
  setFocusChapter: (chapter: number | null, source?: "map" | "timeline" | "reading" | null) => void
  clear: () => void
}

export const useVisualizationFocusStore = create<VisualizationFocusState>((set) => ({
  focusLocation: null,
  focusChapter: null,
  source: null,

  setFocusLocation: (name, source = null) =>
    set({ focusLocation: name, source }),

  setFocusChapter: (chapter, source = null) =>
    set({ focusChapter: chapter, source }),

  clear: () =>
    set({ focusLocation: null, focusChapter: null, source: null }),
}))
