export type Orientation = 'vertical' | 'horizontal'
export type EditingPace = 'natural' | 'fast' | 'jump-cut'
export type SafeArea = 0 | 0.1 | 0.15
export type Phase = 'input' | 'editing' | 'progress' | 'done'
export type FileState = 'idle' | 'loading' | 'ready'

export type Metadata = {
  duration_seconds: number
  resolution_label: string
  codec: string
  size_bytes: number
}

export type Upload = { project_id: string; metadata: Metadata }

export type Output = { output_id: string; filename: string; size_bytes: number }

export type EditStatus = 'queued' | 'running' | 'completed' | 'failed'

export type Edit = {
  edit_id: string
  status: EditStatus
  progress_percent: number
  stage: string
  error?: string
  outputs?: Output[]
}

/** Uma palavra transcrita com seus tempos absolutos (segundos decimais). */
export type Caption = { word: string; start: number; end: number }

/** Formato bruto recebido do arquivo de legendas importado. */
export type RawCaption = { texto: string; start: number; end: number }
export type RawCaptionsPayload = { legendas: RawCaption[] }

export type RemoveInterval = { start: number; end: number }


export type CutRequest = {
  remove_intervals: RemoveInterval[]
}

export type CutResponse = {
  project_id: string
  cut_id: string
  metadata: Metadata
  video_url: string
  captions: Caption[]
}
