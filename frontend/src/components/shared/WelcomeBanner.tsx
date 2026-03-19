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
    <div className="relative mb-6 overflow-hidden rounded-xl bg-gradient-to-r from-blue-600 to-teal-500 p-6 text-white shadow-lg">
      <button
        onClick={handleDismiss}
        className="absolute top-3 right-3 rounded-full p-1 text-white/70 transition hover:bg-white/20 hover:text-white"
        aria-label="关闭"
      >
        <X className="h-4 w-4" />
      </button>

      <div className="flex items-start gap-4">
        <div className="hidden shrink-0 rounded-full bg-white/20 p-3 sm:block">
          <Sparkles className="h-6 w-6" />
        </div>
        <div className="flex-1">
          <h2 className="text-lg font-bold">欢迎使用 AI Reader！</h2>
          <p className="mt-1 text-sm text-white/85 leading-relaxed">
            这{sampleNovels.length === 1 ? "本名著" : `${sampleNovels.length}本名著`}已预先分析完成，点击即可探索人物关系图、世界地图、时间线等 7 种可视化
          </p>
          <div className="mt-4 flex items-center gap-3">
            <Button
              onClick={handleExplore}
              className="bg-white text-blue-700 hover:bg-white/90"
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
