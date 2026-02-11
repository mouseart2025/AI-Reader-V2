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

export interface HealthResponse {
  status: string
}
