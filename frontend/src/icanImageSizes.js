// Only the regular sizes enabled by our Cloud API are offered.
export const ICAN_SIZES = {
  '16:9': { '2K': '2048x1152', '2.5K': '2560x1440' },
  '9:16': { '2K': '1152x2048', '2.5K': '1440x2560' },
  '1:1': { '1K': '1024x1024' },
  '3:2': { '1K': '1536x1024' },
  '2:3': { '1K': '1024x1536' },
}
export function imageChoice(size) {
  for (const [ratio, choices] of Object.entries(ICAN_SIZES)) {
    for (const [quality, pixels] of Object.entries(choices)) {
      if (pixels === size) return { ratio, quality }
    }
  }
  return { ratio: '16:9', quality: '2.5K' }
}
export function imageSize(ratio, quality = '2.5K') {
  const choices = ICAN_SIZES[ratio] || ICAN_SIZES['16:9']
  return choices[quality] || choices['2.5K'] || Object.values(choices)[0]
}
