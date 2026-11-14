// m:ss.s for narration / timeline positions (rounded to 0.1 s).
export function fmtTime(value) {
  const t = Math.max(0, Number(value) || 0)
  const minutes = Math.floor(t / 60)
  return `${minutes}:${(t - minutes * 60).toFixed(1).padStart(4, '0')}`
}

// Keep a CSS variable with the sticky header height so side columns can stick right below it.
export function trackStickyHeight(root, sticky) {
  if (!root || !sticky || typeof ResizeObserver === 'undefined') return () => {}
  const update = () => root.style.setProperty('--wb-top', `${Math.ceil(sticky.getBoundingClientRect().height) + 16}px`)
  const observer = new ResizeObserver(update)
  observer.observe(sticky)
  update()
  return () => observer.disconnect()
}
