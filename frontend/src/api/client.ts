import type { NovelsListResponse } from "./types"

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

export function deleteNovel(novelId: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/novels/${novelId}`, { method: "DELETE" })
}
