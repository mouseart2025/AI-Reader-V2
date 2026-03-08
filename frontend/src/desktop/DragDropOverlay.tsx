/**
 * DragDropOverlay — 拖拽 .air 文件导入覆盖层
 * 监听 Tauri drag-drop 事件，提供视觉反馈
 * 仅在 isTauri 环境下使用
 */

import { useEffect, useState } from "react"
import { Upload } from "lucide-react"

interface DragDropOverlayProps {
  onFileDrop: (path: string) => void
}

export function DragDropOverlay({ onFileDrop }: DragDropOverlayProps) {
  const [dragOver, setDragOver] = useState(false)

  useEffect(() => {
    let unlisten1: (() => void) | undefined
    let unlisten2: (() => void) | undefined
    let unlisten3: (() => void) | undefined

    async function setup() {
      const { listen } = await import("@tauri-apps/api/event")

      unlisten1 = await listen("tauri://drag-enter", () => {
        setDragOver(true)
      })

      unlisten2 = await listen("tauri://drag-leave", () => {
        setDragOver(false)
      })

      unlisten3 = await listen<{ paths: string[] }>("tauri://drag-drop", (event) => {
        setDragOver(false)
        const paths = event.payload.paths
        const airFiles = paths.filter((p) => p.endsWith(".air"))
        if (airFiles.length > 0) {
          onFileDrop(airFiles[0])
        }
      })
    }

    setup().catch(() => {
      // Non-fatal: drag-drop won't work but app still functions
    })

    return () => {
      unlisten1?.()
      unlisten2?.()
      unlisten3?.()
    }
  }, [onFileDrop])

  if (!dragOver) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-blue-500/10 backdrop-blur-sm"
      onClick={() => setDragOver(false)}
    >
      <div className="flex flex-col items-center gap-4 rounded-2xl border-2 border-dashed border-blue-500 px-16 py-12">
        <Upload className="size-12 text-blue-400" />
        <p className="text-lg font-medium text-slate-200">释放以导入 .air 文件</p>
        <p className="text-sm text-slate-400">支持 AI Reader 分析数据</p>
      </div>
    </div>
  )
}
