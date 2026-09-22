export const DEFAULT_DYNAMIC_TEXT_MODE = 'visual_first'

// Missing fields belong to historical tasks/drafts, not new-project defaults.
export function normalizeDynamicTextMode(value, fallback = 'text_assisted') {
  return ['text_assisted', 'visual_first'].includes(value) ? value : fallback
}

export function dynamicTextModeLabel(value) {
  return normalizeDynamicTextMode(value) === 'visual_first' ? '画面优先' : '文字辅助'
}

export const dynamicTextModeDescriptions = {
  text_assisted: '保留现有表达方式，可用简短文字气泡、标签或说明辅助画面传达信息；不改变成片字幕。',
  visual_first: '画面优先、文字少而必要：优先用动作、表情和景物表达，避免大段台词与说明；必要时可保留简短反应或标签，以及菜单、招牌等场景文字。想象气泡优先展示景物，不堆字。成片字幕不受影响。',
}
