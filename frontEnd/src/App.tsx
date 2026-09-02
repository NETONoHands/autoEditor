import { useEffect, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import './App.css'

type Orientation = 'vertical' | 'horizontal'
type EditingPace = 'natural' | 'fast' | 'jump-cut'
type SafeArea = 0 | 0.1 | 0.15
type Phase = 'input' | 'progress' | 'done'
type FileState = 'idle' | 'loading' | 'ready'
type Metadata = { duration_seconds: number; resolution_label: string; codec: string; size_bytes: number }
type Upload = { project_id: string; metadata: Metadata }
type Output = { output_id: string; filename: string; size_bytes: number }
type Edit = { edit_id: string; status: 'queued' | 'running' | 'completed' | 'failed'; progress_percent: number; stage: string; error?: string; outputs?: Output[] }

const API_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'
const bytes = (value: number) => value < 1048576 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1048576).toFixed(1)} MB`
const duration = (value: number) => `${Math.floor(value / 60)} min ${Math.round(value % 60).toString().padStart(2, '0')} s`

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, init)
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(body.detail || 'Não foi possível concluir a solicitação.')
  return body as T
}

function App() {
  const [phase, setPhase] = useState<Phase>('input')
  const [video, setVideo] = useState<File | null>(null)
  const [subtitle, setSubtitle] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [displayTitle, setDisplayTitle] = useState('')
  const [orientation, setOrientation] = useState<Orientation>('vertical')
  const [editingPace, setEditingPace] = useState<EditingPace>('natural')
  const [safeArea, setSafeArea] = useState<SafeArea>(0)
  const [fileState, setFileState] = useState({ video: 'idle' as FileState, subtitle: 'idle' as FileState })
  const [metadata, setMetadata] = useState<Metadata | null>(null)
  const [projectId, setProjectId] = useState('')
  const [edit, setEdit] = useState<Edit | null>(null)
  const [error, setError] = useState('')

  const valid = Boolean(video && subtitle && title.trim() && fileState.video === 'ready' && fileState.subtitle === 'ready')

  useEffect(() => {
    if (!edit || !projectId || !['queued', 'running'].includes(edit.status)) return
    const timer = window.setInterval(async () => {
      try {
        const next = await api<Edit>(`/api/projects/${projectId}/edits/${edit.edit_id}`)
        setEdit(next)
        if (next.status === 'completed') setPhase('done')
      } catch (reason) { setError(reason instanceof Error ? reason.message : 'Falha ao consultar o progresso.') }
    }, 1200)
    return () => window.clearInterval(timer)
  }, [edit, projectId])

  function chooseFile(kind: 'video' | 'subtitle', file?: File) {
    if (!file) return
    const extension = kind === 'video' ? '.mp4' : '.srt'
    if (!file.name.toLowerCase().endsWith(extension)) { setError(`Selecione um arquivo ${extension}.`); return }
    setError(''); setFileState((current) => ({ ...current, [kind]: 'loading' }))
    if (kind === 'video') setVideo(file)
    else setSubtitle(file)
    window.setTimeout(() => setFileState((current) => ({ ...current, [kind]: 'ready' })), 250)
  }

  async function start(event: FormEvent) {
    event.preventDefault(); if (!valid || !video || !subtitle) return
    setError(''); setPhase('progress')
    try {
      const form = new FormData(); form.append('video', video); form.append('subtitle', subtitle); form.append('title', title.trim())
      const upload = await api<Upload>('/api/uploads', { method: 'POST', body: form })
      setProjectId(upload.project_id); setMetadata(upload.metadata)
      const silenceThreshold = editingPace === 'natural' ? 0.3 : editingPace === 'fast' ? 0.15 : 0.1
      const started = await api<Edit>(`/api/projects/${upload.project_id}/edits`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: title.trim(), display_title: displayTitle.trim(), orientation, remove_silence: true, silence_threshold: silenceThreshold, face_tracking: orientation === 'vertical', safe_area: orientation === 'vertical' ? safeArea : 0 }) })
      setEdit(started)
    } catch (reason) { setPhase('input'); setError(reason instanceof Error ? reason.message : 'Não foi possível iniciar a edição.') }
  }

  function reset() { setPhase('input'); setVideo(null); setSubtitle(null); setTitle(''); setDisplayTitle(''); setEditingPace('natural'); setSafeArea(0); setMetadata(null); setProjectId(''); setEdit(null); setError(''); setFileState({ video: 'idle', subtitle: 'idle' }) }
  const stage = edit?.status === 'queued' ? 'Na fila' : edit?.status === 'running' ? edit.stage || 'Processando' : edit?.status === 'completed' ? 'Concluído' : 'Falhou'

  return <main className="app-shell">
    <header className="topbar"><div className="brand-mark">TL</div><div><span className="kicker">estúdio de edição</span><h1>Tapa na Lata</h1></div><span className="version">MVP / 01</span></header>
    {phase === 'input' && <form className="workspace" onSubmit={start}><section className="intro"><p className="eyebrow">01 / entrada</p><h2>Deixe o vídeo pronto<br /><em>para bater forte.</em></h2><p className="intro-copy">Envie a gravação e a legenda. A gente cuida do corte, do som e do enquadramento.</p></section><section className="form-panel"><FileDrop label="Vídeo da live" hint="MP4 · até 2 GB" state={fileState.video} file={video} accept=".mp4" onChange={(event) => chooseFile('video', event.target.files?.[0])} /><FileDrop label="Legenda" hint="SRT · UTF-8" state={fileState.subtitle} file={subtitle} accept=".srt" onChange={(event) => chooseFile('subtitle', event.target.files?.[0])} /><label className="field-label">Nome da edição<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Ex.: Como funciona o Quadstick" /></label><label className="field-label">Título no vídeo <small>(opcional)</small><input value={displayTitle} onChange={(event) => setDisplayTitle(event.target.value)} placeholder="Deixe vazio para não exibir" /></label><div className="orientation-row"><span className="field-label">Formato de saída</span><div className="segmented">{(['vertical', 'horizontal'] as Orientation[]).map((item) => <button type="button" className={orientation === item ? 'selected' : ''} key={item} onClick={() => setOrientation(item)}>{item === 'vertical' ? '9:16 vertical' : '16:9 horizontal'}</button>)}</div></div><div className="orientation-row"><span className="field-label">Ritmo de edição</span><div className="segmented">{([{ value: 'natural', label: 'Natural' }, { value: 'fast', label: 'Rápido' }, { value: 'jump-cut', label: 'Jump cut' }] as const).map((item) => <button type="button" className={editingPace === item.value ? 'selected' : ''} key={item.value} onClick={() => setEditingPace(item.value)}>{item.label}</button>)}</div></div>{orientation === 'vertical' && <div className="orientation-row"><span className="field-label">Área segura</span><div className="segmented">{([{ value: 0, label: 'Sem margem' }, { value: 0.1, label: '10%' }, { value: 0.15, label: '15%' }] as const).map((item) => <button type="button" className={safeArea === item.value ? 'selected' : ''} key={item.value} onClick={() => setSafeArea(item.value)}>{item.label}</button>)}</div></div>}{metadata && <div className="metadata-grid"><Metric label="duração" value={duration(metadata.duration_seconds)} /><Metric label="resolução" value={metadata.resolution_label} /><Metric label="formato" value={metadata.codec.toUpperCase()} /><Metric label="tamanho" value={bytes(metadata.size_bytes)} /></div>}{error && <p className="error-message">{error}</p>}<button className="primary-button" disabled={!valid || fileState.video === 'loading' || fileState.subtitle === 'loading'}>Iniciar edição <span>↗</span></button></section></form>}
      {phase !== 'input' && <section className="progress-view"><p className="eyebrow">02 / processamento</p><h2>{edit?.status === 'failed' ? 'Algo saiu do eixo.' : edit?.status === 'completed' ? 'Está na lata.' : 'Ajustando cada detalhe.'}</h2>{metadata && <div className="metadata-grid progress-metadata"><Metric label="duração" value={duration(metadata.duration_seconds)} /><Metric label="resolução" value={metadata.resolution_label} /><Metric label="formato" value={metadata.codec.toUpperCase()} /><Metric label="tamanho" value={bytes(metadata.size_bytes)} /></div>}<div className="progress-card"><div className="progress-head"><span>{stage}</span><strong>{edit?.progress_percent || 0}%</strong></div><div className="progress-track"><div style={{ width: `${edit?.progress_percent || 0}%` }} /></div><p className="stage-copy">{edit?.status === 'failed' ? edit.error : 'Aplicando tratamento, enquadramento e acabamento à sua edição.'}</p></div>{phase === 'done' && <div className="results"><p className="eyebrow">arquivos finais</p>{edit?.outputs?.filter((item) => /\.srt$/i.test(item.filename) || (item.filename.toLowerCase().includes(orientation) && item.filename.endsWith('.mp4'))).map((item) => <a className="result-link" href={`${API_URL}/api/projects/${projectId}/edits/${edit.edit_id}/outputs/${item.output_id}/download`} key={item.output_id}><span>{item.filename.endsWith('.srt') ? 'SRT' : orientation === 'vertical' ? '9:16' : '16:9'}</span>{item.filename}<b>↓</b></a>)}</div>}<button className="secondary-button" type="button" onClick={reset}>+ Novo processamento</button></section>}
  </main>
}

function FileDrop({ label, hint, state, file, accept, onChange }: { label: string; hint: string; state: FileState; file: File | null; accept: string; onChange: (event: ChangeEvent<HTMLInputElement>) => void }) { return <label className={`file-drop ${state}`}><input type="file" accept={accept} onChange={onChange} /><span className="file-icon">{state === 'ready' ? '✓' : '↑'}</span><span><strong>{file?.name || label}</strong><small>{state === 'loading' ? 'arquivo carregando...' : state === 'ready' ? 'arquivo pronto' : hint}</small></span><i>+</i></label> }
function Metric({ label, value }: { label: string; value: string }) { return <div><small>{label}</small><strong>{value}</strong></div> }
export default App
