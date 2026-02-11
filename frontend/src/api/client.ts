import type {
  AnalysisTask,
  AnalyzeRequest,
  ConfirmImportRequest,
  EnvironmentCheck,
  Novel,
  NovelsListResponse,
  ReSplitRequest,
  SplitModesResponse,
  UploadPreviewResponse,
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
