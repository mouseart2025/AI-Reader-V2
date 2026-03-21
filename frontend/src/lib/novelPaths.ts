import { isTauri } from "@/api/sidecarBridge"

/**
 * 生成平台感知的小说子页面路径
 * Tauri: /novel/{novelId}/{tab}
 * Web:   /{tab}/{novelId}
 */
const TAB_ALIASES: Record<string, string> = { read: "reading" }

export function novelPath(novelId: string, tab: string, query?: string): string {
  const t = isTauri ? (TAB_ALIASES[tab] ?? tab) : tab
  const base = isTauri ? `/novel/${novelId}/${t}` : `/${t}/${novelId}`
  return query ? `${base}?${query}` : base
}
