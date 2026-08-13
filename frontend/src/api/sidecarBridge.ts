/**
 * Sidecar bridge — Tauri 环境下启动/管理 Python 后端 sidecar 进程
 * Web 环境下所有函数均为 no-op
 */

/** 是否在 Tauri 桌面环境中运行 */
export const isTauri =
  typeof window !== "undefined" &&
  ("__TAURI__" in window || "__TAURI_INTERNALS__" in window)

let _sidecarPort: number | null = null
let _sidecarToken: string | null = null
let _startingPromise: Promise<number> | null = null

interface SidecarInfo {
  port: number
  token: string
}

/**
 * 确保 sidecar 已启动，返回端口号。
 * 使用 Promise 缓存防止并发调用启动多个 sidecar 实例。
 */
export async function ensureSidecar(): Promise<number> {
  if (_sidecarPort) return _sidecarPort
  if (_startingPromise) return _startingPromise

  _startingPromise = (async () => {
    const { invoke } = await import("@tauri-apps/api/core")

    // 检查是否已启动
    const existing = await invoke<SidecarInfo | null>("sidecar_status")
    if (existing) {
      _sidecarPort = existing.port
      _sidecarToken = existing.token
      return existing.port
    }

    // 启动 sidecar
    const info = await invoke<SidecarInfo>("sidecar_start")
    _sidecarPort = info.port
    _sidecarToken = info.token
    return info.port
  })()

  try {
    return await _startingPromise
  } catch (e) {
    _startingPromise = null
    throw e
  }
}

/** 获取 sidecar HTTP base URL，如 "http://localhost:12345" */
export function getSidecarBaseUrl(): string {
  return _sidecarPort ? `http://localhost:${_sidecarPort}` : ""
}

/** 获取 sidecar WebSocket base URL，如 "ws://localhost:12345" */
export function getSidecarWsUrl(): string {
  return _sidecarPort ? `ws://localhost:${_sidecarPort}` : ""
}

/** WS 鉴权 query 串（V-01）："?token=xxx" 或 ""（web 直跑）。拼在 WS 路径之后 */
export function sidecarWsQuery(): string {
  return _sidecarToken ? `?token=${encodeURIComponent(_sidecarToken)}` : ""
}

/**
 * sidecar API 鉴权头（V-01）：仅桌面端有令牌；web 直跑返回空对象。
 * 用法：fetch(url, { headers: { ...sidecarAuthHeaders(), ... } })
 */
export function sidecarAuthHeaders(): Record<string, string> {
  return _sidecarToken ? { Authorization: `Bearer ${_sidecarToken}` } : {}
}
