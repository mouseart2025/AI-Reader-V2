export interface Novel {
  id: string
  title: string
  author: string
  total_chapters: number
  total_words: number
  created_at: string
  updated_at: string
}

export interface Chapter {
  id: number
  novel_id: string
  chapter_num: number
  title: string
  word_count: number
  analysis_status: string
}

export interface HealthResponse {
  status: string
}
