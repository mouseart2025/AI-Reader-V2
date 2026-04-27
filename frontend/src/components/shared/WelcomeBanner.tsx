import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { X, Sparkles } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { Novel } from "@/api/types"
import { useI18n } from "@/i18n"
import { novelPath } from "@/lib/novelPaths"

const STORAGE_KEY = "ai-reader-ftue-dismissed"

export function WelcomeBanner({ sampleNovels }: { sampleNovels: Novel[] }) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem(STORAGE_KEY) === "1"
  )

  if (dismissed || sampleNovels.length === 0) return null

  const handleDismiss = () => {
    localStorage.setItem(STORAGE_KEY, "1")
    setDismissed(true)
  }

  const handleExplore = () => {
    navigate(novelPath(sampleNovels[0].id, "graph"))
  }

  return (
    <div className="relative mb-6 overflow-hidden rounded-xl bg-gradient-to-r from-blue-600 to-teal-500 p-6 text-white shadow-lg">
      <button
        onClick={handleDismiss}
        className="absolute top-3 right-3 rounded-full p-1 text-white/70 transition hover:bg-white/20 hover:text-white"
        aria-label={t("common.close")}
      >
        <X className="h-4 w-4" />
      </button>

      <div className="flex items-start gap-4">
        <div className="hidden shrink-0 rounded-full bg-white/20 p-3 sm:block">
          <Sparkles className="h-6 w-6" />
        </div>
        <div className="flex-1">
          <h2 className="text-lg font-bold">{t("shared.welcome.title")}</h2>
          <p className="mt-1 text-sm text-white/85 leading-relaxed">
            {t("shared.welcome.description", { count: sampleNovels.length })}
          </p>
          <div className="mt-4 flex items-center gap-3">
            <Button
              onClick={handleExplore}
              className="bg-white text-blue-700 hover:bg-white/90"
              size="sm"
            >
              {t("shared.welcome.startExploring")}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
