import { computed, reactive, ref, watch } from 'vue'

const STORAGE_KEY = 'ocv.studio.appearance.v1'
const defaultPalette = Object.freeze({
  background: '#151719', panel: '#202526', text: '#e7e9eb', muted: '#a0aaa8',
  accent: '#83dec5', danger: '#ed9292', warning: '#d6bc87',
})
const presets = [
  { id: 'graphite-mint', name: '石墨薄荷', note: '当前默认 · 克制、清爽', colors: defaultPalette },
  { id: 'deep-ocean', name: '深海蓝', note: '沉静、偏专业工具感', colors: { background: '#101827', panel: '#182338', text: '#e8efff', muted: '#a6b7d2', accent: '#72c7ff', danger: '#f38b9c', warning: '#e3c477' } },
  { id: 'violet-night', name: '暗夜紫', note: '柔和、偏创作氛围', colors: { background: '#1a1623', panel: '#262032', text: '#f0ebf7', muted: '#b7acc8', accent: '#c5a0ff', danger: '#fa9ba8', warning: '#e4bf7b' } },
  { id: 'amber-noir', name: '暖黑琥珀', note: '温暖、低饱和', colors: { background: '#1b1916', panel: '#29251f', text: '#f3ede3', muted: '#c0b5a5', accent: '#e7bb72', danger: '#ef9991', warning: '#ebca83' } },
  { id: 'mono-contrast', name: '纯黑高对比', note: '清晰、克制', colors: { background: '#0b0b0b', panel: '#191919', text: '#f5f5f5', muted: '#c4c4c4', accent: '#f1f1f1', danger: '#ff8585', warning: '#f1cf72' } },
]

function validColor(value, fallback) {
  return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value) ? value : fallback
}
export function useAppearance() {
  const activeThemeId = ref('graphite-mint')
  const palette = reactive({ ...defaultPalette })
  const activePreset = computed(() => presets.find(item => item.id === activeThemeId.value))
  function apply() {
    const root = document.documentElement
    root.dataset.ocvTheme = activeThemeId.value
    root.style.setProperty('--ocv-bg', palette.background)
    root.style.setProperty('--ocv-panel', palette.panel)
    root.style.setProperty('--ocv-text', palette.text)
    root.style.setProperty('--ocv-muted', palette.muted)
    root.style.setProperty('--ocv-accent', palette.accent)
    root.style.setProperty('--ocv-danger', palette.danger)
    root.style.setProperty('--ocv-warning', palette.warning)
  }
  function persist() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ id: activeThemeId.value, colors: { ...palette } }))
  }
  function chooseTheme(theme) {
    activeThemeId.value = theme.id
    Object.assign(palette, theme.colors)
  }
  function restoreDefault() { chooseTheme(presets[0]) }
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')
    if (saved) {
      activeThemeId.value = presets.some(item => item.id === saved.id) ? saved.id : 'custom'
      for (const [key, fallback] of Object.entries(defaultPalette)) palette[key] = validColor(saved.colors?.[key], fallback)
    }
  } catch { /* malformed local preference falls back silently */ }
  apply()
  watch([activeThemeId, palette], () => { apply(); persist() }, { deep: true })
  return { appearancePresets: presets, activeThemeId, palette, activePreset, chooseTheme, restoreAppearanceDefault: restoreDefault }
}
