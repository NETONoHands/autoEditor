import { useMemo, useState } from 'react'
import type { Caption, RemoveInterval } from './types'

type CaptionsEditorProps = {
  projectId: string
  videoSrc: string
  captions: Caption[]
  onCaptionsChange: (captions: Caption[]) => void
  onVideoSrcChange: (videoSrc: string) => void
  onRemoveIntervalsChange: (intervals: RemoveInterval[]) => void
  onContinue: () => void
}

function buildRemoveIntervals(captions: Caption[], selected: Set<number>): RemoveInterval[] {
  const sortedIndexes = [...selected].sort((a, b) => a - b)
  const intervals: RemoveInterval[] = []
  let runStart: number | null = null
  let previous: number | null = null

  for (const index of sortedIndexes) {
    if (runStart === null || previous === null) {
      runStart = index
      previous = index
      continue
    }
    if (index === previous + 1) {
      previous = index
      continue
    }
    intervals.push({ start: captions[runStart].start, end: captions[previous].end })
    runStart = index
    previous = index
  }
  if (runStart !== null && previous !== null) {
    intervals.push({ start: captions[runStart].start, end: captions[previous].end })
  }
  return intervals
}

function CaptionsEditor({ projectId: _projectId, videoSrc, captions, onCaptionsChange: _onCaptionsChange, onVideoSrcChange: _onVideoSrcChange, onRemoveIntervalsChange, onContinue }: CaptionsEditorProps) {
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [error, setError] = useState('')

  const selectedCount = selected.size
  const removeIntervals = useMemo(() => buildRemoveIntervals(captions, selected), [captions, selected])

  function toggleWord(index: number) {
    setError('')
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  function deleteSelected() {
    if (removeIntervals.length === 0) {
      setError('Selecione ao menos um intervalo válido para excluir.')
      return
    }
    setError('')
    onRemoveIntervalsChange(removeIntervals)
    onContinue()
  }

  return (
    <section className="captions-editor">
      <p className="eyebrow">02 / edição de texto</p>
      <h2>Corte pelo texto.</h2>
      <video className="captions-editor-video" src={videoSrc} controls key={videoSrc} />
      <div className="captions-words">
        {captions.map((caption, index) => (
          <button
            type="button"
            key={`${caption.word}-${caption.start}-${index}`}
            className={`caption-word ${selected.has(index) ? 'selected' : ''}`}
            onClick={() => toggleWord(index)}
          >
            {caption.word}
          </button>
        ))}
      </div>
      {error && <p className="error-message">{error}</p>}
      <div className="captions-editor-actions">
        <button type="button" className="secondary-button" onClick={deleteSelected}>
          {`Excluir selecionadas (${selectedCount})`}
        </button>
        <button type="button" className="primary-button" onClick={onContinue}>
          Continuar para edição final <span>↗</span>
        </button>
      </div>
    </section>
  )
}

export default CaptionsEditor
