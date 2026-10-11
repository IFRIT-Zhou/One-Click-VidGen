export function readResolution(text) {
  const rows = JSON.parse(text || '[]')
  if (!Array.isArray(rows)) throw Error('固定节点参数必须是数组')
  const get = (field, fallback) => rows.find(r => String(r.node_id) === '115' && r.field === field)?.value ?? fallback
  return { megapixels: Number(get('megapixels', 1)), multiple: Number(get('multiple', 32)), aspect: get('aspect_ratio', '16:9 (Widescreen)') }
}
export function updateResolution(text, field, value) {
  if (field === 'megapixels' && (!Number.isFinite(value) || value < 0.1 || value > 16)) throw Error('像素规模应为 0.1 至 16 MP')
  const rows = JSON.parse(text || '[]')
  if (!Array.isArray(rows)) throw Error('固定节点参数必须是数组')
  const rest = rows.filter(r => !(String(r.node_id) === '115' && r.field === field))
  return JSON.stringify([...rest, { node_id: '115', field, value }], null, 2)
}
export function resolutionSize({ megapixels, multiple, aspect }) {
  const match = String(aspect).match(/^(\d+):(\d+)/)
  if (!match || !Number.isFinite(megapixels) || megapixels < 0.1 || megapixels > 16 || !Number.isInteger(multiple) || multiple < 8 || multiple > 128) return ''
  const [, w, h] = match.map(Number)
  const scale = Math.sqrt(megapixels * 1024 * 1024 / (w * h))
  return `${Math.round(w * scale / multiple) * multiple} × ${Math.round(h * scale / multiple) * multiple}`
}

