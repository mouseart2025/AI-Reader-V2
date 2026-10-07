import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { X, Sparkles } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { Novel } from "@/api/types"
import { novelPath } from "@/lib/novelPaths"

const STORAGE_KEY = "ai-reader-ftue-dismissed"

export function WelcomeBanner({ sampleNovels }: { sampleNovels: Novel[] }) {
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
    <div className="relative mb-6 overflow-hidden rounded-xl border border-blue-500/20 bg-gradient-to-r from-blue-600/15 via-card to-teal-500/10 p-6">
      <button
        onClick={handleDismiss}
        className="absolute top-3 right-3 rounded-full p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
        aria-label="关闭"
      >
        <X className="h-4 w-4" />
      </button>

      <div className="flex items-start gap-4">
        <div className="hidden shrink-0 rounded-full bg-blue-500/15 p-3 text-blue-400 sm:block">
          <Sparkles className="h-6 w-6" />
        </div>
        <div className="flex-1">
          <h2 className="text-lg font-bold text-foreground">欢迎使用 AI Reader！</h2>
          <p className="mt-1 text-sm text-muted-foreground leading-relaxed">
            这{sampleNovels.length === 1 ? "本名著" : `${sampleNovels.length}本名著`}已预先分析完成，点击即可探索人物关系图、世界地图、时间线等 7 种可视化
          </p>
          <div className="mt-4 flex items-center gap-3">
            <Button
              onClick={handleExplore}
              className="bg-blue-600 text-white hover:bg-blue-500"
              size="sm"
            >
              开始探索
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
