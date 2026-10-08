/**
 * Anonymous local usage event tracker.
 * Events are stored in local SQLite only — never uploaded.
 * Respects the tracking_enabled setting.
 */

import { getSidecarBaseUrl, isTauri, sidecarAuthHeaders } from "@/api/sidecarBridge"

// Same reason as useAuthedAsset: in the desktop app the page origin is not
// the sidecar, so a bare "/api/..." dies against the dev-server proxy / the
// tauri:// protocol instead of reaching the backend.
function trackUrl(): string | null {
  if (!isTauri) return "/api/usage/track"
  const base = getSidecarBaseUrl()
  return base ? `${base}/api/usage/track` : null
}

export type EventType =
  | "novel_upload"
  | "novel_delete"
  | "analysis_start"
  | "analysis_complete"
  | "export_series_bible"
  | "export_data"
  | "view_entity_card"
  | "view_graph"
  | "view_map"
  | "view_timeline"
  | "view_factions"
  | "view_encyclopedia"
  | "view_conflicts"
  | "view_screenplay"
  | "chat_question"
  | "prescan_start"

export function trackEvent(eventType: EventType, metadata?: Record<string, unknown>): void {
  const url = trackUrl()
  if (!url) return // sidecar not up yet — fire-and-forget must stay lossy, not noisy
  // Fire and forget — never block the UI
  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...sidecarAuthHeaders() },
    body: JSON.stringify({ event_type: eventType, metadata: metadata ?? {} }),
  }).catch(() => {
    // Silently ignore tracking failures
  })
}
