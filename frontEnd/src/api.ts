import type { Caption, CutResponse, Edit, RemoveInterval, Upload } from './types'

export const API_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, init)
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(body.detail || 'Não foi possível concluir a solicitação.')
  return body as T
}

export function uploadProject(video: File, captions: File, title: string): Promise<Upload> {
  const form = new FormData()
  form.append('video', video)
  form.append('captions', captions)
  form.append('title', title)
  return api<Upload>('/api/uploads', { method: 'POST', body: form })
}

export function requestCut(projectId: string, removeIntervals: RemoveInterval[]): Promise<CutResponse> {
  return api<CutResponse>(`/api/projects/${projectId}/cuts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ remove_intervals: removeIntervals }),
  })
}

export function startEdit(projectId: string, payload: Record<string, unknown>): Promise<Edit> {
  return api<Edit>(`/api/projects/${projectId}/edits`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function getEditProgress(projectId: string, editId: string): Promise<Edit> {
  return api<Edit>(`/api/projects/${projectId}/edits/${editId}`)
}

export function projectVideoUrl(projectId: string): string {
  return `${API_URL}/api/projects/${projectId}/video`
}

// Aceita o formato legado (lista de {word,start,end}) e o novo ({ legendas: [{ texto, start, end }] }).
export function normalizeCaptionsPayload(payload: unknown): Caption[] {
  if (Array.isArray(payload)) return payload as Caption[]
  if (payload && typeof payload === 'object' && Array.isArray((payload as { legendas?: unknown }).legendas)) {
    const legendas = (payload as { legendas: { texto: string; start: number; end: number }[] }).legendas
    return legendas.map((item) => ({ word: item.texto, start: item.start, end: item.end }))
  }
  throw new Error('O arquivo de legendas deve ser uma lista ou conter a chave "legendas".')
}

export async function readCaptionsFile(file: File): Promise<Caption[]> {
  return normalizeCaptionsPayload(JSON.parse(await file.text()))
}
