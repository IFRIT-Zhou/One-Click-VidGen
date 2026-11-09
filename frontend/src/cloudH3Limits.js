// Bounds confirmed on the deployed cluster, including each side rather than area alone.
export function h3MaximumDimensions() {
  return [1664, 936]
}
export function fitsH3Size(width, height) {
  const [long, short] = h3MaximumDimensions()
  return Number.isInteger(width) && Number.isInteger(height)
    && Math.min(width, height) > 0 && Math.min(width, height) <= short && Math.max(width, height) <= long
}
export function fitsH3Dimensions(width, height, seconds = 14) {
  const duration = Number(seconds)
  return Number.isFinite(duration) && duration > 0 && duration <= 14 && fitsH3Size(width, height)
}
