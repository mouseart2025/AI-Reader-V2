/**
 * 导出落盘：桌面端让用户选保存位置，Web 端沿用浏览器下载。
 *
 * 背景（issue #50）：桌面端原先用 `<a download>`，文件只会进系统默认下载目录，
 * 用户不知道存到哪、也不能选。这里在 Tauri 环境下改用原生保存对话框 + Rust 侧
 * 直接取流写盘（`save_export` 命令），Web 端行为保持不变。
 */

import { isTauri, getSidecarToken } from "@/api/sidecarBridge"

export interface SaveExportOptions {
  /** 导出接口地址（sidecar / 同源均可） */
  url: string
  /** 默认文件名（保存对话框里预填） */
  filename: string
  /** 文件类型过滤器，例如 [{ name: "AIR 数据包", extensions: ["air"] }] */
  filters?: { name: string; extensions: string[] }[]
  /** 额外的请求头（Web 端直接 fetch 时使用） */
  headers?: Record<string, string>
}

export interface SaveExportResult {
  /** 用户取消了保存对话框时为 false */
  saved: boolean
  /** 实际保存路径（仅桌面端能拿到） */
  path?: string
}

/** Web 端：沿用浏览器下载（无路径选择能力）。 */
function downloadInBrowser(url: string, filename: string, headers?: Record<string, string>): void {
  const a = document.createElement("a")
  if (headers && Object.keys(headers).length > 0) {
    // 需要鉴权的接口先取 blob 再触发下载
    fetch(url, { headers })
      .then((resp) => {
        if (!resp.ok) throw new Error(`导出失败: ${resp.status}`)
        return resp.blob()
      })
      .then((blob) => {
        const objectUrl = URL.createObjectURL(blob)
        a.href = objectUrl
        a.download = filename
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(objectUrl)
      })
      .catch(() => {
        /* 失败由调用方的错误处理兜底 */
      })
    return
  }
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

/**
 * 保存导出结果。桌面端返回用户选择的路径；用户取消时 saved=false。
 */
export async function saveExportFile(opts: SaveExportOptions): Promise<SaveExportResult> {
  const { url, filename, filters, headers } = opts

  if (!isTauri) {
    downloadInBrowser(url, filename, headers)
    return { saved: true }
  }

  const { save } = await import("@tauri-apps/plugin-dialog")
  const path = await save({ defaultPath: filename, filters })
  if (!path) return { saved: false }

  const { invoke } = await import("@tauri-apps/api/core")
  const savedPath = await invoke<string>("save_export", {
    url,
    path,
    token: getSidecarToken(),
  })
  return { saved: true, path: savedPath }
}
