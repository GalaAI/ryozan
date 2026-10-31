/**
 * FlashManager - Hustle flash sheet upload, AI segmentation, pricing, publishing
 *
 * Flow:
 * 1. Artist uploads a flash sheet image
 * 2. Artist can frame the useful sheet area before Xar segments individual designs
 * 3. Artist reviews segments: accept, adjust, delete, name, price each
 * 4. Artist publishes designs to storefront
 *
 * Artist corrections on segments feed into active learning (feedback_samples table)
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { Check, ImagePlus, RefreshCw, ScanLine, Tags, Trash2, UploadCloud, X } from 'lucide-react'
import {
  ApiError,
  uploadFlashSheet,
  identifyFlashSheet,
  publishFlashSheetCatalog,
  pushFlashTrainingManifestToApex,
  segmentFlashSheet,
  saveFlashSheetCrop,
  listFlashSheets,
  listSheetDesigns,
  updateFlashDesign,
  deleteFlashDesign,
  deleteFlashSheet,
  type FlashSheet,
  type FlashManagerDesign,
  type FlashDesignUpdate,
} from '../../lib/api'
import { useAuthStore } from '../../stores/auth'
import { useStudioSettings } from '../../hooks/useStudioSettings'
import { sampleFlashUrl } from '../../lib/studio-settings'

const STYLE_OPTIONS = [
  'traditional', 'japanese', 'neo-traditional', 'realism',
  'blackwork', 'fine-line', 'watercolor', 'tribal',
  'lettering', 'geometric', 'illustrative', 'other',
]

// Client-side gate. The backend re-verifies bytes with Pillow, so this is only a fast
// pre-submit check to give the artist an immediate, specific message instead of a round-trip.
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024
const MAX_UPLOAD_LABEL = '20 MB'
const ACCEPTED_MIME = new Set(['image/jpeg', 'image/png', 'image/webp'])
const ACCEPTED_EXTENSIONS = new Set(['jpg', 'jpeg', 'png', 'webp'])
// HEIC is called out separately because it is the common iPhone default and needs a
// convert-first message rather than a generic "unsupported format".
const HEIC_EXTENSIONS = new Set(['heic', 'heif'])

function fileExtension(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : ''
}

/**
 * Validate a picked file before upload. Returns an error message string, or null if OK.
 * Mobile Safari sometimes reports an empty file.type; when the MIME is missing we fall
 * back to the extension so a valid JPEG/PNG/WebP is not silently dropped.
 */
function validatePickedFile(file: File): string | null {
  const ext = fileExtension(file.name)
  const mime = file.type || ''

  if (HEIC_EXTENSIONS.has(ext) || mime === 'image/heic' || mime === 'image/heif') {
    return 'HEIC images are not supported yet. Upload a JPEG, PNG, or WebP (on iPhone, use "Most Compatible" or export as JPEG).'
  }

  const mimeOk = mime ? ACCEPTED_MIME.has(mime) : false
  const extOk = ACCEPTED_EXTENSIONS.has(ext)
  // Accept when either signal is valid. Reject only when both are present and wrong,
  // or when neither can be verified.
  if (!mimeOk && !extOk) {
    return 'Unsupported format. Upload a JPEG, PNG, or WebP image.'
  }

  if (file.size === 0) {
    return 'That file is empty. Pick a JPEG, PNG, or WebP image.'
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return `That file is too large. Uploads must be ${MAX_UPLOAD_LABEL} or smaller.`
  }
  return null
}

// Local development only: stand-in previews from the studio's SAMPLE_FLASH_URLS.
const ENABLE_VISUAL_TEST_FLASH_FALLBACK = import.meta.env.DEV

interface FlashManagerProps {
  artistId?: string
  compact?: boolean
  stage?: FlashStage
  onStageChange?: (stage: FlashStage) => void
  stageLabels?: Partial<Record<FlashStage, string>>
  mode?: 'advanced' | 'sheet-publish'
}

export type FlashStage = 'upload' | 'segment' | 'enrich' | 'publish'

interface CropBox {
  x: number
  y: number
  w: number
  h: number
}

type CropHandle = 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w' | 'nw'

interface CropInteraction {
  mode: 'new' | 'move' | 'resize'
  start: { x: number; y: number }
  initial: CropBox
  handle?: CropHandle
}

const FLASH_PROCESS_TABS: Array<{ id: FlashStage; label: string }> = [
  { id: 'upload', label: 'Upload' },
  { id: 'segment', label: 'Segment' },
  { id: 'enrich', label: 'Enrich' },
  { id: 'publish', label: 'Publish' },
]

function isTolerableDeleteMiss(error: unknown) {
  return error instanceof ApiError && (error.status === 404 || error.status === 405)
}

/**
 * Map an upload failure to a specific, user-facing message. Distinguishes the failure
 * modes the artist can act on: unsupported format, too large, auth, API unreachable,
 * and storage unavailable. Falls back to the server message, then a generic string.
 */
function uploadErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    // Backend structured codes take priority. They are the authoritative reason.
    switch (error.code) {
      case 'FLASH_UNSUPPORTED_FORMAT':
      case 'FLASH_UNREADABLE_IMAGE':
      case 'FLASH_IMAGE_TOO_LARGE':
      case 'FLASH_FILE_TOO_LARGE':
      case 'FLASH_EMPTY_FILE':
        return error.message
      case 'FLASH_STORAGE_UNAVAILABLE':
        return 'Upload saved nothing: storage is temporarily unavailable. Please try again shortly.'
      case 'FLASH_API_UNAVAILABLE':
        return 'Could not reach the server. Check your connection and try again.'
    }
    if (error.status === 401 || error.status === 403) {
      return 'Your session expired or you are not allowed to upload here. Sign in again and retry.'
    }
    if (error.status === 0) {
      return 'Could not reach the server. Check your connection and try again.'
    }
    if (error.status >= 500) {
      return 'The server had a problem saving your sheet. Please try again shortly.'
    }
    return error.message
  }
  return error instanceof Error ? error.message : 'An error occurred'
}

