import type {
  AnalysisTask,
  AnalyzeRequest,
  Chapter,
  ChapterContent,
  ChapterEntity,
  ChatMessage,
  ConfirmImportRequest,
  Conversation,
  EntitySummary,
  EnvironmentCheck,
  ImportPreview,
  Novel,
  NovelsListResponse,
  ReSplitRequest,
  SplitModesResponse,
  UploadPreviewResponse,
  UserState,
} from "./types"

const BASE = "/api"

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  })
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

export function fetchNovels(): Promise<NovelsListResponse> {
  return apiFetch<NovelsListResponse>("/novels")
}

export function fetchNovel(novelId: string): Promise<Novel> {
  return apiFetch<Novel>(`/novels/${novelId}`)
}

export function deleteNovel(novelId: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/novels/${novelId}`, { method: "DELETE" })
}

export async function uploadNovel(file: File): Promise<UploadPreviewResponse> {
  const form = new FormData()
  form.append("file", file)
  const res = await fetch(`${BASE}/novels/upload`, {
    method: "POST",
    body: form,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail ?? `上传失败: ${res.status}`)
  }
  return res.json() as Promise<UploadPreviewResponse>
}

export function checkEnvironment(): Promise<EnvironmentCheck> {
  return apiFetch<EnvironmentCheck>("/settings/health-check")
}

export function confirmImport(req: ConfirmImportRequest): Promise<Novel> {
  return apiFetch<Novel>("/novels/confirm", {
    method: "POST",
    body: JSON.stringify(req),
  })
}

export function fetchSplitModes(): Promise<SplitModesResponse> {
  return apiFetch<SplitModesResponse>("/novels/split-modes")
}

export function reSplitChapters(req: ReSplitRequest): Promise<UploadPreviewResponse> {
  return apiFetch<UploadPreviewResponse>("/novels/re-split", {
    method: "POST",
    body: JSON.stringify(req),
  })
}

// ── Chapters & Reading ───────────────────────────

export function fetchChapters(novelId: string): Promise<{ chapters: Chapter[] }> {
  return apiFetch(`/novels/${novelId}/chapters`)
}

export interface SearchResult {
  chapter_num: number
  title: string
  snippet: string
}

export function searchChapters(
  novelId: string,
  query: string,
): Promise<{ results: SearchResult[]; total: number }> {
  return apiFetch(`/novels/${novelId}/search?q=${encodeURIComponent(query)}`)
}

export function fetchChapterContent(
  novelId: string,
  chapterNum: number,
): Promise<ChapterContent> {
  return apiFetch(`/novels/${novelId}/chapters/${chapterNum}`)
}

export function fetchChapterEntities(
  novelId: string,
  chapterNum: number,
): Promise<{ entities: ChapterEntity[] }> {
  return apiFetch(`/novels/${novelId}/chapters/${chapterNum}/entities`)
}

export function fetchUserState(novelId: string): Promise<UserState> {
  return apiFetch(`/novels/${novelId}/user-state`)
}

export function saveUserState(
  novelId: string,
  state: { last_chapter: number; scroll_position?: number },
): Promise<{ ok: boolean }> {
  return apiFetch(`/novels/${novelId}/user-state`, {
    method: "PUT",
    body: JSON.stringify(state),
  })
}

// ── Entities ─────────────────────────────────────

export function fetchEntities(
  novelId: string,
  type?: string,
): Promise<{ entities: EntitySummary[] }> {
  const params = type ? `?type=${type}` : ""
  return apiFetch(`/novels/${novelId}/entities${params}`)
}

export function fetchEntityProfile(
  novelId: string,
  name: string,
  type?: string,
): Promise<Record<string, unknown>> {
  const params = type ? `?type=${type}` : ""
  return apiFetch(`/novels/${novelId}/entities/${encodeURIComponent(name)}${params}`)
}

// ── Visualization ────────────────────────────────

function rangeParams(start?: number, end?: number): string {
  const parts: string[] = []
  if (start != null) parts.push(`chapter_start=${start}`)
  if (end != null) parts.push(`chapter_end=${end}`)
  return parts.length > 0 ? `?${parts.join("&")}` : ""
}

export function fetchGraphData(
  novelId: string,
  start?: number,
  end?: number,
): Promise<Record<string, unknown>> {
  return apiFetch(`/novels/${novelId}/graph${rangeParams(start, end)}`)
}

export function fetchMapData(
  novelId: string,
  start?: number,
  end?: number,
): Promise<Record<string, unknown>> {
  return apiFetch(`/novels/${novelId}/map${rangeParams(start, end)}`)
}

export function fetchTimelineData(
  novelId: string,
  start?: number,
  end?: number,
): Promise<Record<string, unknown>> {
  return apiFetch(`/novels/${novelId}/timeline${rangeParams(start, end)}`)
}

export function fetchFactionsData(
  novelId: string,
  start?: number,
  end?: number,
): Promise<Record<string, unknown>> {
  return apiFetch(`/novels/${novelId}/factions${rangeParams(start, end)}`)
}

// ── Analysis ──────────────────────────────────

export function startAnalysis(
  novelId: string,
  req?: AnalyzeRequest,
): Promise<{ task_id: string; status: string }> {
  return apiFetch(`/novels/${novelId}/analyze`, {
    method: "POST",
    body: JSON.stringify(req ?? {}),
  })
}

export function patchAnalysisTask(
  taskId: string,
  status: "paused" | "running" | "cancelled",
): Promise<{ task_id: string; status: string }> {
  return apiFetch(`/analysis/${taskId}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  })
}

