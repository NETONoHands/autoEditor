import { useEffect, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import './App.css'
import CaptionsEditor from './CaptionsEditor'
import CropSelector from './CropSelector'
import { API_URL, getEditProgress, projectVideoUrl, readCaptionsFile, requestCut, startEdit, uploadProject } from './api'
import type { Caption, Edit, EditingPace, FileState, Metadata, Orientation, Phase, SafeArea, RemoveInterval } from './types'

const bytes = (value: number) => value < 1048576 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1048576).toFixed(1)} MB`
const duration = (value: number) => `${Math.floor(value / 60)} min ${Math.round(value % 60).toString().padStart(2, '0')} s`

function App() {
  const [phase, setPhase] = useState<Phase>('input')
  const [video, setVideo] = useState<File | null>(null)
  const [captionsFile, setCaptionsFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [displayTitle, setDisplayTitle] = useState('')
  const [orientation, setOrientation] = useState<Orientation>('vertical')
  const [editingPace, setEditingPace] = useState<EditingPace>('natural')
  const [safeArea, setSafeArea] = useState<SafeArea>(0)
  const [cropX, setCropX] = useState<number | null>(null)
  const [cropY, setCropY] = useState<number | null>(null)
  const [cropW, setCropW] = useState<number | null>(null)
  const [crop_h, setCropH] = useState<number | null>(null)
  const [contentCrop, setContentCrop] = useState<{ x: number; y: number; w: number; h: number } | null>(null)
  const [subtitleFont, setSubtitleFont] = useState('Arial')
  const [subtitleColor, setSubtitleColor] = useState('white_black_outline')
  const [subtitlePosition, setSubtitlePosition] = useState<'top' | 'center' | 'bottom'>('bottom')
  const [subtitleScale, setSubtitleScale] = useState(1)
  const [fileState, setFileState] = useState({ video: 'idle' as FileState, captions: 'idle' as FileState })
  const [metadata, setMetadata] = useState<Metadata | null>(null)
  const [projectId, setProjectId] = useState('')
  const [videoSrc, setVideoSrc] = useState('')
  const [captions, setCaptions] = useState<Caption[]>([])
  const [, setRemoveIntervals] = useState<RemoveInterval[]>([])
  const [edit, setEdit] = useState<Edit | null>(null)
  const [error, setError] = useState('')

  const valid = Boolean(video && captionsFile && title.trim() && fileState.video === 'ready' && fileState.captions === 'ready')

  useEffect(() => {
    if (!edit || !projectId || !['queued', 'running'].includes(edit.status)) return
    const timer = window.setInterval(async () => {
      try {
        const next = await getEditProgress(projectId, edit.edit_id)
        setEdit(next)
        if (next.status === 'completed') setPhase('done')
      } catch (reason) { setError(reason instanceof Error ? reason.message : 'Falha ao consultar o progresso.') }
    }, 1200)
    return () => window.clearInterval(timer)
  }, [edit, projectId])

  function chooseFile(kind: 'video' | 'captions', file?: File) {
    if (!file) return
    const extension = kind === 'video' ? '.mp4' : '.json'
    if (!file.name.toLowerCase().endsWith(extension)) { setError(`Selecione um arquivo ${extension}.`); return }
    setError(''); setFileState((current) => ({ ...current, [kind]: 'loading' }))
    if (kind === 'video') setVideo(file)
    else setCaptionsFile(file)
    window.setTimeout(() => setFileState((current) => ({ ...current, [kind]: 'ready' })), 250)
  }

  async function start(event: FormEvent) {
    event.preventDefault(); if (!valid || !video || !captionsFile) return
    setError(''); setPhase('progress')
    try {
      const upload = await uploadProject(video, captionsFile, title.trim())
      const parsedCaptions = await readCaptionsFile(captionsFile)
      setProjectId(upload.project_id); setMetadata(upload.metadata)
      setCaptions(parsedCaptions); setVideoSrc(projectVideoUrl(upload.project_id))
      setPhase('crop-select')
    } catch (reason) { setPhase('input'); setError(reason instanceof Error ? reason.message : 'Não foi possível enviar os arquivos.') }
  }

  function completeCrop(crop: { x: number; y: number; w: number; h: number }, content: { x: number; y: number; w: number; h: number }) {
    setContentCrop(content)
    setCropX(crop.x)
    setCropY(crop.y)
    setCropW(crop.w)
    setCropH(crop.h)
    setPhase('editing')
  }

  async function finishEditing(intervals: RemoveInterval[]) {
    setError(''); setPhase('progress')
    try {
      if (intervals.length > 0) {
        const cutResponse = await requestCut(projectId, intervals)
        setVideoSrc(cutResponse.video_url)
        setCaptions(cutResponse.captions)
        setMetadata(cutResponse.metadata)
      }

      const silenceThreshold = editingPace === 'natural' ? 0.3 : editingPace === 'fast' ? 0.15 : 0.1
      const started = await startEdit(projectId, { name: title.trim(), display_title: displayTitle.trim(), orientation, remove_silence: editingPace !== 'jump-cut', silence_threshold: silenceThreshold, face_tracking: safeArea === 0, safe_area: safeArea, crop_x: cropX, crop_y: cropY, crop_w: cropW, crop_h, content_crop_x: contentCrop?.x ?? null, content_crop_y: contentCrop?.y ?? null, content_crop_w: contentCrop?.w ?? null, content_crop_h: contentCrop?.h ?? null, subtitle_font: subtitleFont, subtitle_color_preset: subtitleColor, subtitle_position_y: subtitlePosition, subtitle_scale: subtitleScale })
      setEdit(started)
    } catch (reason) { setPhase('editing'); setError(reason instanceof Error ? reason.message : 'Não foi possível iniciar a edição.') }
  }

  function reset() { setPhase('input'); setVideo(null); setCaptionsFile(null); setTitle(''); setDisplayTitle(''); setEditingPace('natural'); setSafeArea(0); setCropX(null); setCropY(null); setCropW(null); setCropH(null); setContentCrop(null); setSubtitleFont('Arial'); setSubtitleColor('white_black_outline'); setSubtitlePosition('bottom'); setSubtitleScale(1); setMetadata(null); setProjectId(''); setVideoSrc(''); setCaptions([]); setEdit(null); setError(''); setFileState({ video: 'idle', captions: 'idle' }); setRemoveIntervals([]) }
  const stage = edit?.status === 'queued' ? 'Na fila' : edit?.status === 'running' ? edit.stage || 'Processando' : edit?.status === 'completed' ? 'Concluído' : 'Falhou'

  return <main className="app-shell">
    <header className="topbar"><div className="brand-mark">TL</div><div><span className="kicker">estúdio de edição</span><h1>Tapa na Lata</h1></div><span className="version">MVP / 01</span></header>
    {phase === 'input' && <form className="workspace" onSubmit={start}><section className="intro"><p className="eyebrow">01 / entrada</p><h2>Deixe o vídeo pronto<br /><em>para bater forte.</em></h2><p className="intro-copy">Envie a gravação e a legenda. A gente cuida do corte, do som e do enquadramento.</p></section><section className="form-panel"><FileDrop label="Vídeo da live" hint="MP4 · até 2 GB" state={fileState.video} file={video} accept=".mp4" onChange={(event) => chooseFile('video', event.target.files?.[0])} /><FileDrop label="Legenda" hint="JSON · palavra a palavra" state={fileState.captions} file={captionsFile} accept=".json" onChange={(event) => chooseFile('captions', event.target.files?.[0])} /><label className="field-label">Nome da edição<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Ex.: Como funciona o Quadstick" /></label><label className="field-label">Título no vídeo <small>(opcional)</small><input value={displayTitle} onChange={(event) => setDisplayTitle(event.target.value)} placeholder="Deixe vazio para não exibir" /></label><div className="orientation-row"><span className="field-label">Formato de saída</span><div className="segmented">{(['vertical', 'horizontal'] as Orientation[]).map((item) => <button type="button" className={orientation === item ? 'selected' : ''} key={item} onClick={() => setOrientation(item)}>{item === 'vertical' ? '9:16 vertical' : '16:9 horizontal'}</button>)}</div></div><div className="orientation-row"><span className="field-label">Ritmo de edição</span><div className="segmented">{([{ value: 'natural', label: 'Natural' }, { value: 'fast', label: 'Rápido' }, { value: 'jump-cut', label: 'Jump cut' }] as const).map((item) => <button type="button" className={editingPace === item.value ? 'selected' : ''} key={item.value} onClick={() => setEditingPace(item.value)}>{item.label}</button>)}</div></div>{orientation === 'vertical' && <div className="orientation-row"><span className="field-label">Área segura</span><div className="segmented">{([{ value: 0, label: 'Sem margem' }, { value: 0.1, label: '10%' }, { value: 0.15, label: '15%' }] as const).map((item) => <button type="button" className={safeArea === item.value ? 'selected' : ''} key={item.value} onClick={() => setSafeArea(item.value)}>{item.label}</button>)}</div></div>}<section className="subtitle-settings"><span className="field-label">Estilo de Legendas</span><label className="field-label">Fonte<select value={subtitleFont} onChange={(event) => setSubtitleFont(event.target.value)}>{['Arial', 'Roboto', 'Anton', 'Sansation', 'DejaVu Sans'].map((font) => <option key={font} value={font}>{font}</option>)}</select></label><label className="field-label">Cores<select value={subtitleColor} onChange={(event) => setSubtitleColor(event.target.value)}>{([{ value: 'white_black_outline', label: 'Branco com contorno preto' }, { value: 'yellow_shadow', label: 'Amarelo com sombra' }, { value: 'white_black_box', label: 'Branco com caixa preta' }, { value: 'cyan_black_outline', label: 'Ciano com contorno preto' }] as const).map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label><div className="orientation-row"><span className="field-label">Posição vertical</span><div className="segmented">{([{ value: 'top', label: 'Topo' }, { value: 'center', label: 'Centro' }, { value: 'bottom', label: 'Fundo' }] as const).map((item) => <button type="button" className={subtitlePosition === item.value ? 'selected' : ''} key={item.value} onClick={() => setSubtitlePosition(item.value)}>{item.label}</button>)}</div></div><label className="field-label">Tamanho da fonte ({subtitleScale.toFixed(1)}×)<input type="range" min="0.5" max="2" step="0.1" value={subtitleScale} onChange={(event) => setSubtitleScale(Number(event.target.value))} /></label></section>{metadata && <div className="metadata-grid"><Metric label="duração" value={duration(metadata.duration_seconds)} /><Metric label="resolução" value={metadata.resolution_label} /><Metric label="formato" value={metadata.codec.toUpperCase()} /><Metric label="tamanho" value={bytes(metadata.size_bytes)} /></div>}{error && <p className="error-message">{error}</p>}<button className="primary-button" disabled={!valid || fileState.video === 'loading' || fileState.captions === 'loading'}>Iniciar edição <span>↗</span></button></section></form>}
    {phase === 'crop-select' && metadata && <section className="crop-select-view"><p className="eyebrow">02 / enquadramento</p><h2>Defina os enquadramentos.</h2><CropSelector projectId={projectId} metadata={metadata} onCropComplete={completeCrop} /></section>}
    {phase === 'editing' && <CaptionsEditor projectId={projectId} videoSrc={videoSrc} captions={captions} onCaptionsChange={setCaptions} onVideoSrcChange={setVideoSrc} onRemoveIntervalsChange={setRemoveIntervals} onContinue={finishEditing} />}
      {phase !== 'input' && phase !== 'crop-select' && phase !== 'editing' && <section className="progress-view"><p className="eyebrow">03 / processamento</p><h2>{edit?.status === 'failed' ? 'Algo saiu do eixo.' : edit?.status === 'completed' ? 'Está na lata.' : 'Ajustando cada detalhe.'}</h2>{metadata && <div className="metadata-grid progress-metadata"><Metric label="duração" value={duration(metadata.duration_seconds)} /><Metric label="resolução" value={metadata.resolution_label} /><Metric label="formato" value={metadata.codec.toUpperCase()} /><Metric label="tamanho" value={bytes(metadata.size_bytes)} /></div>}<div className="progress-card"><div className="progress-head"><span>{stage}</span><strong>{edit?.progress_percent || 0}%</strong></div><div className="progress-track"><div style={{ width: `${edit?.progress_percent || 0}%` }} /></div><p className="stage-copy">{edit?.status === 'failed' ? edit.error : 'Aplicando tratamento, enquadramento e acabamento à sua edição.'}</p></div>{phase === 'done' && <div className="results"><p className="eyebrow">arquivos finais</p>{edit?.outputs?.filter((item) => /\.json$/i.test(item.filename) || (item.filename.toLowerCase().includes(orientation) && item.filename.endsWith('.mp4'))).map((item) => <a className="result-link" href={`${API_URL}/api/projects/${projectId}/edits/${edit.edit_id}/outputs/${item.output_id}/download`} key={item.output_id}><span>{item.filename.endsWith('.json') ? 'JSON' : orientation === 'vertical' ? '9:16' : '16:9'}</span>{item.filename}<b>↓</b></a>)}</div>}<button className="secondary-button" type="button" onClick={reset}>+ Novo processamento</button></section>}
  </main>
}

function FileDrop({ label, hint, state, file, accept, onChange }: { label: string; hint: string; state: FileState; file: File | null; accept: string; onChange: (event: ChangeEvent<HTMLInputElement>) => void }) { return <label className={`file-drop ${state}`}><input type="file" accept={accept} onChange={onChange} /><span className="file-icon">{state === 'ready' ? '✓' : '↑'}</span><span><strong>{file?.name || label}</strong><small>{state === 'loading' ? 'arquivo carregando...' : state === 'ready' ? 'arquivo pronto' : hint}</small></span><i>+</i></label> }
function Metric({ label, value }: { label: string; value: string }) { return <div><small>{label}</small><strong>{value}</strong></div> }
export default App
