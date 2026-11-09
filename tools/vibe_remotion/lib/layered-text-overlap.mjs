/** Static-poster text collisions, measured from browser text ranges/ink.
 *
 * Never compare layout boxes: a centered short caption may intentionally use
 * a wide box. Range rectangles follow the laid-out text lines. These are still
 * font metric rectangles, not exact glyph masks, so shallow edge contact is
 * tolerated. This deliberately does not validate text/image overlap.
 */
// Range rectangles describe a font's entire ascent/descent, including the
// substantial blank space above/below digits. Keep those rectangles for layout
// and clipping checks, but use measured glyph ink for collision checks when the
// browser can establish it reliably. Unknown/rotated layouts retain the old
// conservative range check; absence of an ink measurement is not clearance.
export function measureTextNodeInk(textNode) {
  const document = textNode?.ownerDocument;
  const view = document?.defaultView;
  const parent = textNode?.parentElement;
  const text = textNode?.textContent ?? '';
  if (!view || !parent || !document.createElement || !text.trim()) return null;
  const style = view.getComputedStyle(parent);
  if (style.writingMode !== 'horizontal-tb' || style.direction !== 'ltr' ||
      !['none', ''].includes(style.textTransform) || style.fontVariantCaps !== 'normal') return null;
  let scaleX = 1;
  for (let element = parent; element; element = element.parentElement) {
    const transform = view.getComputedStyle(element).transform;
    if (!transform || transform === 'none') continue;
    let matrix;
    try { matrix = new view.DOMMatrixReadOnly(transform); } catch { return null; }
    if (!matrix.is2D || Math.abs(matrix.b) > 1e-5 || Math.abs(matrix.c) > 1e-5 ||
        matrix.a <= 0 || matrix.d <= 0) return null;
    scaleX *= matrix.a;
  }
  const context = document.createElement('canvas').getContext('2d');
  if (!context) return null;
  context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  context.textAlign = 'left';
  context.textBaseline = 'alphabetic';
  if ('fontKerning' in context) context.fontKerning = style.fontKerning;
  const segmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter(undefined, {granularity: 'grapheme'}) : null;
  if (!segmenter) return null;
  const range = document.createRange(), lines = new Map();
  for (const {segment, index} of segmenter.segment(text)) {
    if (!segment.trim()) continue;
    range.setStart(textNode, index);
    range.setEnd(textNode, index + segment.length);
    const ranges = Array.from(range.getClientRects()).filter(validRect);
    // A grapheme should be on one line; unusual shaping/fragmentation should
    // not result in a guessed clearance.
    if (ranges.length !== 1) return null;
    const rect = ranges[0], metrics = context.measureText(segment);
    const values = ['actualBoundingBoxLeft', 'actualBoundingBoxRight',
      'actualBoundingBoxAscent', 'actualBoundingBoxDescent',
      'fontBoundingBoxAscent', 'fontBoundingBoxDescent'];
    if (values.some(key => !Number.isFinite(metrics[key]))) return null;
    const fontHeight = metrics.fontBoundingBoxAscent + metrics.fontBoundingBoxDescent;
    if (fontHeight <= 0) return null;
    const scaleY = (rect.bottom - rect.top) / fontHeight;
    const baseline = rect.top + metrics.fontBoundingBoxAscent * scaleY;
    const glyph = {
      left: rect.left - metrics.actualBoundingBoxLeft * scaleX,
      right: rect.left + metrics.actualBoundingBoxRight * scaleX,
      top: baseline - metrics.actualBoundingBoxAscent * scaleY,
      bottom: baseline + metrics.actualBoundingBoxDescent * scaleY,
    };
    if (!validRect(glyph)) return null;
    // Report one ink envelope per laid-out line, rather than hundreds of
    // per-character rectangles in the model's tool receipt. Different lines
    // remain separate and inline font/style fragments are measured separately.
    const lineKey = `${rect.top}:${rect.bottom}`;
    const line = lines.get(lineKey);
    if (line) {
      line.left = Math.min(line.left, glyph.left);
      line.top = Math.min(line.top, glyph.top);
      line.right = Math.max(line.right, glyph.right);
      line.bottom = Math.max(line.bottom, glyph.bottom);
    } else lines.set(lineKey, glyph);
  }
  return lines.size ? Array.from(lines.values()) : null;
}

export function isStaticLayeredPlan(plan) {
  return Array.isArray(plan?.elements) && plan.elements.length > 0 &&
    plan.elements.every(layer => layer.motion === 'none' &&
      layer.at === 0 && layer.enter === 0 && layer.exit === 0);
}

function validRect(rect) {
  return rect && ['left', 'top', 'right', 'bottom'].every(key => Number.isFinite(rect[key])) &&
    rect.right > rect.left && rect.bottom > rect.top;
}

function clearlyIntersects(a, b) {
  if (!validRect(a) || !validRect(b)) return null;
  const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
  const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
  const lineHeight = Math.min(a.bottom - a.top, b.bottom - b.top);
  // Range boxes include ascender/descender whitespace. Do not flag a few
  // pixels of anti-aliasing/metric contact between adjacent title fragments.
  if (width <= Math.max(3, lineHeight * .16) ||
      height <= Math.max(3, lineHeight * .30)) return null;
  return {width, height};
}

export function findLayeredTextOverlaps(entries) {
  const failures = [];
  const seen = new Set();
  for (let first = 0; first < entries.length; first++) {
    const a = entries[first];
    if (!a?.id || a.visible === false) continue;
    for (let second = first + 1; second < entries.length; second++) {
      const b = entries[second];
      if (!b?.id || b.visible === false || a.id === b.id) continue;
      const key = [a.id, b.id].sort().join('\u0000');
      if (seen.has(key)) continue;
      let overlap;
      for (const aRect of a.ink_rects ?? a.rects ?? []) {
        for (const bRect of b.ink_rects ?? b.rects ?? []) {
          overlap = clearlyIntersects(aRect, bRect);
          if (overlap) break;
        }
        if (overlap) break;
      }
      if (overlap) {
        seen.add(key);
        failures.push({first: a.id, second: b.id,
          width: Math.round(overlap.width * 10) / 10,
          height: Math.round(overlap.height * 10) / 10});
      }
    }
  }
  return failures;
}

function visibleText(element) {
  const view = element.ownerDocument.defaultView;
  if (!view || !element.textContent?.trim()) return false;
  let opacity = 1;
  for (let node = element; node; node = node.parentElement) {
    const style = view.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
    const alpha = Number.parseFloat(style.opacity);
    if (Number.isFinite(alpha)) opacity *= alpha;
    if (opacity < .03) return false;
  }
  return true;
}

export function collectLayeredTextRects(root) {
  const entries = [];
  root.querySelectorAll('[data-layered-text]').forEach(element => {
    if (!visibleText(element)) return;
    const range = element.ownerDocument.createRange();
    range.selectNodeContents(element);
    const rects = Array.from(range.getClientRects(), rect => ({
      left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom,
    })).filter(validRect);
    // Halos have no data-layered-text attribute and never enter this list.
    if (rects.length) entries.push({id: element.dataset.layeredText, rects});
  });
  return entries;
}