export function getAnalysisTask(taskId: string): Promise<AnalysisTask> {
  return apiFetch<AnalysisTask>(`/analysis/${taskId}`)
}

export function getLatestAnalysisTask(
  novelId: string,
): Promise<{ task: AnalysisTask | null }> {
  return apiFetch(`/novels/${novelId}/analysis/latest`)
}

// ── Chat / Conversations ─────────────────────

export function fetchConversations(
  novelId: string,
): Promise<{ conversations: Conversation[] }> {
  return apiFetch(`/novels/${novelId}/conversations`)
}

export function createConversation(
  novelId: string,
  title?: string,
): Promise<Conversation> {
  return apiFetch(`/novels/${novelId}/conversations`, {
    method: "POST",
    body: JSON.stringify({ title: title ?? "新对话" }),
  })
}

export function deleteConversation(
  conversationId: string,
): Promise<{ ok: boolean }> {
  return apiFetch(`/conversations/${conversationId}`, { method: "DELETE" })
}

export function fetchMessages(
  conversationId: string,
): Promise<{ messages: ChatMessage[]; conversation: Conversation }> {
  return apiFetch(`/conversations/${conversationId}/messages`)
}

export function exportConversationUrl(conversationId: string): string {
  return `${BASE}/conversations/${conversationId}/export`
}

// ── Encyclopedia ─────────────────────────────

export function fetchEncyclopediaStats(
  novelId: string,
): Promise<Record<string, unknown>> {
  return apiFetch(`/novels/${novelId}/encyclopedia`)
}

export function fetchEncyclopediaEntries(
  novelId: string,
  category?: string,
  sort?: string,
): Promise<{ entries: { name: string; type: string; category: string; definition: string; first_chapter: number }[] }> {
  const params: string[] = []
  if (category) params.push(`category=${category}`)
  if (sort) params.push(`sort=${sort}`)
  const qs = params.length > 0 ? `?${params.join("&")}` : ""
  return apiFetch(`/novels/${novelId}/encyclopedia/entries${qs}`)
}

export function fetchConceptDetail(
  novelId: string,
  name: string,
): Promise<Record<string, unknown>> {
  return apiFetch(`/novels/${novelId}/encyclopedia/${encodeURIComponent(name)}`)
}

// ── Export / Import ─────────────────────────────

export function exportNovelUrl(novelId: string): string {
  return `${BASE}/novels/${novelId}/export`
}

export async function previewImport(file: File): Promise<ImportPreview> {
  const form = new FormData()
  form.append("file", file)
  const res = await fetch(`${BASE}/novels/import/preview`, {
    method: "POST",
    body: form,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail ?? `Preview failed: ${res.status}`)
  }
  return res.json() as Promise<ImportPreview>
}

export async function confirmDataImport(
  file: File,
  overwrite: boolean = false,
): Promise<Record<string, unknown>> {
  const form = new FormData()
  form.append("file", file)
  const res = await fetch(
    `${BASE}/novels/import/confirm?overwrite=${overwrite}`,
    { method: "POST", body: form },
  )
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail ?? `Import failed: ${res.status}`)
  }
  return res.json() as Promise<Record<string, unknown>>
}
