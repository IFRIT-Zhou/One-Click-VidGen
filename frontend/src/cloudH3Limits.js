// Bounds confirmed on the deployed cluster, including each side rather than area alone.
export function h3MaximumDimensions(seconds = 14) {
  return [1664, 936]
}
export function fitsH3Dimensions(width, height, seconds = 14) {
  const duration = Number(seconds)
  const [long, short] = h3MaximumDimensions(seconds)
  return Number.isFinite(duration) && duration > 0 && duration <= 14
    && Number.isInteger(width) && Number.isInteger(height)
    && Math.min(width, height) > 0 && Math.min(width, height) <= short && Math.max(width, height) <= long
}
