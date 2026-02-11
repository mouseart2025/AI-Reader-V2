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
  title: string
  word_count: number
  analysis_status: string
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

export type AnalysisWsMessage = WsProgress | WsChapterDone | WsTaskStatus
