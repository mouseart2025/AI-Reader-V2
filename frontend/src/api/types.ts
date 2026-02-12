export interface Novel {
  id: string
  title: string
  author: string | null
  total_chapters: number
  total_words: number
  created_at: string
  updated_at: string
  analysis_progress: number
  reading_progress: number
  last_opened: string | null
}

export interface Chapter {
  id: number
  novel_id: string
  chapter_num: number
  volume_num: number | null
  volume_title: string | null
  title: string
  word_count: number
  analysis_status: string
  analyzed_at: string | null
}

export interface ChapterContent extends Chapter {
  content: string
}

export interface ChapterEntity {
  name: string
  type: "person" | "location" | "item" | "org" | "concept"
}

export interface UserState {
  novel_id: string
  last_chapter: number | null
  scroll_position: number
  chapter_range?: string | null
  updated_at?: string
}

export interface EntitySummary {
  name: string
  type: string
  chapter_count: number
  first_chapter: number
}

export interface NovelsListResponse {
  novels: Novel[]
}

export interface ChapterPreview {
  chapter_num: number
  title: string
  word_count: number
}

export interface UploadPreviewResponse {
  title: string
  author: string | null
  file_hash: string
  total_chapters: number
  total_words: number
  chapters: ChapterPreview[]
  warnings: string[]
  duplicate_novel_id: string | null
}

export interface ConfirmImportRequest {
  file_hash: string
  title: string
  author?: string | null
}

export interface ReSplitRequest {
  file_hash: string
  mode?: string | null
  custom_regex?: string | null
}

export interface SplitModesResponse {
  modes: string[]
}

export interface HealthResponse {
  status: string
}

export interface EnvironmentCheck {
  ollama_running: boolean
  ollama_url: string
  required_model: string
  model_available: boolean
  available_models: string[]
}

// ── Analysis ──────────────────────────────────

export interface AnalyzeRequest {
  chapter_start?: number | null
  chapter_end?: number | null
  force?: boolean
}

export interface AnalysisTask {
  id: string
  novel_id: string
  status: "pending" | "running" | "paused" | "completed" | "cancelled"
  chapter_start: number
  chapter_end: number
  current_chapter: number
  created_at: string
  updated_at: string
}

export interface AnalysisStats {
  entities: number
  relations: number
  events: number
}

export interface WsProgress {
  type: "progress"
  chapter: number
  total: number
  done: number
  stats: AnalysisStats
}

export interface WsChapterDone {
  type: "chapter_done"
  chapter: number
  status: "completed" | "failed"
  error?: string
}

export interface WsTaskStatus {
  type: "task_status"
  status: string
  stats?: AnalysisStats
}

export interface WsProcessing {
  type: "processing"
  chapter: number
  total: number
}

export type AnalysisWsMessage = WsProgress | WsProcessing | WsChapterDone | WsTaskStatus

// ── Entity Profiles ──────────────────────────────

export type EntityType = "person" | "location" | "item" | "org" | "concept"

export interface PersonProfile {
  name: string
  type: "person"
  aliases: { name: string; first_chapter: number }[]
  appearances: { chapter: number; description: string }[]
  abilities: { chapter: number; dimension: string; name: string; description: string }[]
  relations: {
    other_person: string
    stages: { chapter: number; relation_type: string; evidence: string }[]
  }[]
  items: { chapter: number; item_name: string; item_type: string; action: string; description: string }[]
  experiences: { chapter: number; summary: string; type: string; location: string | null }[]
  stats: Record<string, number>
}

export interface LocationProfile {
  name: string
  type: "location"
  location_type: string
  parent: string | null
  children: string[]
  descriptions: { chapter: number; description: string }[]
  visitors: { name: string; chapters: number[]; is_resident: boolean }[]
  events: { chapter: number; summary: string; type: string }[]
  stats: Record<string, number>
}

export interface ItemProfile {
  name: string
  type: "item"
  item_type: string
  flow: { chapter: number; action: string; actor: string; recipient: string | null; description: string }[]
  related_items: string[]
  stats: Record<string, number>
}

export interface OrgProfile {
  name: string
  type: "org"
  org_type: string
  member_events: { chapter: number; member: string; role: string | null; action: string; description: string }[]
  org_relations: { chapter: number; other_org: string; relation_type: string }[]
  stats: Record<string, number>
}

export type EntityProfile = PersonProfile | LocationProfile | ItemProfile | OrgProfile

// ── Chat ──────────────────────────────────────

export interface Conversation {
  id: string
  novel_id: string
  title: string
  created_at: string
  updated_at: string
  message_count?: number
}

export interface ChatMessage {
  id: number
  conversation_id: string
  role: "user" | "assistant"
  content: string
  sources: number[]
  created_at: string
}

export interface ChatWsOutgoing {
  novel_id: string
  question: string
  conversation_id: string | null
}

export type ChatWsIncoming =
  | { type: "token"; content: string }
  | { type: "sources"; chapters: number[] }
  | { type: "done" }
  | { type: "error"; message: string }

// ── Export / Import ──────────────────────────────

export interface ImportPreview {
  title: string
  author: string | null
  total_chapters: number
  total_words: number
  analyzed_chapters: number
  facts_count: number
  has_user_state: boolean
  data_size_bytes: number
  existing_novel_id: string | null
}
