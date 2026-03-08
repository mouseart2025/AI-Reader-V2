import { isTauri } from "@/api/sidecarBridge"

/**
 * 生成平台感知的小说子页面路径
 * Tauri: /novel/{novelId}/{tab}
 * Web:   /{tab}/{novelId}
 */
export function novelPath(novelId: string, tab: string): string {
  return isTauri ? `/novel/${novelId}/${tab}` : `/${tab}/${novelId}`
}
