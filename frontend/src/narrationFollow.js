// Use the saved full-audio timeline, never accumulate rounded UI durations.
export function narrationSegmentAt(segments, time) {
  if (!Number.isFinite(time) || time < 0) return null
  for (let i = 0; i < segments.length; i++) {
    const row = segments[i], start = Number(row.start), end = Number(row.end)
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) continue
    // Keep the preceding sentence indicated through its explicit trailing pause.
    const next = Number(segments[i + 1]?.start)
    const until = Number.isFinite(next) && next >= end ? next : end + Math.max(0, Number(row.pause_after) || 0)
    if (time >= start && time < until) return row.index
  }
  return null
}

export function followNarration(event, segments, previous, list, selectSentence) {
  const audio = event.currentTarget
  const seek = event.type === 'seeking' || event.type === 'seeked'
  if (!seek && (audio.paused || audio.ended)) return previous
  const index = narrationSegmentAt(segments, audio.currentTime)
  // Don't fight manual scrolling on each timeupdate; only move on a new sentence
  // or an explicit scrub/play. Never scroll the page or change batch checkboxes.
  if (index != null && (index !== previous || seek || event.type === 'play')) {
    selectSentence?.(index)
    const row = [...(list?.querySelectorAll('[data-narration-index]') || [])]
      .find(node => node.dataset.narrationIndex === String(index))
    if (row && list) list.scrollTop += row.getBoundingClientRect().top - list.getBoundingClientRect().top - list.clientTop
  }
  return index
}
