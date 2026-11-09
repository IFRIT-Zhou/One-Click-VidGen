/** Deterministic authored-animation progress. No executable capability is
 * inserted into model context: the bounded interpreter invokes this helper. */
export function sceneProgress(context, start, end) {
  const fail = message => { throw new Error('OCV_CODE_CONTRACT:' + message); };
  if (![start, end].every(value => typeof value === 'number' && Number.isFinite(value)) || end < start)
    fail('ctx.progress(start,end) expects finite seconds with end >= start');
  const {frame, fps, poster} = context;
  if (typeof frame !== 'number' || !Number.isFinite(frame) || frame < 0 ||
      typeof fps !== 'number' || !Number.isFinite(fps) || fps <= 0)
    fail('ctx.progress requires a valid frame and positive fps');
  // frame=0 is also a real video frame. Only the explicit poster flag reveals
  // all content; do not infer poster mode from a time/frame value.
  if (poster === true) return 1;
  const seconds = frame / fps;
  if (start === end) return Number(seconds >= start);
  return Math.max(0, Math.min(1, (seconds - start) / (end - start)));
}
