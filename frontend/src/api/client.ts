import type {
  AnalysisStats,
  AnalysisTask,
  AnalyzeRequest,
  Chapter,
  ChapterContent,
  ChapterEntity,
  ChatMessage,
  CleanAndReSplitRequest,
  ConfirmImportRequest,
  Conversation,
  EntityDictionaryResponse,
  EntitySummary,
  EnvironmentCheck,
  HierarchyRebuildResult,
  ImportPreview,
  MapData,
  Novel,
  NovelsListResponse,
  OverrideType,
  PrescanStatusResponse,
  ReSplitRequest,
  SplitModesResponse,
  UploadPreviewResponse,
  UserState,
  WorldStructureData,
  WorldStructureOverride,
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

export function startOllama(): Promise<{ success: boolean; error?: string }> {
  return apiFetch<{ success: boolean; error?: string }>("/settings/ollama/start", {
    method: "POST",
  })
}

export function fetchHardware(): Promise<import("./types").HardwareInfo> {
  return apiFetch<import("./types").HardwareInfo>("/settings/hardware")
}

export function fetchModelRecommendations(): Promise<{
  total_ram_gb: number
  recommendations: import("./types").ModelRecommendation[]
}> {
  return apiFetch("/settings/ollama/recommendations")
}

export function pullOllamaModel(
  model: string,
  onProgress: (data: { status: string; completed?: number; total?: number; error?: string }) => void,
  onDone: () => void,
  onError: (error: string) => void,
): () => void {
  const controller = new AbortController()
  fetch(`/api/settings/ollama/pull`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) throw new Error(`Pull failed: ${res.status}`)
      const reader = res.body?.getReader()
      if (!reader) throw new Error("No response body")
      const decoder = new TextDecoder()
      let buffer = ""
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split("\n")
        buffer = lines.pop() ?? ""
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6))
              onProgress(data)
              if (data.status === "success") {
                onDone()
                return
              }
              if (data.status === "error") {
                onError(data.error ?? "未知错误")
                return
              }
            } catch { /* skip parse errors */ }
          }
        }
      }
      onDone()
    })
    .catch((err) => {
      if (err.name !== "AbortError") onError(err.message)
    })
  return () => controller.abort()
}

export function setDefaultModel(model: string): Promise<{ success: boolean; model: string }> {
  return apiFetch("/settings/ollama/default-model", {
    method: "POST",
    body: JSON.stringify({ model }),
  })
}

export function fetchCloudProviders(): Promise<{ providers: import("./types").CloudProvider[] }> {
  return apiFetch("/settings/cloud/providers")
}

export function fetchCloudConfig(): Promise<import("./types").CloudConfig> {
  return apiFetch("/settings/cloud/config")
}

export function saveCloudConfig(config: {
  provider: string
  base_url: string
  model: string
  api_key: string
}): Promise<{ success: boolean; storage: string }> {
  return apiFetch("/settings/cloud/config", {
    method: "POST",
    body: JSON.stringify(config),
  })
}

export function validateCloudApi(
  base_url: string,
  api_key: string,
): Promise<{ valid: boolean; error?: string }> {
  return apiFetch("/settings/cloud/validate", {
    method: "POST",
    body: JSON.stringify({ base_url, api_key }),
  })
}

export function fetchSettings(): Promise<{
  settings: {
    llm_provider: string
    llm_model: string
    ollama_base_url: string
    ollama_model: string
    required_model: string
    max_tokens: number
  }
}> {
  return apiFetch("/settings")
}

export function switchLlmMode(
  mode: string,
  ollamaModel?: string,
): Promise<{ success: boolean; mode: string; error?: string }> {
  return apiFetch("/settings/llm-mode", {
    method: "POST",
    body: JSON.stringify({ mode, ollama_model: ollamaModel }),
  })
}

export function fetchRunningTasks(): Promise<{ running_count: number }> {
  return apiFetch("/settings/running-tasks")
}

export function saveAdvancedSettings(
  maxTokens: number,
): Promise<{ success: boolean; max_tokens: number; error?: string }> {
  return apiFetch("/settings/advanced", {
    method: "POST",
    body: JSON.stringify({ max_tokens: maxTokens }),
  })
}

export function restoreDefaults(): Promise<{ success: boolean }> {
  return apiFetch("/settings/restore-defaults", { method: "POST" })
}

export function runModelBenchmark(): Promise<import("./types").BenchmarkResult> {
  return apiFetch("/settings/model-benchmark", { method: "POST" })
}

export function fetchBenchmarkHistory(): Promise<import("./types").BenchmarkRecord[]> {
  return apiFetch("/settings/model-benchmark/history")
}

export function deleteBenchmarkRecord(id: number): Promise<{ success: boolean }> {
  return apiFetch(`/settings/model-benchmark/history/${id}`, { method: "DELETE" })
}

