// Literal replacements only; never interpret user text as a regular expression.
export function findTtsReplacements(segments, drafts, search, replacement, mode = 'pronunciation') {
  if (!search || search === replacement) return []
  return (segments || []).flatMap(item => {
    const before = mode === 'subtitle' ? String(item.text ?? '') : String(drafts[item.index] ?? item.tts_text ?? item.text ?? '')
    const count = before.split(search).length - 1
    if (!count) return []
    const after = before.split(search).join(replacement)
    return [{ item, before, after, count }]
  })
}
