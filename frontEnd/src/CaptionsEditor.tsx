import { useEffect, useMemo, useState } from 'react'
import { getSuggestedCuts } from './api'
import type { Caption, RemoveInterval } from './types'

type CaptionsEditorProps = {
  projectId: string
  videoSrc: string
  captions: Caption[]
  onCaptionsChange: (captions: Caption[]) => void
  onVideoSrcChange: (videoSrc: string) => void
  onRemoveIntervalsChange: (intervals: RemoveInterval[]) => void
  onContinue: (intervals: RemoveInterval[]) => void
}

type Token = { kind: 'word' | 'silence'; index: number; time: number }

const MERGE_GAP_SECONDS = 0.15

function buildWordIntervals(captions: Caption[], selected: Set<number>): RemoveInterval[] {
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

// Une intervalos sobrepostos ou muito próximos para evitar micro-cortes.
function consolidateIntervals(intervals: RemoveInterval[]): RemoveInterval[] {
  const sorted = intervals.filter((item) => item.end > item.start).sort((a, b) => a.start - b.start)
  const merged: RemoveInterval[] = []
  for (const item of sorted) {
    const last = merged[merged.length - 1]
    if (last && item.start - last.end <= MERGE_GAP_SECONDS) last.end = Math.max(last.end, item.end)
    else merged.push({ ...item })
  }
  return merged
}

function wordsInsideIntervals(captions: Caption[], intervals: RemoveInterval[]): Set<number> {
  const result = new Set<number>()
  captions.forEach((caption, index) => {
    const middle = (caption.start + caption.end) / 2
    if (intervals.some((item) => middle >= item.start && middle <= item.end)) result.add(index)
  })
  return result
}

function CaptionsEditor({ projectId, videoSrc, captions, onCaptionsChange: _onCaptionsChange, onVideoSrcChange: _onVideoSrcChange, onRemoveIntervalsChange, onContinue }: CaptionsEditorProps) {
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [suggestedWords, setSuggestedWords] = useState<Set<number>>(new Set())
  const [silences, setSilences] = useState<RemoveInterval[]>([])
  const [selectedSilences, setSelectedSilences] = useState<Set<number>>(new Set())
  const [loadingSuggestions, setLoadingSuggestions] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoadingSuggestions(true)
    getSuggestedCuts(projectId)
      .then((suggestions) => {
        if (cancelled) return
        const words = wordsInsideIntervals(captions, suggestions.disfluencies)
        setSuggestedWords(words)
        setSelected(new Set(words))
        setSilences(suggestions.silences)
        setSelectedSilences(new Set(suggestions.silences.map((_, index) => index)))
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'Não foi possível calcular os cortes automáticos.')
      })
      .finally(() => {
        if (!cancelled) setLoadingSuggestions(false)
      })
    return () => {
      cancelled = true
    }
  }, [projectId, captions])

  const tokens = useMemo<Token[]>(() => {
    const items: Token[] = [
      ...captions.map((caption, index): Token => ({ kind: 'word', index, time: caption.start })),
      ...silences.map((silence, index): Token => ({ kind: 'silence', index, time: silence.start })),
    ]
    return items.sort((a, b) => a.time - b.time || (a.kind === 'word' ? -1 : 1))
  }, [captions, silences])

  const selectedCount = selected.size + selectedSilences.size
  const removeIntervals = useMemo(
    () => consolidateIntervals([
      ...buildWordIntervals(captions, selected),
      ...silences.filter((_, index) => selectedSilences.has(index)),
    ]),
    [captions, selected, silences, selectedSilences],
  )

  function toggle(setter: (update: (current: Set<number>) => Set<number>) => void, index: number) {
    setError('')
    setter((current) => {
      const next = new Set(current)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  function submit() {
    setError('')
    onRemoveIntervalsChange(removeIntervals)
    onContinue(removeIntervals)
  }

  return (
    <section className="captions-editor">
      <p className="eyebrow">02 / edição de texto</p>
      <h2>Corte pelo texto.</h2>
      <video className="captions-editor-video" src={videoSrc} controls key={videoSrc} />
      <p className="captions-hint">
        {loadingSuggestions
          ? 'Analisando silêncios e repetições…'
          : 'Trechos destacados foram sugeridos para remoção automática. Clique para desmarcar.'}
      </p>
      <div className="captions-words">
        {tokens.map((token) => {
          if (token.kind === 'silence') {
            const silence = silences[token.index]
            const isSelected = selectedSilences.has(token.index)
            return (
              <button
                type="button"
                key={`silence-${silence.start}-${token.index}`}
                className={`caption-word caption-silence suggested ${isSelected ? 'selected' : ''}`}
                title="Silêncio detectado"
                onClick={() => toggle(setSelectedSilences, token.index)}
              >
                {`pausa ${(silence.end - silence.start).toFixed(1)}s`}
              </button>
            )
          }
          const caption = captions[token.index]
          const classes = ['caption-word']
          if (selected.has(token.index)) classes.push('selected')
          if (suggestedWords.has(token.index)) classes.push('suggested')
          return (
            <button
              type="button"
              key={`${caption.word}-${caption.start}-${token.index}`}
              className={classes.join(' ')}
              onClick={() => toggle(setSelected, token.index)}
            >
              {caption.word}
            </button>
          )
        })}
      </div>
      {error && <p className="error-message">{error}</p>}
      <div className="captions-editor-actions">
        <button type="button" className="secondary-button" onClick={submit} disabled={removeIntervals.length === 0}>
          {`Excluir selecionadas (${selectedCount})`}
        </button>
        <button type="button" className="primary-button" onClick={submit} disabled={loadingSuggestions}>
          Continuar para edição final <span>↗</span>
        </button>
      </div>
    </section>
  )
}

export default CaptionsEditor
