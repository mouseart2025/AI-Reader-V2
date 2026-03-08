/**
 * Sidecar bridge — Tauri 环境下启动/管理 Python 后端 sidecar 进程
 * Web 环境下所有函数均为 no-op
 */

/** 是否在 Tauri 桌面环境中运行 */
export const isTauri =
  typeof window !== "undefined" &&
  ("__TAURI__" in window || "__TAURI_INTERNALS__" in window)

let _sidecarPort: number | null = null
let _startingPromise: Promise<number> | null = null

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
    const existing = await invoke<number | null>("sidecar_status")
    if (existing) {
      _sidecarPort = existing
      return existing
    }

    // 启动 sidecar
    _sidecarPort = await invoke<number>("sidecar_start")
    return _sidecarPort
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
