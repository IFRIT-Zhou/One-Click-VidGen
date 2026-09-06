import { computed, ref, watch } from 'vue'

// Explicit allowlist: a style never carries credentials, audio or render settings.
const fields = ['content_mode', 'visual_prompt_mode', 'visual_style_prompt', 'visual_prompt_system', 'agent0_prompt_system', 'agent1_prompt_system', 'agent2_director_theme']
const arrangementFields = ['director_strategy', 'visual_pacing_preset', 'visual_min_duration', 'visual_target_duration', 'visual_max_duration', 'visual_max_slides']
export function useStyleLibrary(w) {
  const styles = ref([]), styleName = ref(''), styleId = ref(''), styleMessage = ref('')
  const styleCharacters = ref(false), styleEnvironment = ref(false)
  const styleDirty = computed(() => {
    const saved = styles.value.find(s => s.id === styleId.value)
    return !!saved && [...fields, 'global_character_prompt', 'story_environment_prompt'].some(k => Object.hasOwn(saved.values, k) && saved.values[k] !== w.form[k])
  })
  function changeMode(mode) {
    const arrangement = Object.fromEntries(arrangementFields.map(k => [k, w.form[k]]))
    w.setContentMode(mode)
    Object.assign(w.form, arrangement)
  }
  const key = () => `ocv.studio.styles.v1:${w.session.value.user?.id || 'local'}`
  watch(() => w.session.value.user?.id, () => {
    try { const data = JSON.parse(localStorage.getItem(key()) || '[]'); styles.value = Array.isArray(data) ? data.filter(x => x && typeof x.name === 'string' && x.values && typeof x.values === 'object') : [] }
    catch { styles.value = []; styleMessage.value = '风格库读取失败，请检查浏览器存储。' }
    styleId.value = ''; styleName.value = ''; styleCharacters.value = false; styleEnvironment.value = false
  }, { immediate: true })
  function persist(next) {
    try { localStorage.setItem(key(), JSON.stringify(next)); styles.value = next; return true }
    catch { styleMessage.value = '保存失败，请检查浏览器存储空间。'; return false }
  }
  function saveStyle(overwrite = false) {
    const name = styleName.value.trim()
    if (!name) { styleMessage.value = '请先填写风格名称。'; return }
    const existing = styles.value.find(s => s.id === styleId.value)
    if (overwrite && !existing) return
    if (styles.value.some(s => s.name === name && (!overwrite || s.id !== styleId.value))) { styleMessage.value = '已有同名风格，请换一个名称。'; return }
    if (overwrite && !window.confirm(`更新“${existing.name}”？其他任务不会受到影响。`)) return
    const values = Object.fromEntries(fields.map(k => [k, w.form[k]]))
    if (styleCharacters.value) values.global_character_prompt = w.form.global_character_prompt
    if (styleEnvironment.value) values.story_environment_prompt = w.form.story_environment_prompt
    const record = { id: overwrite ? existing.id : crypto.randomUUID(), name, values }
    if (persist([record, ...styles.value.filter(s => s.id !== record.id)])) { styleId.value = record.id; styleMessage.value = '风格已保存到本机，可在不同任务中选用。' }
  }
  function applyStyle(s) {
    if (!w.contentModeOptions.value.some(m => m.key === s.values.content_mode)) { styleMessage.value = '此风格的基础模式不可用。'; return }
    // Preserve current story settings when the saved style does not include them.
    const character = w.form.global_character_prompt, environment = w.form.story_environment_prompt
    changeMode(s.values.content_mode)
    for (const k of fields) if (Object.hasOwn(s.values, k)) w.form[k] = s.values[k]
    w.form.global_character_prompt = s.values.global_character_prompt ?? character
    w.form.story_environment_prompt = s.values.story_environment_prompt ?? environment
    w.rememberVisualPrompt()
    styleId.value = s.id; styleName.value = s.name
    styleCharacters.value = Object.hasOwn(s.values, 'global_character_prompt')
    styleEnvironment.value = Object.hasOwn(s.values, 'story_environment_prompt')
    styleMessage.value = '已应用到当前设置；未保存人物或环境的风格会保留当前任务对应设置。'
  }
  function useExample(mode) {
    changeMode(mode); w.resetSimpleVisualPrompt()
    styleId.value = ''; styleName.value = ''; styleCharacters.value = false; styleEnvironment.value = false
    styleMessage.value = '已载入内置示范，可修改画面设置后另存为自己的风格。'
  }
  function deleteStyle(s) {
    if (!window.confirm(`删除风格“${s.name}”？不会删除任何项目或修改当前设置。`)) return
    if (persist(styles.value.filter(x => x.id !== s.id)) && styleId.value === s.id) { styleId.value = ''; styleName.value = '' }
  }
  return { styles, styleName, styleId, styleDirty, styleMessage, styleCharacters, styleEnvironment, saveStyle, applyStyle, useExample, deleteStyle }
}
