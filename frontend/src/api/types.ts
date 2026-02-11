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
