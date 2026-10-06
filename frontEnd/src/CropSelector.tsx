import { useCallback, useEffect, useRef, useState } from 'react'
import { API_URL } from './api'
import type { Metadata } from './types'
import './CropSelector.css'

type Crop = { x: number; y: number; w: number; h: number }
type Point = { x: number; y: number }

type CropSelectorProps = {
  projectId: string
  metadata: Metadata
  onCropComplete: (crop: Crop) => void
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
  const [selection, setSelection] = useState<{ start: Point; end: Point } | null>(null)
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
    updateSelection(clientX, clientY)
    dragStartRef.current = null
    setIsDragging(false)
  }, [updateSelection])

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
    if (event.button !== 0 || !resolution || imageError) return
    event.preventDefault()
    const point = getPoint(event.clientX, event.clientY)
    if (!point) return

    dragStartRef.current = point
    setSelection({ start: point, end: point })
    setIsDragging(true)
  }

  function handleConfirm() {
    if (!selection || !resolution) return

    const left = Math.min(selection.start.x, selection.end.x)
    const top = Math.min(selection.start.y, selection.end.y)
    const right = Math.max(selection.start.x, selection.end.x)
    const bottom = Math.max(selection.start.y, selection.end.y)
    const x = Math.min(resolution.width - 1, Math.floor(left * resolution.width))
    const y = Math.min(resolution.height - 1, Math.floor(top * resolution.height))
    const rightEdge = Math.min(resolution.width, Math.ceil(right * resolution.width))
    const bottomEdge = Math.min(resolution.height, Math.ceil(bottom * resolution.height))

    onCropComplete({
      x,
      y,
      w: Math.max(1, rightEdge - x),
      h: Math.max(1, bottomEdge - y),
    })
  }

  function handleReset() {
    dragStartRef.current = null
    setIsDragging(false)
    setSelection(null)
  }

  const rectangle = selection
    ? {
        left: Math.min(selection.start.x, selection.end.x) * 100,
        top: Math.min(selection.start.y, selection.end.y) * 100,
        width: Math.abs(selection.end.x - selection.start.x) * 100,
        height: Math.abs(selection.end.y - selection.start.y) * 100,
      }
    : null
  const verticalFrame = selection && resolution
    ? (() => {
        const frameWidth = Math.min(resolution.height * 9 / 16, resolution.width)
        const selectedCenter = ((selection.start.x + selection.end.x) / 2) * resolution.width
        const frameX = Math.min(
          Math.max(selectedCenter - frameWidth / 2, 0),
          resolution.width - frameWidth,
        )
        return { left: frameX / resolution.width * 100, width: frameWidth / resolution.width * 100 }
      })()
    : null
  const hasArea = Boolean(selection && selection.start.x !== selection.end.x && selection.start.y !== selection.end.y)

  return (
    <section className="crop-selector" aria-label="Seleção do recorte da câmera">
      <p className="crop-selector-hint">
        Marque a câmera. O quadro vertical usa toda a altura do vídeo e mantém o conteúdo acima e abaixo dela.
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
      <div className={`crop-selector-image ${isDragging ? 'is-dragging' : ''}`}>
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
        {rectangle && (
          <div
            className="crop-selector-rectangle"
            aria-hidden="true"
            style={{
              left: `${rectangle.left}%`,
              top: `${rectangle.top}%`,
              width: `${rectangle.width}%`,
              height: `${rectangle.height}%`,
            }}
          />
        )}
      </div>
      <div className="crop-selector-actions">
        <button type="button" className="primary-button" disabled={!hasArea || !resolution} onClick={handleConfirm}>
          Confirmar Enquadramento da Câmera
        </button>
        <button type="button" className="secondary-button" disabled={!selection} onClick={handleReset}>
          Resetar Seleção
        </button>
      </div>
    </section>
  )
}

export default CropSelector
