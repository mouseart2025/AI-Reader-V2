import { create } from "zustand"
import type { EntityProfile, EntityType } from "@/api/types"

interface BreadcrumbEntry {
  name: string
  type: EntityType
}

interface EntityCardState {
  open: boolean
  loading: boolean
  profile: EntityProfile | null
  breadcrumbs: BreadcrumbEntry[]
  conceptPopup: { name: string; definition: string; category: string; related: string[] } | null

  openCard: (name: string, type: EntityType) => void
  setProfile: (profile: EntityProfile) => void
  setLoading: (loading: boolean) => void
  navigateTo: (name: string, type: EntityType) => void
  goBack: (index: number) => void
  close: () => void
  openConceptPopup: (data: { name: string; definition: string; category: string; related: string[] }) => void
  closeConceptPopup: () => void
}

const MAX_BREADCRUMBS = 10

export const useEntityCardStore = create<EntityCardState>((set) => ({
  open: false,
  loading: false,
  profile: null,
  breadcrumbs: [],
  conceptPopup: null,

  openCard: (name, type) =>
    set({
      open: true,
      loading: true,
      profile: null,
      breadcrumbs: [{ name, type }],
      conceptPopup: null,
    }),

  setProfile: (profile) => set({ profile, loading: false }),
  setLoading: (loading) => set({ loading }),

  navigateTo: (name, type) =>
    set((s) => {
      const crumbs = [...s.breadcrumbs, { name, type }]
      if (crumbs.length > MAX_BREADCRUMBS) crumbs.shift()
      return { breadcrumbs: crumbs, loading: true, profile: null }
    }),

  goBack: (index) =>
    set((s) => ({
      breadcrumbs: s.breadcrumbs.slice(0, index + 1),
      loading: true,
      profile: null,
    })),

  close: () =>
    set({ open: false, profile: null, breadcrumbs: [], loading: false }),

  openConceptPopup: (data) => set({ conceptPopup: data }),
  closeConceptPopup: () => set({ conceptPopup: null }),
}))
