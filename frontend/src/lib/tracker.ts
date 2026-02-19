/**
 * Anonymous local usage event tracker.
 * Events are stored in local SQLite only — never uploaded.
 * Respects the tracking_enabled setting.
 */

const TRACK_URL = "/api/usage/track"

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
  // Fire and forget — never block the UI
  fetch(TRACK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event_type: eventType, metadata: metadata ?? {} }),
  }).catch(() => {
    // Silently ignore tracking failures
  })
}
