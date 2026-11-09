/** Measured axis-aligned clipping evidence, not an artistic crop decision.
 * Boxes are [x, y, width, height] in canvas pixels. No DOM or model code runs here.
 */
const round = value => Math.round(value * 100) / 100;
const edges = box => [box[0], box[1], box[0] + box[2], box[1] + box[3]];
const area = rect => Math.max(0, rect[2] - rect[0]) * Math.max(0, rect[3] - rect[1]);
const valid = box => Array.isArray(box) && box.length === 4 && box.every(Number.isFinite);

export function geometryVisibility(box, canvas, clips = []) {
  if (!valid(box) || box[2] <= 0 || box[3] <= 0) {
    return {visible:false, state:'empty', visible_box:null, visible_fraction:0, clipped_by:[]};
  }
  const original = edges(box), frame = [0, 0, canvas[0], canvas[1]];
  const intersection = (current, clip, x = true, y = true) => [
    x ? Math.max(current[0], clip[0]) : current[0],
    y ? Math.max(current[1], clip[1]) : current[1],
    x ? Math.min(current[2], clip[2]) : current[2],
    y ? Math.min(current[3], clip[3]) : current[3],
  ];
  let remaining = intersection(original, frame);
  const clippedBy = [];
  // A subpixel rounding difference is not an artistic clipping defect.
  const changed = (before, after) => before.some((value, i) => Math.abs(value - after[i]) > 1);
  const canvasClipped = changed(original, remaining);
  const outsideCanvas = area(remaining) === 0;
  for (const clip of clips) {
    if (!valid(clip.box) || (!clip.clip_x && !clip.clip_y)) continue;
    const next = intersection(remaining, edges(clip.box), !!clip.clip_x, !!clip.clip_y);
    if (changed(remaining, next) || (area(remaining) > 0 && area(next) === 0)) {
      clippedBy.push({target:clip.target, box:clip.box.map(round),
        overflow_x:clip.overflow_x, overflow_y:clip.overflow_y});
    }
    remaining = next;
  }
  const remainingArea = area(remaining), visible = remainingArea > 0;
  return {visible, state:outsideCanvas ? 'outside_canvas' : !visible ? 'clipped' :
    canvasClipped || clippedBy.length ? 'partial' : 'inside',
    visible_box:visible ? [remaining[0], remaining[1], remaining[2]-remaining[0], remaining[3]-remaining[1]].map(round) : null,
    visible_fraction:Math.round(remainingArea / area(original) * 10000) / 10000,
    canvas_clipped:canvasClipped, clipped_by:clippedBy};
}

export function sourceAnchorFeedback(expected, actual, ancestors = []) {
  if (!valid(expected) || !valid(actual)) return null;
  const delta = actual.map((value, i) => round(value - expected[i]));
  return {expected_canvas_box:expected.map(round), actual_canvas_box:actual.map(round),
    delta, displaced:delta.some(value => Math.abs(value) > 1), positioned_ancestors:ancestors,
    hint:'anchor="source" already uses canvas coordinates. A positioned/scaled parent adds its own offset/transform; use an unshifted full-canvas Group for source placement, or anchor="manual" with box={[x,y,width,height]} for intentional parent-local layout. Do not apply the source position twice.'};
}