function FlashSheetImage({
  imageUrl,
  title,
  index = 0,
  full = false,
  onImageLoad,
}: {
  imageUrl: string | null
  title: string | null
  index?: number
  full?: boolean
  onImageLoad?: (size: { width: number; height: number }) => void
}) {
  const [fallbackActive, setFallbackActive] = useState(false)
  const [failed, setFailed] = useState(false)
  const { settings: studioSettings } = useStudioSettings()
  const fallbackSrc = ENABLE_VISUAL_TEST_FLASH_FALLBACK ? sampleFlashUrl(studioSettings, index) : null
  const src = fallbackActive && fallbackSrc ? fallbackSrc : imageUrl

  if (!src || failed) {
    return (
      <div className={full ? 'flash-sheet-placeholder flash-sheet-placeholder--full' : 'flash-sheet-placeholder'}>
        {src ? 'Preview unavailable' : 'No preview'}
      </div>
    )
  }

  const image = (
    <img
      src={src}
      alt={title || 'Flash sheet'}
      className={full ? 'flash-sheet-full' : 'flash-sheet-thumb'}
      referrerPolicy="no-referrer"
      loading={full ? "eager" : "lazy"}
      decoding="async"
      onLoad={(event) => onImageLoad?.({
        width: event.currentTarget.naturalWidth,
        height: event.currentTarget.naturalHeight,
      })}
      onError={() => {
        if (!fallbackActive && fallbackSrc) {
          setFallbackActive(true)
          return
        }
        setFailed(true)
      }}
    />
  )

  if (full) {
    return (
      <>
        {image}
        {fallbackActive && <span className="flash-preview-fallback-badge">Test preview</span>}
      </>
    )
  }

  return (
    <div className="flash-sheet-thumb-wrap">
      {image}
      {fallbackActive && <span className="flash-preview-fallback-badge">Test preview</span>}
    </div>
  )
}

