/**
 * Body placement regions shared by the BodyMap selector and the flash
 * placement preview. Kept apart from BodyMap.tsx so that file exports only
 * components (React Fast Refresh).
 */
export interface BodyRegion {
  id: string
  label: string
  group: string
}

// ── Region metadata (decoupled from SVG paths for reuse) ────────

export const REGIONS: BodyRegion[] = [
  // Front
  { id: 'head', label: 'Head', group: 'Head & Neck' },
  { id: 'neck', label: 'Neck', group: 'Head & Neck' },
  { id: 'chest', label: 'Chest', group: 'Upper Body' },
  { id: 'stomach', label: 'Stomach', group: 'Upper Body' },
  { id: 'ribs-left', label: 'Left Ribs / Side', group: 'Upper Body' },
  { id: 'ribs-right', label: 'Right Ribs / Side', group: 'Upper Body' },
  { id: 'shoulder-left', label: 'Left Shoulder', group: 'Arms' },
  { id: 'shoulder-right', label: 'Right Shoulder', group: 'Arms' },
  { id: 'upper-arm-left', label: 'Left Upper Arm', group: 'Arms' },
  { id: 'upper-arm-right', label: 'Right Upper Arm', group: 'Arms' },
  { id: 'forearm-left', label: 'Left Forearm', group: 'Arms' },
  { id: 'forearm-right', label: 'Right Forearm', group: 'Arms' },
  { id: 'wrist-left', label: 'Left Wrist', group: 'Arms' },
  { id: 'wrist-right', label: 'Right Wrist', group: 'Arms' },
  { id: 'hand-left', label: 'Left Hand', group: 'Arms' },
  { id: 'hand-right', label: 'Right Hand', group: 'Arms' },
  { id: 'hip-left', label: 'Left Hip', group: 'Lower Body' },
  { id: 'hip-right', label: 'Right Hip', group: 'Lower Body' },
  { id: 'thigh-left', label: 'Left Thigh', group: 'Lower Body' },
  { id: 'thigh-right', label: 'Right Thigh', group: 'Lower Body' },
  { id: 'knee-left', label: 'Left Knee', group: 'Lower Body' },
  { id: 'knee-right', label: 'Right Knee', group: 'Lower Body' },
  { id: 'calf-left', label: 'Left Calf / Shin', group: 'Lower Body' },
  { id: 'calf-right', label: 'Right Calf / Shin', group: 'Lower Body' },
  { id: 'ankle-left', label: 'Left Ankle', group: 'Lower Body' },
  { id: 'ankle-right', label: 'Right Ankle', group: 'Lower Body' },
  { id: 'foot-left', label: 'Left Foot', group: 'Lower Body' },
  { id: 'foot-right', label: 'Right Foot', group: 'Lower Body' },
  // Back only
  { id: 'upper-back', label: 'Upper Back', group: 'Upper Body' },
  { id: 'lower-back', label: 'Lower Back', group: 'Upper Body' },
  { id: 'full-back', label: 'Full Back', group: 'Upper Body' },
]