export function fetchBudget(): Promise<import("./types").BudgetInfo> {
  return apiFetch("/settings/budget")
}

export function setBudget(monthlyCny: number): Promise<{ success: boolean; monthly_budget_cny: number }> {
  return apiFetch("/settings/budget", {
    method: "POST",
    body: JSON.stringify({ monthly_budget_cny: monthlyCny }),
  })
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

export function cleanAndResplit(req: CleanAndReSplitRequest): Promise<UploadPreviewResponse> {
  return apiFetch<UploadPreviewResponse>("/novels/clean-and-resplit", {
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

export function excludeChapters(
  novelId: string,
  chapterNums: number[],
  excluded: boolean,
): Promise<{ chapters: Chapter[] }> {
  return apiFetch(`/novels/${novelId}/chapters/exclude`, {
    method: "PATCH",
    body: JSON.stringify({ chapter_nums: chapterNums, excluded }),
  })
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
): Promise<{ entities: EntitySummary[]; alias_map: Record<string, string> }> {
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
  layerId?: string,
): Promise<MapData> {
  let params = rangeParams(start, end)
  if (layerId) {
    params += (params ? "&" : "?") + `layer_id=${encodeURIComponent(layerId)}`
  }
  return apiFetch<MapData>(`/novels/${novelId}/map${params}`)
}

export function fetchWorldStructure(
  novelId: string,
): Promise<WorldStructureData> {
  return apiFetch<WorldStructureData>(`/novels/${novelId}/world-structure`)
}

export function fetchWorldStructureOverrides(
  novelId: string,
): Promise<{ overrides: WorldStructureOverride[] }> {
  return apiFetch(`/novels/${novelId}/world-structure/overrides`)
}

export function saveWorldStructureOverrides(
  novelId: string,
  overrides: { override_type: OverrideType; override_key: string; override_json: Record<string, unknown> }[],
): Promise<WorldStructureData> {
  return apiFetch<WorldStructureData>(`/novels/${novelId}/world-structure/overrides`, {
    method: "PUT",
    body: JSON.stringify({ overrides }),
  })
}

export function deleteWorldStructureOverride(
  novelId: string,
  overrideId: number,
): Promise<WorldStructureData> {
  return apiFetch<WorldStructureData>(`/novels/${novelId}/world-structure/overrides/${overrideId}`, {
    method: "DELETE",
  })
}

export function rebuildHierarchy(
  novelId: string,
  onProgress?: (message: string) => void,
): Promise<HierarchyRebuildResult> {
  return new Promise((resolve, reject) => {
    fetch(`${BASE}/novels/${novelId}/world-structure/rebuild-hierarchy`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    })
      .then(async (res) => {
        if (!res.ok) throw new Error(`API error: ${res.status} ${res.statusText}`)
        const reader = res.body?.getReader()
        if (!reader) throw new Error("No response body")
        const decoder = new TextDecoder()
        let buffer = ""
        let result: HierarchyRebuildResult | null = null
        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split("\n")
          buffer = lines.pop() ?? ""
          for (const line of lines) {
            if (!line.startsWith("data: ")) continue
            try {
              const data = JSON.parse(line.slice(6))
              if (data.stage === "done") {
                result = data.result
              } else if (data.stage === "error") {
                reject(new Error(data.message))
                return
              } else if (onProgress && data.message) {
                onProgress(data.message)
              }
            } catch { /* skip parse errors */ }
          }
        }
        if (result) resolve(result)
        else reject(new Error("未收到重建结果"))
      })
      .catch(reject)
  })
}

export function applyHierarchyChanges(
  novelId: string,
  changes: { location: string; new_parent: string | null }[],
  locationTiers?: Record<string, string>,
): Promise<{
  status: string
  old_parent_count: number
  new_parent_count: number
  root_count: number
  roots: string[]
}> {
  return apiFetch(`/novels/${novelId}/world-structure/apply-hierarchy-changes`, {
    method: "POST",
    body: JSON.stringify({ changes, location_tiers: locationTiers }),
  })
}

export function saveLocationOverride(
  novelId: string,
  locationName: string,
  x: number,
  y: number,
  opts?: { constraint_type?: string; locked_parent?: string | null },
): Promise<{ status: string; message: string }> {
  return apiFetch(`/novels/${novelId}/map/layout/${encodeURIComponent(locationName)}`, {
    method: "PUT",
    body: JSON.stringify({ x, y, ...opts }),
  })
}

export function saveGeoLocationOverride(
  novelId: string,
  locationName: string,
  lat: number,
  lng: number,
  opts?: { constraint_type?: string; locked_parent?: string | null },
): Promise<{ status: string; message: string }> {
  return apiFetch(`/novels/${novelId}/map/layout/${encodeURIComponent(locationName)}`, {
    method: "PUT",
    body: JSON.stringify({ x: 0, y: 0, lat, lng, ...opts }),
  })
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

// ── Scenes (Screenplay Mode) ─────────────────────

export function fetchChapterScenes(
  novelId: string,
  chapterNum: number,
): Promise<import("./types").ChapterScenesResponse> {
  return apiFetch(`/novels/${novelId}/scenes/${chapterNum}`)
}

// ── Analysis ──────────────────────────────────

export function fetchCostEstimate(
  novelId: string,
  chapterStart?: number,
  chapterEnd?: number,
): Promise<import("./types").CostEstimate> {
  const params = rangeParams(chapterStart, chapterEnd)
  return apiFetch(`/novels/${novelId}/analyze/estimate${params}`)
}

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
): Promise<{ task: AnalysisTask | null; stats: AnalysisStats | null; quality: import("./types").AnalysisQualitySummary | null }> {
  return apiFetch(`/novels/${novelId}/analysis/latest`)
}

export function clearAnalysisData(
  novelId: string,
): Promise<{ ok: boolean; message: string }> {
  return apiFetch(`/novels/${novelId}/analysis`, { method: "DELETE" })
}

export function retryFailedChapters(
  novelId: string,
): Promise<{ retried: number; succeeded: number; failed: number }> {
  return apiFetch(`/novels/${novelId}/analysis/retry-failed`, { method: "POST" })
}

export function fetchActiveAnalyses(): Promise<{
  items: { novel_id: string; status: "running" | "paused" }[]
}> {
  return apiFetch(`/analysis/active`)
}

export function fetchCostDetail(
  novelId: string,
): Promise<import("./types").CostDetailResponse> {
  return apiFetch(`/novels/${novelId}/analysis/cost-detail`)
}

export function costDetailCsvUrl(novelId: string): string {
  return `/api/novels/${novelId}/analysis/cost-detail/csv`
}

export function fetchAnalysisRecords(): Promise<{
  records: import("./types").AnalysisRecord[]
}> {
  return apiFetch(`/settings/analysis-records`)
}

// ── Prescan Dictionary ──────────────────────────

export function fetchPrescanStatus(
  novelId: string,
): Promise<PrescanStatusResponse> {
  return apiFetch<PrescanStatusResponse>(`/novels/${novelId}/prescan`)
}

export function triggerPrescan(
  novelId: string,
): Promise<{ status: string }> {
  return apiFetch(`/novels/${novelId}/prescan`, { method: "POST" })
}

export function fetchEntityDictionary(
  novelId: string,
  type?: string,
  limit?: number,
): Promise<EntityDictionaryResponse> {
  const parts: string[] = []
  if (type) parts.push(`type=${encodeURIComponent(type)}`)
  if (limit != null) parts.push(`limit=${limit}`)
  const qs = parts.length > 0 ? `?${parts.join("&")}` : ""
  return apiFetch<EntityDictionaryResponse>(`/novels/${novelId}/entity-dictionary${qs}`)
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
): Promise<{ entries: { name: string; type: string; category: string; definition: string; first_chapter: number; parent?: string | null; depth?: number }[] }> {
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

// ── Series Bible ────────────────────────────────

export async function exportSeriesBible(
  novelId: string,
  req?: import("./types").SeriesBibleRequest,
): Promise<void> {
  const res = await fetch(`${BASE}/novels/${novelId}/series-bible/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req ?? {}),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail ?? `导出失败: ${res.status}`)
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  const defaultName = req?.format === "docx"
    ? "设定集.docx"
    : req?.format === "xlsx"
      ? "设定集.xlsx"
      : req?.format === "pdf"
        ? "设定集.pdf"
        : "设定集.md"
  a.download = decodeURIComponent(
    res.headers.get("Content-Disposition")?.match(/filename\*=UTF-8''(.+)/)?.[1] ?? defaultName,
  )
  a.click()
  URL.revokeObjectURL(url)
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

// ── Full Backup ──────────────────────────────────

export function backupExportUrl(): string {
  return `${BASE}/backup/export`
}

export async function previewBackupImport(
  file: File,
): Promise<import("./types").BackupPreview> {
  const form = new FormData()
  form.append("file", file)
  const res = await fetch(`${BASE}/backup/import/preview`, {
    method: "POST",
    body: form,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail ?? `预览失败: ${res.status}`)
  }
  return res.json() as Promise<import("./types").BackupPreview>
}

export async function confirmBackupImport(
  file: File,
  conflictMode: "skip" | "overwrite" = "skip",
): Promise<import("./types").BackupImportResult> {
  const form = new FormData()
  form.append("file", file)
  const res = await fetch(
    `${BASE}/backup/import/confirm?conflict_mode=${conflictMode}`,
    { method: "POST", body: form },
  )
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail ?? `导入失败: ${res.status}`)
  }
  return res.json() as Promise<import("./types").BackupImportResult>
}
