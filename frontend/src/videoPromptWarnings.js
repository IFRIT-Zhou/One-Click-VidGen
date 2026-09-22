// Historical tasks persist warning strings. Keep the severity mapping here so
// opening an old task needs neither a migration nor another Agent request.
export function promptWarningSeverity(value) {
  const text = String(value ?? '').trim()
  if (!text || /^(提示词分节标题不完整或顺序不同|分镜图提示词结构或顺序不正确)/.test(text)) return 'format'
  if (/^(缺少短文字原文|规划了画面短文字，却同时要求全面禁止文字|核心图混入未选用的阶段文字|核心图混入其他阶段文字)/.test(text)) return 'warning'
  // Names, attribution and container wording are lexical comparisons, not a
  // reliable determination that the meaning or speaker is actually wrong.
  return 'info'
}

export function shotPromptNotes(shot) {
  const notes = [], seen = new Set()
  for (const [field, source] of [['image_prompt_warnings', '核心图'], ['video_prompt_warnings', '视频']]) {
    for (const value of Array.isArray(shot?.[field]) ? shot[field] : []) {
      const text = String(value ?? '').trim(), severity = promptWarningSeverity(text)
      const key = source + ':' + text
      if (severity === 'format' || seen.has(key)) continue
      seen.add(key)
      notes.push({ source, text, severity, label: source + '：' + text })
    }
  }
  return notes
}

export function shotHasPromptWarning(shot) {
  return shotPromptNotes(shot).some(note => note.severity === 'warning')
}

export function promptWarningRows(shots) {
  return (shots || []).map((shot, index) => ({ shot, index, notes: shotPromptNotes(shot).filter(note => note.severity === 'warning') }))
    .filter(row => row.notes.length)
}
