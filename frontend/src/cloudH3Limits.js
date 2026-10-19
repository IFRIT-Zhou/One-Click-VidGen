// Bounds confirmed on the deployed cluster, including each side rather than area alone.
export function h3MaximumDimensions(seconds = 15) {
  return Number(seconds) > 0 && Number(seconds) <= 14 ? [1664, 936] : [1532, 832]
}
export function fitsH3Dimensions(width, height, seconds = 15) {
  const duration = Number(seconds)
  const [long, short] = h3MaximumDimensions(seconds)
  return Number.isFinite(duration) && duration > 0 && duration <= 15
    && Number.isInteger(width) && Number.isInteger(height)
    && Math.min(width, height) > 0 && Math.min(width, height) <= short && Math.max(width, height) <= long
}