export default function FlashManager({ artistId, compact = false, stage, onStageChange, stageLabels, mode = 'advanced' }: FlashManagerProps) {
  const sheetPublishMode = mode === 'sheet-publish'
  const [sheets, setSheets] = useState<FlashSheet[]>([])
  const [selectedSheet, setSelectedSheet] = useState<FlashSheet | null>(null)
  const [designs, setDesigns] = useState<FlashManagerDesign[]>([])
  const [pendingUpload, setPendingUpload] = useState<{ file: File; previewUrl: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const [segmenting, setSegmenting] = useState(false)
  const [identifying, setIdentifying] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [savingCrop, setSavingCrop] = useState(false)
  const [sendingTrainingData, setSendingTrainingData] = useState(false)
  const [trainingReceipt, setTrainingReceipt] = useState<string | null>(null)
  const [deletingDesignId, setDeletingDesignId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [workflowStage, setWorkflowStage] = useState<FlashStage>('upload')
  const [cropMode, setCropMode] = useState(false)
  const [cropDraft, setCropDraft] = useState<CropBox | null>(null)
  const [sheetSourceSize, setSheetSourceSize] = useState<{ width: number; height: number } | null>(null)
  const uploadInputRef = useRef<HTMLInputElement>(null)
  const sheetCanvasRef = useRef<HTMLDivElement>(null)
  const cropInteractionRef = useRef<CropInteraction | null>(null)
  const cropDraftRef = useRef<CropBox | null>(null)
  const artistScopeVersionRef = useRef(0)
  const user = useAuthStore((state) => state.user)
  const canManageTrainingData = user?.role === 'founder' || user?.role === 'admin'

  // Upload state
  const [dragOver, setDragOver] = useState(false)

  useEffect(() => {
    const scopeVersion = ++artistScopeVersionRef.current
    setSheets([])
    setSelectedSheet(null)
    setDesigns([])
    setWorkflowStage('upload')
    setSegmenting(false)
    setIdentifying(false)
    setPublishing(false)
    setSavingCrop(false)
    setDeletingDesignId(null)
    setLoading(true)
    setError(null)

    void listFlashSheets(0, 20, artistId)
      .then((result) => {
        if (scopeVersion !== artistScopeVersionRef.current) return
        setSheets(result.sheets)
      })
      .catch((e: unknown) => {
        if (scopeVersion !== artistScopeVersionRef.current) return
        setError(e instanceof Error ? e.message : 'An error occurred')
      })
      .finally(() => {
        if (scopeVersion !== artistScopeVersionRef.current) return
        setLoading(false)
      })
  }, [artistId])

  useEffect(() => {
    onStageChange?.(workflowStage)
  }, [onStageChange, workflowStage])

  // Apply the parent-controlled stage ONLY when the parent actually changes it
  // (e.g. a stage tab click), not on every transient divergence from the
  // internal workflowStage. Depending on workflowStage here caused an
  // oscillation that cleared selectedSheet and strobed the stage pills, so
  // opening a sheet's review view was impossible while the parent held 'upload'.
  const prevStageRef = useRef(stage)
  useEffect(() => {
    if (stage && stage !== prevStageRef.current) {
      setWorkflowStage(stage)
      if (stage === 'upload') {
        setSelectedSheet(null)
        setDesigns([])
      }
    }
    prevStageRef.current = stage
  }, [stage])

  useEffect(() => {
    return () => {
      if (pendingUpload?.previewUrl) URL.revokeObjectURL(pendingUpload.previewUrl)
    }
  }, [pendingUpload?.previewUrl])

  useEffect(() => {
    cropDraftRef.current = cropDraft
  }, [cropDraft])

  const loadDesigns = useCallback(async (
    sheetId: string,
    scopeVersion = artistScopeVersionRef.current,
  ): Promise<FlashManagerDesign[] | null> => {
    try {
      const result = await listSheetDesigns(sheetId)
      if (scopeVersion !== artistScopeVersionRef.current) return null
      setDesigns(result.designs)
      return result.designs
    } catch (e: unknown) {
      if (scopeVersion !== artistScopeVersionRef.current) return null
      setError(e instanceof Error ? e.message : 'An error occurred')
      return null
    }
  }, [])

  const handlePrepareUpload = useCallback((file: File) => {
    const validationError = validatePickedFile(file)
    if (validationError) {
      // Surface the reason instead of silently dropping the file, and keep any prior
      // pending upload so the artist does not lose their earlier selection.
      setError(validationError)
      return
    }
    setPendingUpload(prev => {
      if (prev?.previewUrl) URL.revokeObjectURL(prev.previewUrl)
      return { file, previewUrl: URL.createObjectURL(file) }
    })
    setError(null)
    setWorkflowStage('upload')
  }, [])

  const handleReplaceUpload = useCallback(() => {
    setPendingUpload(prev => {
      if (prev?.previewUrl) URL.revokeObjectURL(prev.previewUrl)
      return null
    })
  }, [])

  const handleUpload = useCallback(async (file: File) => {
    const scopeVersion = artistScopeVersionRef.current
    setLoading(true)
    setError(null)
    try {
      const sheet = await uploadFlashSheet(file, file.name.replace(/\.[^.]+$/, ''), artistId)
      if (scopeVersion !== artistScopeVersionRef.current) return
      setSheets(prev => [sheet, ...prev])
      setSheetSourceSize(null)
      setSelectedSheet(sheet)
      // Only clear the pending file after a confirmed success, so a failed upload keeps
      // the selection and the artist can retry without re-picking.
      setPendingUpload(prev => {
        if (prev?.previewUrl) URL.revokeObjectURL(prev.previewUrl)
        return null
      })
      setWorkflowStage(sheetPublishMode ? 'enrich' : 'segment')
    } catch (e: unknown) {
      if (scopeVersion !== artistScopeVersionRef.current) return
      setError(uploadErrorMessage(e))
      // Intentionally leave pendingUpload set so "Confirm Sheet" can be retried.
    } finally {
      if (scopeVersion === artistScopeVersionRef.current) setLoading(false)
    }
  }, [artistId, sheetPublishMode])

  const handleIdentify = useCallback(async () => {
    if (!selectedSheet) return
    const scopeVersion = artistScopeVersionRef.current
    setIdentifying(true)
    setError(null)
    setWorkflowStage('enrich')
    try {
      const result = await identifyFlashSheet(selectedSheet.id)
      if (scopeVersion !== artistScopeVersionRef.current) return
      setSelectedSheet(result.sheet)
      setSheets(prev => prev.map(sheet => sheet.id === result.sheet.id ? result.sheet : sheet))
      setDesigns(result.designs)
      setWorkflowStage('publish')
    } catch (e: unknown) {
      if (scopeVersion !== artistScopeVersionRef.current) return
      setError(e instanceof Error ? e.message : 'Could not identify this sheet')
    } finally {
      if (scopeVersion === artistScopeVersionRef.current) setIdentifying(false)
    }
  }, [selectedSheet])

  const handlePublishCatalog = useCallback(async () => {
    if (!selectedSheet) return
    const scopeVersion = artistScopeVersionRef.current
    setPublishing(true)
    setError(null)
    try {
      const result = await publishFlashSheetCatalog(selectedSheet.id)
      if (scopeVersion !== artistScopeVersionRef.current) return
      setSelectedSheet(result.sheet)
      setSheets(prev => prev.map(sheet => sheet.id === result.sheet.id ? result.sheet : sheet))
      setDesigns(result.designs)
      setWorkflowStage('publish')
    } catch (e: unknown) {
      if (scopeVersion !== artistScopeVersionRef.current) return
      setError(e instanceof Error ? e.message : 'Could not publish this flash catalog')
    } finally {
      if (scopeVersion === artistScopeVersionRef.current) setPublishing(false)
    }
  }, [selectedSheet])

  const handleSegment = useCallback(async () => {
    if (!selectedSheet) return
    const scopeVersion = artistScopeVersionRef.current
    const sheetId = selectedSheet.id
    setSegmenting(true)
    setError(null)
    setWorkflowStage('segment')
    try {
      const result = await segmentFlashSheet(sheetId)
      if (scopeVersion !== artistScopeVersionRef.current) return
      // Reload designs after segmentation
      const refreshedDesigns = await loadDesigns(sheetId, scopeVersion)
      if (refreshedDesigns === null || scopeVersion !== artistScopeVersionRef.current) return
      // Update sheet in list
      setSelectedSheet(prev => prev?.id === sheetId ? { ...prev, status: 'segmented', design_count: result.design_count } : prev)
      setSheets(prev => prev.map(s => s.id === sheetId ? { ...s, status: 'segmented', design_count: result.design_count } : s))
      setWorkflowStage('enrich')
    } catch (e: unknown) {
      if (scopeVersion !== artistScopeVersionRef.current) return
      setError(e instanceof Error ? e.message : 'An error occurred')
    } finally {
      if (scopeVersion === artistScopeVersionRef.current) setSegmenting(false)
    }
  }, [selectedSheet, loadDesigns])

  const cropPoint = useCallback((clientX: number, clientY: number) => {
    const rect = sheetCanvasRef.current?.getBoundingClientRect()
    if (!rect || rect.width === 0 || rect.height === 0) return null
    return {
      x: Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100)),
      y: Math.max(0, Math.min(100, ((clientY - rect.top) / rect.height) * 100)),
    }
  }, [])

  const updateCropInteraction = useCallback((clientX: number, clientY: number) => {
    const interaction = cropInteractionRef.current
    const point = cropPoint(clientX, clientY)
    if (!interaction || !point) return

    if (interaction.mode === 'new') {
      const nextCrop = {
        x: Math.min(interaction.start.x, point.x),
        y: Math.min(interaction.start.y, point.y),
        w: Math.abs(point.x - interaction.start.x),
        h: Math.abs(point.y - interaction.start.y),
      }
      cropDraftRef.current = nextCrop
      setCropDraft(nextCrop)
      return
    }

    if (interaction.mode === 'move') {
      const x = Math.max(0, Math.min(100 - interaction.initial.w, interaction.initial.x + point.x - interaction.start.x))
      const y = Math.max(0, Math.min(100 - interaction.initial.h, interaction.initial.y + point.y - interaction.start.y))
      const nextCrop = { ...interaction.initial, x, y }
      cropDraftRef.current = nextCrop
      setCropDraft(nextCrop)
      return
    }

    const handle = interaction.handle ?? 'se'
    const left = handle.includes('w') ? Math.min(point.x, interaction.initial.x + interaction.initial.w - 2) : interaction.initial.x
    const right = handle.includes('e') ? Math.max(point.x, interaction.initial.x + 2) : interaction.initial.x + interaction.initial.w
    const top = handle.includes('n') ? Math.min(point.y, interaction.initial.y + interaction.initial.h - 2) : interaction.initial.y
    const bottom = handle.includes('s') ? Math.max(point.y, interaction.initial.y + 2) : interaction.initial.y + interaction.initial.h
    const nextCrop = { x: left, y: top, w: right - left, h: bottom - top }
    cropDraftRef.current = nextCrop
    setCropDraft(nextCrop)
  }, [cropPoint])

  const finishCropInteraction = useCallback(() => {
    cropInteractionRef.current = null
    const finalCrop = cropDraftRef.current
    if (!finalCrop || finalCrop.w < 2 || finalCrop.h < 2) {
      setError('Frame the usable flash sheet area before Xar segments it.')
      return
    }
    setError(null)
    setWorkflowStage('segment')
  }, [])

  const beginCropInteraction = useCallback((
    event: React.PointerEvent<HTMLElement>,
    mode: CropInteraction['mode'],
    handle?: CropHandle,
  ) => {
    if (!cropMode) return
    const point = cropPoint(event.clientX, event.clientY)
    if (!point) return
    event.preventDefault()
    event.stopPropagation()
    sheetCanvasRef.current?.setPointerCapture(event.pointerId)
    const initial = cropDraft ?? { x: point.x, y: point.y, w: 0, h: 0 }
    cropInteractionRef.current = { mode, start: point, initial, handle }
    if (mode === 'new') setCropDraft(initial)
  }, [cropDraft, cropMode, cropPoint])

  const confirmCrop = useCallback(async () => {
    if (!selectedSheet) return
    if (!cropDraft || cropDraft.w < 2 || cropDraft.h < 2) {
      setError('Frame the usable flash sheet area before Xar segments it.')
      return
    }
    const scopeVersion = artistScopeVersionRef.current
    cropInteractionRef.current = null
    setSavingCrop(true)
    setError(null)
    try {
      const updated = await saveFlashSheetCrop(selectedSheet.id, {
        crop_x: cropDraft.x,
        crop_y: cropDraft.y,
        crop_w: cropDraft.w,
        crop_h: cropDraft.h,
      })
      if (scopeVersion !== artistScopeVersionRef.current) return
      setSelectedSheet(updated)
      setSheets(prev => prev.map(sheet => sheet.id === updated.id ? updated : sheet))
      setSheetSourceSize(null)
      setCropDraft(null)
      setCropMode(false)
      setWorkflowStage('segment')
    } catch (e: unknown) {
      if (scopeVersion !== artistScopeVersionRef.current) return
      setError(e instanceof Error ? e.message : 'Could not save that sheet crop')
    } finally {
      if (scopeVersion === artistScopeVersionRef.current) setSavingCrop(false)
    }
  }, [cropDraft, selectedSheet])

  const handleUseFullSheet = useCallback(async () => {
    if (!selectedSheet) return
    const scopeVersion = artistScopeVersionRef.current
    setSavingCrop(true)
    setError(null)
    try {
      const updated = await saveFlashSheetCrop(selectedSheet.id, {
        crop_x: 0,
        crop_y: 0,
        crop_w: 100,
        crop_h: 100,
      })
      if (scopeVersion !== artistScopeVersionRef.current) return
      setSelectedSheet(updated)
      setSheets(prev => prev.map(sheet => sheet.id === updated.id ? updated : sheet))
      setSheetSourceSize(null)
      setCropDraft(null)
      setCropMode(false)
    } catch (e: unknown) {
      if (scopeVersion !== artistScopeVersionRef.current) return
      setError(e instanceof Error ? e.message : 'Could not restore the full sheet')
    } finally {
      if (scopeVersion === artistScopeVersionRef.current) setSavingCrop(false)
    }
  }, [selectedSheet])

  const handleDesignUpdate = useCallback(async (designId: string, data: FlashDesignUpdate): Promise<boolean> => {
    const scopeVersion = artistScopeVersionRef.current
    setError(null)
    try {
      const updated = await updateFlashDesign(designId, data)
      if (scopeVersion !== artistScopeVersionRef.current) return false
      setDesigns(prev => prev.map(d => d.id === designId ? updated : d))
      setWorkflowStage(sheetPublishMode || data.status === 'published' ? 'publish' : 'enrich')
      return true
    } catch (e: unknown) {
      if (scopeVersion !== artistScopeVersionRef.current) return false
      setError(e instanceof Error ? e.message : 'An error occurred')
      return false
    }
  }, [sheetPublishMode])

  const handleDeleteDesign = useCallback(async (design: FlashManagerDesign) => {
    if (design.status === 'claimed' || design.claimed_by) {
      setError('Claimed designs cannot be deleted.')
      return
    }
    const label = design.name || design.ai_suggested_name || 'this design'
    if (!window.confirm(`Delete ${label}? The source sheet will be preserved.`)) return

    const scopeVersion = artistScopeVersionRef.current
    const previousDesigns = designs
    const nextCount = Math.max(0, designs.length - 1)
    setDeletingDesignId(design.id)
    setError(null)
    setDesigns(prev => prev.filter(item => item.id !== design.id))
    setSelectedSheet(prev => prev ? { ...prev, design_count: nextCount } : prev)
    setSheets(prev => prev.map(sheet => sheet.id === design.sheet_id
      ? { ...sheet, design_count: nextCount }
      : sheet))
    try {
      await deleteFlashDesign(design.id)
    } catch (e: unknown) {
      if (scopeVersion !== artistScopeVersionRef.current) return
      setDesigns(previousDesigns)
      setSelectedSheet(prev => prev ? { ...prev, design_count: previousDesigns.length } : prev)
      setSheets(prev => prev.map(sheet => sheet.id === design.sheet_id
        ? { ...sheet, design_count: previousDesigns.length }
        : sheet))
      setError(e instanceof Error ? e.message : 'Could not delete design')
    } finally {
      if (scopeVersion === artistScopeVersionRef.current) setDeletingDesignId(null)
    }
  }, [designs])

  const handlePushTrainingData = useCallback(async () => {
    setSendingTrainingData(true)
    setError(null)
    setTrainingReceipt(null)
    try {
      const receipt = await pushFlashTrainingManifestToApex()
      setTrainingReceipt(
        `${receipt.sample_count} reviewed masks saved to Apex. No training job was started.`,
      )
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not send reviewed masks to Apex')
    } finally {
      setSendingTrainingData(false)
    }
  }, [])

  const handleSelectSheet = useCallback(async (sheet: FlashSheet) => {
    const scopeVersion = artistScopeVersionRef.current
    setSheetSourceSize(null)
    setCropMode(false)
    setCropDraft(null)
    setDesigns([])
    setSelectedSheet(sheet)
    setWorkflowStage(sheetPublishMode ? (sheet.status === 'published' ? 'publish' : 'enrich') : 'segment')
    const loadedDesigns = await loadDesigns(sheet.id, scopeVersion)
    if (scopeVersion !== artistScopeVersionRef.current || loadedDesigns === null) return
    if (sheetPublishMode && loadedDesigns.length > 0) setWorkflowStage('publish')
  }, [loadDesigns, sheetPublishMode])

  const handleDeleteSheet = useCallback(async (sheet: FlashSheet) => {
    const scopeVersion = artistScopeVersionRef.current
    setLoading(true)
    setError(null)
    const wasSelected = selectedSheet?.id === sheet.id
    setSheets(prev => prev.filter(s => s.id !== sheet.id))
    setSelectedSheet(prev => prev?.id === sheet.id ? null : prev)
    if (wasSelected) {
      setDesigns([])
      setWorkflowStage('upload')
    }
    try {
      await deleteFlashSheet(sheet.id)
    } catch (e: unknown) {
      if (scopeVersion !== artistScopeVersionRef.current) return
      if (isTolerableDeleteMiss(e)) return
      setSheets(prev => [sheet, ...prev])
      if (wasSelected) {
        setSelectedSheet(sheet)
        void loadDesigns(sheet.id)
      }
      setError(e instanceof Error ? e.message : 'Could not delete sheet')
    } finally {
      if (scopeVersion === artistScopeVersionRef.current) setLoading(false)
    }
  }, [loadDesigns, selectedSheet?.id])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file) handlePrepareUpload(file)
  }, [handlePrepareUpload])

  const handleFileInput = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) handlePrepareUpload(file)
    e.target.value = ''
  }, [handlePrepareUpload])

  const catalogReady = designs.length > 0 && designs.every(design => (
    Boolean(design.name)
    && design.price !== null
    && Boolean(design.estimated_minutes)
    && design.deposit_amount !== null
  ))

  return (
    <div className={`flash-manager${compact ? ' flash-manager--compact' : ''}`}>
      {error && (
        <div className="flash-error">{error}<button onClick={() => setError(null)}>Dismiss</button></div>
      )}
      {trainingReceipt && (
        <div className="flash-review-note" role="status">
          {trainingReceipt}
          <button type="button" onClick={() => setTrainingReceipt(null)}>Dismiss</button>
        </div>
      )}

      {!(compact && sheetPublishMode) && (
        <div className="flash-top-workflow" aria-label="Flash sheet workflow">
          {FLASH_PROCESS_TABS.slice(0, 3).map((tab) => (
            <span key={tab.id} className={workflowStage === tab.id ? 'active' : ''}>
              {tab.id === 'upload' && <UploadCloud size={14} aria-hidden="true" />}
              {tab.id === 'segment' && <ScanLine size={14} aria-hidden="true" />}
              {(tab.id === 'enrich' || tab.id === 'publish') && <Tags size={14} aria-hidden="true" />}
              {stageLabels?.[tab.id] ?? tab.label}
            </span>
          ))}
          {loading && <p className="flash-uploading">Uploading...</p>}
        </div>
      )}
      {compact && sheetPublishMode && loading ? <p className="flash-uploading">Uploading...</p> : null}

      {!selectedSheet ? (
        /* ── Sheet List + Upload ──────────────────── */
        <div className="flash-sheets-view">
          <div className="flash-inventory-workbench">
            <div
              className={`flash-upload-zone ${compact ? 'flash-upload-zone--compact' : ''} ${dragOver ? 'drag-over' : ''} ${!pendingUpload ? 'is-clickable' : ''}`}
              onDragOver={e => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => {
                if (!pendingUpload && !loading) uploadInputRef.current?.click()
              }}
              onKeyDown={(event) => {
                if (pendingUpload || loading || (event.key !== 'Enter' && event.key !== ' ')) return
                event.preventDefault()
                uploadInputRef.current?.click()
              }}
              role={!pendingUpload ? 'button' : undefined}
              tabIndex={!pendingUpload ? 0 : undefined}
              aria-label={!pendingUpload ? 'Upload a flash sheet' : undefined}
            >
              <input
                ref={uploadInputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"
                onChange={handleFileInput}
                hidden
              />
              <div className={`flash-upload-hero ${pendingUpload ? 'flash-upload-hero--preview' : ''}`}>
                <div className="flash-upload-icon" aria-hidden="true">
                  {pendingUpload ? (
                    <img src={pendingUpload.previewUrl} alt="" />
                  ) : (
                    <ImagePlus size={24} strokeWidth={1.9} />
                  )}
                </div>
                <div className="flash-upload-copy">
                  <p>{pendingUpload ? 'Confirm this sheet' : 'Drop a flash sheet'}</p>
                  <span className="flash-upload-or">
                    {pendingUpload ? pendingUpload.file.name : `JPG, PNG, or WebP · up to ${MAX_UPLOAD_LABEL}`}
                  </span>
                </div>
              </div>
              {pendingUpload ? (
                <div className="flash-upload-confirm-actions">
                  <button
                    type="button"
                    className="flash-upload-btn flash-upload-btn--ghost"
                    onClick={handleReplaceUpload}
                    disabled={loading}
                  >
                    <RefreshCw size={14} aria-hidden="true" />
                    Replace
                  </button>
                  <button
                    type="button"
                    className="flash-upload-btn flash-upload-btn--primary"
                    onClick={() => handleUpload(pendingUpload.file)}
                    disabled={loading}
                  >
                    <Check size={14} aria-hidden="true" />
                    Confirm Sheet
                  </button>
                </div>
              ) : null}
            </div>

            {sheets.length > 0 && (
              <div className="flash-gallery-lane">
                <div className="flash-sheet-grid" role="region" aria-label="Uploaded flash sheets">
                  {sheets.map((sheet, index) => (
                    <div key={sheet.id} className="flash-sheet-card">
                      <button
                        type="button"
                        className="flash-sheet-open"
                        onPointerDown={(event) => {
                          event.preventDefault()
                          void handleSelectSheet(sheet)
                        }}
                        onKeyDown={(event) => {
                          if (event.key !== 'Enter' && event.key !== ' ') return
                          event.preventDefault()
                          void handleSelectSheet(sheet)
                        }}
                        aria-label={`Open ${sheet.title || 'flash sheet'} for ${sheetPublishMode ? 'publishing' : 'segmentation'}`}
                      >
                        <FlashSheetImage imageUrl={sheet.image_url} title={sheet.title} index={index} />
                        <span className="flash-sheet-title">{sheet.title || 'Untitled'}</span>
                        <span className="flash-sheet-meta">
                          {sheet.design_count} designs
                          <span className={`flash-status flash-status-${sheet.status}`}>{sheet.status}</span>
                        </span>
                      </button>
                      <button
                        type="button"
                        className="flash-sheet-delete"
                        onClick={() => handleDeleteSheet(sheet)}
                        disabled={loading}
                        aria-label={`Delete ${sheet.title || 'flash sheet'}`}
                        title="Delete sheet"
                      >
                        <X size={14} strokeWidth={2.2} aria-hidden="true" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      ) : (
        /* ── Design Review ────────────────────────── */
        <div className="flash-review-view">
          <div className="flash-header">
            <h2>{selectedSheet.title || 'Flash Sheet'}</h2>
            <div className="flash-header-actions">
              <button className="flash-back" onClick={() => { setSelectedSheet(null); setDesigns([]); setCropMode(false); setCropDraft(null); setWorkflowStage('upload') }}>
                Back
              </button>
              {sheetPublishMode ? (
                workflowStage === 'enrich' ? (
                  <button className="flash-segment-btn" onClick={handleIdentify} disabled={identifying}>
                    {identifying && <ScanLine className="flash-scan-button-icon" size={13} aria-hidden="true" />}
                    {identifying ? 'Scanning...' : 'Scan'}
                  </button>
                ) : null
              ) : <button className="flash-segment-btn" onClick={handleSegment} disabled={segmenting || cropMode || savingCrop}>
                {segmenting ? 'Segmenting...' : selectedSheet.status === 'uploaded' ? 'AI Segment' : 'Re-segment'}
              </button>}
              {!sheetPublishMode && <>
              {canManageTrainingData && (
                <button
                  className="flash-segment-btn"
                  onClick={() => void handlePushTrainingData()}
                  disabled={sendingTrainingData}
                  title="Save the current reviewed-mask manifest in Ark Apex. This does not start paid training."
                >
                  <UploadCloud size={13} aria-hidden="true" />
                  {sendingTrainingData ? 'Saving dataset...' : 'Send reviews to Apex'}
                </button>
              )}
              <button
                className="flash-segment-btn"
                onClick={() => {
                  if (cropMode) {
                    void confirmCrop()
                    return
                  }
                  setCropDraft(selectedSheet.crop_applied ? {
                    x: selectedSheet.crop_x,
                    y: selectedSheet.crop_y,
                    w: selectedSheet.crop_w,
                    h: selectedSheet.crop_h,
                  } : { x: 5, y: 5, w: 90, h: 90 })
                  setError(null)
                  setCropMode(true)
                }}
                disabled={loading || segmenting || savingCrop || selectedSheet.design_count > 0}
                aria-pressed={cropMode}
                title={selectedSheet.design_count > 0 ? 'Remove generated designs before changing the sheet crop' : undefined}
              >
                {savingCrop ? 'Saving crop...' : cropMode ? 'Save crop' : selectedSheet.crop_applied ? 'Adjust crop' : 'Crop sheet'}
              </button>
              {selectedSheet.crop_applied && !cropMode ? (
                <button
                  className="flash-segment-btn"
                  onClick={() => void handleUseFullSheet()}
                  disabled={loading || segmenting || savingCrop || selectedSheet.design_count > 0}
                >
                  {savingCrop ? 'Restoring...' : 'Use full sheet'}
                </button>
              ) : null}
              </>}
            </div>
          </div>

          {/* Original sheet image with overlay boxes */}
          <div className={`flash-review-layout${sheetPublishMode ? ` is-${workflowStage === 'publish' ? 'publish' : 'identify'}` : ''}`}>
            {(!sheetPublishMode || workflowStage !== 'publish') && (
            <div className={`flash-sheet-preview ${cropMode ? 'is-cropping' : ''} ${identifying ? 'is-scanning' : ''}`}>
              <div
                className="flash-sheet-canvas"
                ref={sheetCanvasRef}
                onPointerDown={(event) => {
                  if (!cropMode) return
                  beginCropInteraction(event, 'new')
                }}
                onPointerMove={(event) => {
                  if (!cropMode || !cropInteractionRef.current) return
                  updateCropInteraction(event.clientX, event.clientY)
                }}
                onPointerUp={() => { if (cropMode) finishCropInteraction() }}
                onPointerCancel={() => { cropInteractionRef.current = null }}
              >
                <FlashSheetImage
                  imageUrl={cropMode ? selectedSheet.source_image_url : selectedSheet.image_url}
                  title={selectedSheet.title}
                  full
                  onImageLoad={setSheetSourceSize}
                />
                {!sheetPublishMode && !cropMode && designs.map(d => {
                  const legacyPixel = d.crop_units === 'pixel'
                  const crop = legacyPixel && sheetSourceSize
                    ? {
                        x: d.crop_x / sheetSourceSize.width * 100,
                        y: d.crop_y / sheetSourceSize.height * 100,
                        w: d.crop_w / sheetSourceSize.width * 100,
                        h: d.crop_h / sheetSourceSize.height * 100,
                      }
                    : { x: d.crop_x, y: d.crop_y, w: d.crop_w, h: d.crop_h }
                  return (
                    <div
                      key={d.id}
                      className={`flash-design-overlay ${d.status}`}
                      style={{
                        left: `${crop.x}%`,
                        top: `${crop.y}%`,
                        width: `${crop.w}%`,
                        height: `${crop.h}%`,
                        visibility: legacyPixel && !sheetSourceSize ? 'hidden' : 'visible',
                      }}
                      title={d.name || d.ai_suggested_name || 'Unnamed'}
                    />
                  )
                })}
                {cropDraft && cropMode && (
                  <div
                    className={`flash-crop-frame ${cropMode ? 'is-editing' : ''}`}
                    style={{
                      left: `${cropDraft.x}%`,
                      top: `${cropDraft.y}%`,
                      width: `${cropDraft.w}%`,
                      height: `${cropDraft.h}%`,
                    }}
                    onPointerDown={(event) => beginCropInteraction(event, 'move')}
                  />
                )}
                {cropDraft && cropMode ? (
                  <div
                    className="flash-crop-controls"
                    style={{
                      left: `${cropDraft.x}%`,
                      top: `${cropDraft.y}%`,
                      width: `${cropDraft.w}%`,
                      height: `${cropDraft.h}%`,
                    }}
                    aria-label="Freeform crop handles"
                  >
                    {(['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'] as CropHandle[]).map(handle => (
                      <button
                        key={handle}
                        type="button"
                        className={`flash-crop-handle is-${handle}`}
                        aria-label={`Resize crop ${handle}`}
                        onPointerDown={(event) => beginCropInteraction(event, 'resize', handle)}
                      />
                    ))}
                  </div>
                ) : null}
              </div>
              {identifying && (
                <div className="flash-scan-loader" role="status" aria-live="polite">
                  <span className="flash-scan-loader__beam" aria-hidden="true" />
                  <div className="flash-scan-loader__status">
                    <ScanLine size={28} strokeWidth={1.7} aria-hidden="true" />
                    <strong>Scanning flash sheet</strong>
                    <small>Identifying the design count and names</small>
                  </div>
                </div>
              )}
            </div>
            )}

            {/* Design list with pricing */}
            {(!sheetPublishMode || workflowStage === 'publish') && (
            <div className={`flash-design-list${sheetPublishMode ? ' flash-design-list--inventory' : ''}`}>
              <h3>{sheetPublishMode ? 'AI design list' : 'Designs'} ({designs.length})</h3>
              {designs.length === 0 && (
                <div className="flash-empty">{sheetPublishMode ? 'Scan the sheet to identify its designs.' : 'Click "AI Segment" to detect designs'}</div>
              )}
              {designs.length > 0 && !sheetPublishMode && (
                <p className="flash-review-note">
                  Segment gives each region a neutral Design number. Use Enrich to add the real title, price, time, and style before publishing.
                </p>
              )}
              {sheetPublishMode && designs.length > 0 && (
                <p className="flash-review-note">
                  {selectedSheet.status === 'published'
                    ? 'This audited catalog is live on the Flash Wall.'
                    : 'Audit every item, save its fields, then publish the catalog.'}
                </p>
              )}
              {sheetPublishMode ? (
                <div className="flash-inventory-track" aria-label="Identified flash designs">
                  {designs.map((d, index) => (
                    <DesignCard
                      key={d.id}
                      design={d}
                      inventoryMode
                      fallbackImageUrl={selectedSheet.image_url}
                      index={index}
                      onUpdate={(data) => handleDesignUpdate(d.id, data)}
                      onDelete={() => handleDeleteDesign(d)}
                      deleting={deletingDesignId === d.id}
                    />
                  ))}
                </div>
              ) : designs.map((d) => (
                <DesignCard
                  key={d.id}
                  design={d}
                  onUpdate={(data) => handleDesignUpdate(d.id, data)}
                  onDelete={() => handleDeleteDesign(d)}
                  deleting={deletingDesignId === d.id}
                />
              ))}
              {sheetPublishMode && designs.length > 0 ? (
                <button
                  type="button"
                  className="flash-publish-catalog"
                  onClick={handlePublishCatalog}
                  disabled={!catalogReady || publishing || selectedSheet.status === 'published'}
                >
                  {publishing ? 'Publishing...' : selectedSheet.status === 'published' ? 'Live on Flash Wall' : catalogReady ? 'Publish catalog' : 'Complete all fields'}
                </button>
              ) : null}
            </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

/* ── Design Card (inline edit) ──────────────────────── */

function DesignCard({
  design,
  onUpdate,
  onDelete,
  deleting,
  inventoryMode = false,
  fallbackImageUrl,
  index,
}: {
  design: FlashManagerDesign
  onUpdate: (data: FlashDesignUpdate) => Promise<boolean>
  onDelete: () => void
  deleting: boolean
  inventoryMode?: boolean
  fallbackImageUrl?: string
  index?: number
}) {
  const [editing, setEditing] = useState(inventoryMode && design.status !== 'published')
  const [name, setName] = useState(design.name || design.ai_suggested_name || '')
  const [price, setPrice] = useState(design.price?.toString() || '')
  const [minutes, setMinutes] = useState(design.estimated_minutes?.toString() || '')
  const [deposit, setDeposit] = useState(design.deposit_amount?.toString() || '')
  const [reclaimable, setReclaimable] = useState(design.reclaimable)
  const [style, setStyle] = useState(design.style || '')
  const [saving, setSaving] = useState(false)
  const depositEnabled = deposit.trim() !== '' && Number(deposit) > 0
  const previewUrl = design.preview_url || (inventoryMode ? design.image_url || fallbackImageUrl : null)
  const isEnriched = Boolean(
    design.name
    && design.price !== null
    && design.deposit_amount !== null
    && (!inventoryMode || design.estimated_minutes)
  )

  const save = async () => {
    setSaving(true)
    try {
      const didSave = await onUpdate({
        name,
        price: price.trim() ? parseFloat(price) : undefined,
        estimated_minutes: minutes.trim() ? parseInt(minutes) : undefined,
        deposit_amount: depositEnabled ? parseFloat(deposit) : 0,
        reclaimable,
        style,
      })
      if (didSave) setEditing(false)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={`flash-design-card ${design.status}${inventoryMode ? ' flash-design-card--inventory' : ''}`}>
      {previewUrl && (
        <div className="flash-design-cutout-preview">
          <img
            src={previewUrl}
            alt={`${design.name || design.ai_suggested_name || 'Design'} preview`}
          />
          {inventoryMode && index !== undefined ? <span>{String(index + 1).padStart(2, '0')}</span> : null}
        </div>
      )}
      <div className="flash-design-header">
        <span className="flash-design-name">{design.name || design.ai_suggested_name || 'Unnamed'}</span>
        <span className={`flash-status flash-status-${design.status}`}>{design.status}</span>
      </div>

      {design.price && (
        <span className="flash-design-price">${design.price}</span>
      )}

      {editing ? (
        <div className="flash-design-edit">
          <label><span>Name</span><input placeholder="Design name" value={name} onChange={e => setName(e.target.value)} /></label>
          <label><span>Price</span><input placeholder="$" type="number" min="0" value={price} onChange={e => setPrice(e.target.value)} /></label>
          <label><span>Time</span><input placeholder="Minutes" type="number" min="1" value={minutes} onChange={e => setMinutes(e.target.value)} /></label>
          <label className="flash-reclaimable-field">
            <input
              type="checkbox"
              checked={depositEnabled}
              onChange={e => setDeposit(e.target.checked ? (design.deposit_amount && design.deposit_amount > 0 ? String(design.deposit_amount) : '100') : '0')}
            />
            <span>Collect a deposit</span>
          </label>
          {depositEnabled ? (
            <label><span>Deposit amount</span><input placeholder="$" type="number" min="1" step="1" value={deposit} onChange={e => setDeposit(e.target.value)} /></label>
          ) : null}
          <select value={style} onChange={e => setStyle(e.target.value)}>
            <option value="">Style...</option>
            {STYLE_OPTIONS.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          {inventoryMode ? (
            <label className="flash-reclaimable-field">
              <input type="checkbox" checked={reclaimable} onChange={e => setReclaimable(e.target.checked)} />
              <span>Can be reclaimed</span>
            </label>
          ) : null}
          <div className="flash-design-edit-actions">
            <button type="button" onClick={() => void save()} disabled={saving}>
              {saving ? 'Saving...' : inventoryMode ? 'Save item' : 'Save enrich'}
            </button>
            {!inventoryMode ? <button type="button" onClick={() => setEditing(false)} disabled={saving}>Cancel</button> : null}
          </div>
        </div>
      ) : (
        <div className="flash-design-meta">
          {design.estimated_minutes && <span>{design.estimated_minutes} min</span>}
          {design.style && <span>{design.style}</span>}
          {design.deposit_amount == null
            ? <span>Deposit not set</span>
            : <span>{design.deposit_amount > 0 ? `$${design.deposit_amount} deposit` : 'No deposit'}</span>}
          <button className="flash-edit-btn" onClick={() => setEditing(true)}>Enrich</button>
          <button
            className="flash-design-delete"
            onClick={onDelete}
            disabled={deleting || design.status === 'claimed' || Boolean(design.claimed_by)}
            aria-label={`Delete ${design.name || design.ai_suggested_name || 'design'}`}
          >
            <Trash2 size={13} aria-hidden="true" />
            {deleting ? 'Deleting...' : 'Delete'}
          </button>
          {!inventoryMode && design.status === 'draft' && isEnriched && (
            <button
              className="flash-publish-single"
              onClick={() => void onUpdate({ status: 'published' })}
              title="Publish and record this cutout as accepted training evidence"
            >
              Publish
            </button>
          )}
        </div>
      )}
    </div>
  )
}
