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
  error: string | null
  breadcrumbs: BreadcrumbEntry[]
  conceptPopup: { name: string; definition: string; category: string; related: string[] } | null
  profileCache: Map<string, EntityProfile>

  openCard: (name: string, type: EntityType) => void
  setProfile: (profile: EntityProfile) => void
  setLoading: (loading: boolean) => void
  setError: (error: string | null) => void
  navigateTo: (name: string, type: EntityType) => void
  goBack: (index: number) => void
  close: () => void
  openConceptPopup: (data: { name: string; definition: string; category: string; related: string[] }) => void
  closeConceptPopup: () => void
  getCachedProfile: (type: EntityType, name: string) => EntityProfile | undefined
  setCachedProfile: (type: EntityType, name: string, profile: EntityProfile) => void
}

const MAX_BREADCRUMBS = 10
const MAX_PROFILE_CACHE = 50

export const useEntityCardStore = create<EntityCardState>((set, get) => ({
  open: false,
  loading: false,
  profile: null,
  error: null,
  breadcrumbs: [],
  conceptPopup: null,
  profileCache: new Map(),

  openCard: (name, type) =>
    set({
      open: true,
      loading: true,
      profile: null,
      error: null,
      breadcrumbs: [{ name, type }],
      conceptPopup: null,
    }),

  setProfile: (profile) => set({ profile, loading: false, error: null }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error, loading: false }),

  navigateTo: (name, type) =>
    set((s) => {
      const crumbs = [...s.breadcrumbs, { name, type }]
      if (crumbs.length > MAX_BREADCRUMBS) crumbs.shift()
      return { breadcrumbs: crumbs, loading: true, profile: null, error: null }
    }),

  goBack: (index) =>
    set((s) => ({
      breadcrumbs: s.breadcrumbs.slice(0, index + 1),
      loading: true,
      profile: null,
      error: null,
    })),

  close: () =>
    set({ open: false, profile: null, breadcrumbs: [], loading: false, error: null }),

  openConceptPopup: (data) => set({ conceptPopup: data }),
  closeConceptPopup: () => set({ conceptPopup: null }),

  getCachedProfile: (type, name) => get().profileCache.get(`${type}:${name}`),

  setCachedProfile: (type, name, profile) => {
    const cache = get().profileCache
    const key = `${type}:${name}`
    if (cache.size >= MAX_PROFILE_CACHE && !cache.has(key)) {
      // FIFO eviction
      const firstKey = cache.keys().next().value
      if (firstKey) cache.delete(firstKey)
    }
    cache.set(key, profile)
  },
}))
