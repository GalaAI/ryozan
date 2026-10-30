/**
 * Single owner of the product's visible identity. Pages read these values
 * instead of spelling the name, so one change rebrands every surface.
 */
export const BRAND = {
  product: 'Ryozan',
  studio: 'Ryozan Studio',
  tagline: 'The studio operating system run by Gala and the eight Bandits',
  mark: '/assets/brand/ryozan-mark.svg',
  markAlt: 'Ryozan seal',
  models: 'NVIDIA Nemotron on Nebius Token Factory',
} as const
