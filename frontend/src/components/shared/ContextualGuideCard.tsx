import { useState } from "react"
import { Upload, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { hasVisitedTabs } from "@/lib/tabTracking"

const DISMISSED_KEY = "ai-reader-guide-card-dismissed"

interface ContextualGuideCardProps {
  onUpload: () => void
}

export function ContextualGuideCard({ onUpload }: ContextualGuideCardProps) {
  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem(DISMISSED_KEY) === "1"
  )

  if (dismissed || !hasVisitedTabs(2)) return null

  const handleDismiss = () => {
    localStorage.setItem(DISMISSED_KEY, "1")
    setDismissed(true)
  }

  return (
    <Card className="mt-6 border-dashed">
      <CardContent className="relative flex items-center gap-4 py-4">
        <button
          onClick={handleDismiss}
          className="absolute top-2 right-2 rounded-full p-1 text-muted-foreground hover:bg-muted hover:text-foreground transition"
          aria-label="关闭"
        >
          <X className="h-3.5 w-3.5" />
        </button>

        <div className="flex-1">
          <p className="text-sm font-medium">想分析自己的小说？</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            上传 TXT 文件，AI 将自动提取人物、关系、地点等信息，生成和这里一样的可视化分析
          </p>
        </div>

        <Button size="sm" onClick={onUpload}>
          <Upload className="mr-1.5 h-3.5 w-3.5" />
          上传小说
        </Button>
      </CardContent>
    </Card>
  )
}
