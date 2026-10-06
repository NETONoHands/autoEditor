import { useCallback, useEffect, useRef, useState } from 'react'
import { API_URL } from './api'
import type { Metadata } from './types'
import './CropSelector.css'

type Crop = { x: number; y: number; w: number; h: number }
type Point = { x: number; y: number }
type Selection = { start: Point; end: Point }
type Step = 'camera' | 'content' | 'review'

type CropSelectorProps = {
  projectId: string
  metadata: Metadata
  onCropComplete: (camCrop: Crop, contentCrop: Crop) => void
}

function parseResolution(resolution: string): { width: number; height: number } | null {
  const match = resolution.match(/(\d+)\s*[x×]\s*(\d+)/i)
  if (!match) return null

  const width = Number(match[1])
  const height = Number(match[2])
  return width > 0 && height > 0 ? { width, height } : null
}

function CropSelector({ projectId, metadata, onCropComplete }: CropSelectorProps) {
  const imageRef = useRef<HTMLImageElement>(null)
  const dragStartRef = useRef<Point | null>(null)
  const [step, setStep] = useState<Step>('camera')
  const [selection, setSelection] = useState<Selection | null>(null)
  const [camCrop, setCamCrop] = useState<Selection | null>(null)
  const [contentCrop, setContentCrop] = useState<Selection | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [imageError, setImageError] = useState(false)
  const resolution = parseResolution(metadata.resolution_label)
  const thumbnailUrl = `${API_URL}/api/projects/${encodeURIComponent(projectId)}/thumbnail`

  const getPoint = useCallback((clientX: number, clientY: number): Point | null => {
    const image = imageRef.current
    if (!image) return null

    const bounds = image.getBoundingClientRect()
    if (bounds.width === 0 || bounds.height === 0) return null

    return {
      x: Math.min(1, Math.max(0, (clientX - bounds.left) / bounds.width)),
      y: Math.min(1, Math.max(0, (clientY - bounds.top) / bounds.height)),
    }
  }, [])

  const updateSelection = useCallback((clientX: number, clientY: number) => {
    const start = dragStartRef.current
    const end = getPoint(clientX, clientY)
    if (start && end) setSelection({ start, end })
  }, [getPoint])

  const finishSelection = useCallback((clientX: number, clientY: number) => {
    const start = dragStartRef.current
    const end = getPoint(clientX, clientY)
    dragStartRef.current = null
    setIsDragging(false)
    if (!start || !end) return
    setSelection({ start, end })
    if (Math.abs(end.x - start.x) < 0.005 || Math.abs(end.y - start.y) < 0.005) return

    const finished = { start, end }
    if (step === 'camera') {
      setCamCrop(finished)
      setSelection(null)
      setStep('content')
    } else if (step === 'content') {
      setContentCrop(finished)
      setSelection(null)
      setStep('review')
    }
  }, [getPoint, step])

  useEffect(() => {
    if (!isDragging) return

    const handleMouseMove = (event: MouseEvent) => updateSelection(event.clientX, event.clientY)
    const handleMouseUp = (event: MouseEvent) => finishSelection(event.clientX, event.clientY)
    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)

    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [finishSelection, isDragging, updateSelection])

  function handleMouseDown(event: React.MouseEvent<HTMLImageElement>) {
    if (event.button !== 0 || !resolution || imageError || step === 'review') return
    event.preventDefault()
    const point = getPoint(event.clientX, event.clientY)
    if (!point) return

    dragStartRef.current = point
    setSelection({ start: point, end: point })
    setIsDragging(true)
  }

  function toPixels(area: Selection, size: { width: number; height: number }): Crop {
    const left = Math.min(area.start.x, area.end.x)
    const top = Math.min(area.start.y, area.end.y)
    const right = Math.max(area.start.x, area.end.x)
    const bottom = Math.max(area.start.y, area.end.y)
    const x = Math.min(size.width - 1, Math.floor(left * size.width))
    const y = Math.min(size.height - 1, Math.floor(top * size.height))
    const rightEdge = Math.min(size.width, Math.ceil(right * size.width))
    const bottomEdge = Math.min(size.height, Math.ceil(bottom * size.height))
    return { x, y, w: Math.max(1, rightEdge - x), h: Math.max(1, bottomEdge - y) }
  }

  function handleConfirm() {
    if (!camCrop || !contentCrop || !resolution) return
    onCropComplete(toPixels(camCrop, resolution), toPixels(contentCrop, resolution))
  }

  function handleReset() {
    dragStartRef.current = null
    setIsDragging(false)
    setSelection(null)
    setCamCrop(null)
    setContentCrop(null)
    setStep('camera')
  }

  function toRectangle(area: Selection | null) {
    return area
      ? {
          left: Math.min(area.start.x, area.end.x) * 100,
          top: Math.min(area.start.y, area.end.y) * 100,
          width: Math.abs(area.end.x - area.start.x) * 100,
          height: Math.abs(area.end.y - area.start.y) * 100,
        }
      : null
  }

  const frameSource = step === 'camera' ? selection : camCrop
  const verticalFrame = frameSource && resolution
    ? (() => {
        const frameWidth = Math.min(resolution.height * 9 / 16, resolution.width)
        const selectedCenter = ((frameSource.start.x + frameSource.end.x) / 2) * resolution.width
        const frameX = Math.min(
          Math.max(selectedCenter - frameWidth / 2, 0),
          resolution.width - frameWidth,
        )
        return { left: frameX / resolution.width * 100, width: frameWidth / resolution.width * 100 }
      })()
    : null
  const rectangles = [
    { key: 'camera', className: 'is-camera', area: step === 'camera' ? selection : camCrop },
    { key: 'content', className: 'is-content', area: step === 'content' ? selection : contentCrop },
  ].map((item) => ({ ...item, rect: toRectangle(item.area) }))
  const instruction = step === 'camera'
    ? 'Desenhe o recorte da Câmera'
    : step === 'content'
      ? 'Desenhe o recorte do Conteúdo (Gameplay/Tela)'
      : 'Confira os dois enquadramentos e confirme.'

  return (
    <section className="crop-selector" aria-label="Seleção do recorte da câmera">
      <p className="crop-selector-hint">
        <strong>Passo {step === 'camera' ? 1 : step === 'content' ? 2 : 3} de 3 — {instruction}</strong>
      </p>
      {!resolution && (
        <p className="crop-selector-error" role="alert">
          Não foi possível identificar a resolução original do vídeo ({metadata.resolution_label}).
        </p>
      )}
      {imageError && (
        <p className="crop-selector-error" role="alert">
          Não foi possível carregar a thumbnail do vídeo.
        </p>
      )}
      <div className={`crop-selector-image ${step === 'review' ? '' : 'is-drawing'} ${isDragging ? 'is-dragging' : ''}`}>
        <img
          ref={imageRef}
          src={thumbnailUrl}
          alt="Thumbnail do vídeo para selecionar a área da câmera"
          draggable={false}
          onError={() => setImageError(true)}
          onLoad={() => setImageError(false)}
          onMouseDown={handleMouseDown}
          onMouseMove={(event) => {
            if (isDragging) updateSelection(event.clientX, event.clientY)
          }}
          onMouseUp={(event) => {
            if (isDragging) finishSelection(event.clientX, event.clientY)
          }}
        />
        {verticalFrame && (
          <div
            className="crop-selector-vertical-frame"
            aria-hidden="true"
            style={{ left: `${verticalFrame.left}%`, width: `${verticalFrame.width}%` }}
          />
        )}
        {rectangles.map(({ key, className, rect }) => rect && (
          <div
            key={key}
            className={`crop-selector-rectangle ${className}`}
            aria-hidden="true"
            style={{ left: `${rect.left}%`, top: `${rect.top}%`, width: `${rect.width}%`, height: `${rect.height}%` }}
          />
        ))}
      </div>
      <div className="crop-selector-actions">
        <button type="button" className="primary-button" disabled={step !== 'review' || !resolution} onClick={handleConfirm}>
          Confirmar Enquadramentos
        </button>
        <button type="button" className="secondary-button" disabled={step === 'camera' && !selection} onClick={handleReset}>
          Recomeçar
        </button>
      </div>
    </section>
  )
}

export default CropSelector
