import { create } from "zustand"
import { persist } from "zustand/middleware"

interface TourState {
  /** Current step index (0-3). -1 means tour completed or dismissed. */
  currentStep: number
  /** If true, user clicked "不再提示" — permanently hidden. */
  dismissed: boolean
  /** Advance to the next step. Completes tour after last step. */
  nextStep: () => void
  /** Permanently dismiss all tour bubbles. */
  dismiss: () => void
  /** Reset tour state (for debugging). */
  reset: () => void
}

export const TOUR_STEPS = [
  { message: "试试点击高亮的人物名称，查看 AI 自动生成的角色卡片" },
  { message: "点击查看人物关系网络，发现隐藏的角色关联" },
  { message: "探索小说中的地理世界，查看角色行动轨迹" },
  { message: "向 AI 提问关于小说的任何问题，获得基于原文的回答" },
] as const

export const TOTAL_TOUR_STEPS = TOUR_STEPS.length

export const useTourStore = create<TourState>()(
  persist(
    (set) => ({
      currentStep: 0,
      dismissed: false,
      nextStep: () =>
        set((s) => ({
          currentStep: s.currentStep + 1 >= TOTAL_TOUR_STEPS ? -1 : s.currentStep + 1,
        })),
      dismiss: () => set({ currentStep: -1, dismissed: true }),
      reset: () => set({ currentStep: 0, dismissed: false }),
    }),
    { name: "ai-reader-tour-state" },
  ),
)
