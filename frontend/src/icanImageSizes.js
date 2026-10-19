// Request sizes tested on 2026-10-19. The upstream may return different pixels.
export const ICAN_SIZES = {
  '16:9': { '2K': '2048x1152', '2.5K': '2560x1440', '3K': '3072x1728', '4K': '3840x2160' },
  '9:16': { '2K': '1152x2048', '2.5K': '1440x2560' },
  '1:1': { '2K': '2048x2048', '2.8K': '2880x2880' },
  '3:4': { '2K': '1536x2048', '2.5K': '1920x2560', '3K': '2304x3072' },
  '3:2': { '1K': '1536x1024' },
  '2:3': { '1K': '1024x1536' },
}
const RUNNING_SAMPLES = {
  '16:9': { '1K': ['1672×941', '1360×768'], '2K': ['2048×1152', '2736×1536'], '4K': ['3840×2160'] },
  '1:1': { '1K': ['1254×1254', '1024×1024'], '2K': ['2048×2048'], '4K': ['2880×2880'] },
  '3:4': { '1K': ['1086×1448', '880×1184'], '2K': ['1536×2048', '1776×2368'], '4K': ['2448×3264', '2480×3312'] },
}
export function imageChoice(size) {
  // Keep old square drafts usable, but offer the measured 2K request for new work.
  if (size === '1024x1024') return { ratio: '1:1', quality: '2K' }
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
export function imageQualityLabel(method, ratio, quality) {
  if (method !== 'ican') return `${quality} 档`
  const pixels = ICAN_SIZES[ratio]?.[quality]
  return pixels ? `${quality} · ${pixels.replace('x', '×')}` : quality
}
export function imageQualityHint(method, ratio, quality) {
  if (method !== 'ican') {
    const samples = RUNNING_SAMPLES[ratio]?.[quality]
    return samples
      ? `本档实测参考：${samples.join(' / ')}。不同版本可能不同，实际像素以生成结果为准。`
      : '按画幅和清晰度档位生成，实际像素以生成结果为准。'
  }
  const size = imageSize(ratio, quality)
  if (size === '3840x2160') return '请求 3840×2160，本次实测返回 4096×2304；实际像素以生成结果为准。'
  if (size === '1024x1536') return '请求 1024×1536，本次实测返回 1365×2048；实际像素以生成结果为准。'
  return '按所选像素请求；本次实测按此尺寸返回，实际像素以生成结果为准。'
}
