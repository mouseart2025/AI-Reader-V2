import { create } from "zustand"
import { checkEnvironment } from "@/api/client"
import { translate } from "@/i18n"

interface LlmInfoState {
  model: string | null
  provider: string | null // "ollama" | "openai"
  loading: boolean
  /** Fetch LLM info from backend health-check. Cached — only fetches once per session unless force=true. */
  fetch: (force?: boolean) => Promise<void>
}

export const useLlmInfoStore = create<LlmInfoState>((set, get) => ({
  model: null,
  provider: null,
  loading: false,

  fetch: async (force = false) => {
    const s = get()
    if (!force && s.model) return
    if (s.loading) return
    set({ loading: true })
    try {
      const env = await checkEnvironment()
      set({
        model: env.llm_model || null,
        provider: env.llm_provider || null,
        loading: false,
      })
    } catch {
      set({ loading: false })
    }
  },
}))

/** Format model label for display, localized by current UI language. */
export function formatLlmLabel(
  model: string | null | undefined,
  provider: string | null | undefined,
): string {
  if (!model) return ""
  const suffix = provider === "openai"
    ? translate("analysis.mode.cloud")
    : translate("analysis.mode.local")
  return `${model}${suffix}`
}
