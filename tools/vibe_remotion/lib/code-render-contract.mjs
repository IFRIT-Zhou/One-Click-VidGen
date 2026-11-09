/** Browser-safe presentation contract. No model-provided code is evaluated here. */
export const CODE_RENDERER = 'ocv-remotion-code-v1';
export const CODE_VERSION = 'ocv-code-program-v1';
export const CODE_LOG_PREFIX = 'OCV_CODE_MEASURE:';
export const FONT_SANS = '"Microsoft YaHei", "Noto Sans CJK SC", Arial, sans-serif';
export const FONT_SERIF = '"SimSun", "Noto Serif CJK SC", Georgia, serif';

const own = (x, k) => Object.prototype.hasOwnProperty.call(x, k);
const ID = /^[a-zA-Z0-9_:-]{1,80}$/;
const dangerous = /url\s*\(|expression\s*\(|image-set\s*\(|image\s*\(|cross-fade\s*\(|paint\s*\(|src\s*\(|attr\s*\(|var\s*\(|(?:java|vb)script:|https?:|data:|file:|@|\\|[;<>\u0000]|\/\*|\*\//i;
const STYLES = new Set(`position inset left top right bottom width height minWidth maxWidth minHeight maxHeight
 display flexDirection flexWrap alignItems alignSelf alignContent justifyContent justifyItems justifySelf gap rowGap columnGap
 flex flexGrow flexShrink flexBasis order gridTemplateColumns gridTemplateRows gridColumn gridRow gridAutoFlow
 padding paddingTop paddingBottom paddingLeft paddingRight margin marginTop marginBottom marginLeft marginRight
 boxSizing overflow overflowX overflowY opacity zIndex background backgroundColor backgroundImage backgroundSize backgroundPosition
 border borderColor borderWidth borderStyle borderRadius borderTop borderBottom borderLeft borderRight
 borderTopLeftRadius borderTopRightRadius borderBottomLeftRadius borderBottomRightRadius boxShadow
 color fontFamily fontSize fontWeight fontStyle lineHeight letterSpacing textAlign textShadow whiteSpace wordBreak overflowWrap verticalAlign
 textTransform textDecoration transform transformOrigin filter clipPath maskImage WebkitMaskImage maskSize WebkitMaskSize
 backgroundClip WebkitBackgroundClip WebkitTextFillColor objectFit objectPosition aspectRatio isolation pointerEvents`.split(/\s+/));
const COMMON = ['id', 'key', 'style'];
const PROPS = {
  div: COMMON, span: COMMON,
  Text: COMMON,
  Group: [...COMMON, 'at', 'enter', 'exit', 'motion'],
  Asset: [...COMMON, 'box', 'crop', 'anchor', 'fit'],
  svg: [...COMMON, 'width', 'height', 'viewBox', 'fill', 'stroke', 'strokeWidth', 'opacity', 'preserveAspectRatio'],
  g: [...COMMON, 'fill', 'stroke', 'strokeWidth', 'opacity', 'transform', 'strokeLinecap', 'strokeLinejoin', 'strokeDasharray', 'strokeDashoffset', 'fillRule'],
  path: [...COMMON, 'd', 'fill', 'stroke', 'strokeWidth', 'opacity', 'strokeLinecap', 'strokeLinejoin', 'strokeDasharray', 'strokeDashoffset', 'fillRule', 'transform'],
  rect: [...COMMON, 'x', 'y', 'width', 'height', 'rx', 'ry', 'fill', 'stroke', 'strokeWidth', 'opacity', 'transform'],
  circle: [...COMMON, 'cx', 'cy', 'r', 'fill', 'stroke', 'strokeWidth', 'opacity', 'transform'],
  ellipse: [...COMMON, 'cx', 'cy', 'rx', 'ry', 'fill', 'stroke', 'strokeWidth', 'opacity', 'transform'],
  line: [...COMMON, 'x1', 'y1', 'x2', 'y2', 'fill', 'stroke', 'strokeWidth', 'opacity', 'strokeLinecap', 'strokeDasharray', 'strokeDashoffset', 'transform'],
  polyline: [...COMMON, 'points', 'fill', 'stroke', 'strokeWidth', 'opacity', 'strokeLinecap', 'strokeLinejoin', 'strokeDasharray', 'strokeDashoffset', 'transform'],
};
function fail(message) { throw new Error(`OCV_CODE_CONTRACT: ${message}`); }
function object(x, label) {
  if (!x || typeof x !== 'object' || Array.isArray(x)) fail(`${label} must be an object`);
  for (const k of Object.keys(x)) if (['__proto__', 'prototype', 'constructor'].includes(k)) fail(`Unsafe ${label} key`);
}
function keys(x, allowed, label) { object(x, label); for (const k of Object.keys(x)) if (!allowed.includes(k)) fail(`Unsupported ${label}.${k}`); }
function id(x, label) { if (typeof x !== 'string' || !ID.test(x)) fail(`Invalid ${label}`); }
function diagnosticValue(value) {
  if (typeof value === 'string') return JSON.stringify(value.length > 100 ? value.slice(0, 100) + '…' : value);
  if (value === null) return 'null';
  if (value === undefined) return 'undefined';
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return Array.isArray(value) ? '[array]' : `[${typeof value}]`;
}
function elementId(value, tag, required = false) {
  if (value === undefined && !required) return;
  if (typeof value === 'string' && ID.test(value)) return;
  const reason = value === undefined ? 'Missing required' : 'Invalid';
  const example = tag === 'Text' ? '<Text id="label_1">原文</Text>; in map use id={\'label_\' + i}, not key alone. Do not remove Text.id' :
    tag === 'Asset' ? 'Use an existing render_assets ID, for example <Asset id="subject_0" />' :
    'Use a stable ID such as id="panel_1" or id={\'panel_\' + i}; do not build IDs from fractional times';
  fail(`${reason} ${tag}.id: received ${diagnosticValue(value)}. ID must be a string of 1–80 ASCII letters, digits, _, : or - (no spaces, Chinese characters or decimal points). ${example}`);
}
function finite(x, lo, hi, label) { if (!Number.isFinite(x) || x < lo || x > hi) fail(`Invalid ${label}`); }
function box(x, label) {
  if (!Array.isArray(x) || x.length !== 4 || x.some(v => !Number.isFinite(v))) fail(`Invalid ${label}`);
  if (x[2] <= 0 || x[3] <= 0 || x.some(v => Math.abs(v) > 10000)) fail(`Invalid ${label} dimensions`);
}
export function validateCrop(crop = [0, 0, 1, 1]) {
  if (!Array.isArray(crop) || crop.length !== 4 || crop.some(v => !Number.isFinite(v) || v < 0 || v > 1) || crop[2] - crop[0] < .001 || crop[3] - crop[1] < .001) fail('crop must be [left,top,right,bottom] in 0..1');
  return crop;
}
export function safeStyle(value = {}) {
  object(value, 'style');
  const result = {};
  if (Object.keys(value).length > 100) fail('Too many style properties');
  for (const [key, val] of Object.entries(value)) {
    if (!STYLES.has(key)) fail(`Unsupported style.${key}`);
    if (typeof val === 'number') { finite(val, -10000, 10000, `style.${key}`); }
    else if (typeof val !== 'string' || val.length > 2000 || dangerous.test(val)) fail(`Unsafe style.${key}`);
    result[key] = val;
  }
  return result;
}

/** Crops refer to the *prepared raster*, never to its original source file. */
export function assetPlacement(props, geometry = {}) {
  let crop = validateCrop(props.crop);
  const prepared = geometry[props.id]?.prepared_size;
  // Match the actual integer-pixel crop used by prepared-raster sampling.
  if (prepared) {
    crop = crop.map((value,i)=>Math.round(value*prepared[i%2])/prepared[i%2]);
    validateCrop(crop);
  }
  if (props.anchor === 'source') {
    const source = geometry[props.id];
    if (!source?.anchor_available) fail(`Asset ${props.id} has no source anchor; request a measured manual box`);
    box(source.box, 'source box');
    if (props.box !== undefined) fail('source anchor and manual box are mutually exclusive');
    for (const key of ['left', 'top', 'right', 'bottom', 'width', 'height', 'position', 'inset']) {
      if (own(props.style ?? {}, key)) fail(`Source-anchored asset cannot override style.${key}`);
    }
    const [x, y, w, h] = source.box;
    return {box: [x + crop[0] * w, y + crop[1] * h, (crop[2] - crop[0]) * w, (crop[3] - crop[1]) * h], crop};
  }
  if (props.anchor !== undefined && props.anchor !== 'manual') fail('Unsupported anchor');
  if (props.box !== undefined) box(props.box, 'asset box');
  return {box: props.box ?? null, crop};
}

export function validateTree(tree, assets, geometry = {}, duration = 60) {
  const usedTextIds = new Map();
  let nodes = 0;
  function visit(node, depth, insideText, insideSvg, path) {
    try { visitNode(node, depth, insideText, insideSvg, path); }
    catch (error) {
      if (error instanceof Error && error.message.startsWith('OCV_CODE_CONTRACT:') && !error.message.includes(' [node ')) {
        const tag = node && typeof node === 'object' && own(PROPS, node.tag) ? `<${node.tag}>` : Array.isArray(node) ? 'array' : typeof node;
        error.message += ` [node ${path} ${tag}]`;
      }
      throw error;
    }
  }
  function visitNode(node, depth, insideText, insideSvg, path) {
    if (++nodes > 1200 || depth > 48) fail('Presentation tree is too large');
    if (node === null || node === undefined || typeof node === 'boolean') return;
    if (typeof node === 'string' || typeof node === 'number') {
      if (!insideText && String(node).trim()) fail('Visible text must be placed inside Text, not raw div/span/SVG text');
      if (String(node).length > 8000) fail('Text is too long');
      return;
    }
    if (Array.isArray(node)) { for (let i = 0; i < node.length; i++) visit(node[i], depth + 1, insideText, insideSvg, `${path}[${i}]`); return; }
    keys(node, ['tag', 'props', 'children'], 'node');
    if (!own(PROPS, node.tag)) fail(`Unsupported tag ${node.tag}`);
    const p = node.props ?? {};
    keys(p, PROPS[node.tag], `${node.tag} props`);
    safeStyle(p.style);
    elementId(p.id, node.tag, node.tag === 'Text' || node.tag === 'Asset');
    if (p.key !== undefined && (typeof p.key !== 'string' && typeof p.key !== 'number')) fail('Invalid key');
    if (insideText && node.tag !== 'span') fail('Text may only contain inline span children');
    if (insideSvg && !['g', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline'].includes(node.tag)) fail('SVG accepts only safe geometric primitives');
    for (const [key, val] of Object.entries(p)) {
      if (['style', 'box', 'crop'].includes(key)) continue;
      if (!['string', 'number'].includes(typeof val) || (typeof val === 'string' && (val.length > 16000 || dangerous.test(val))) || (typeof val === 'number' && !Number.isFinite(val))) fail(`Unsafe ${node.tag}.${key}`);
    }
    if (node.tag === 'Text') {
      if (usedTextIds.has(p.id)) fail(`Duplicate Text id ${diagnosticValue(p.id)}; first used at ${usedTextIds.get(p.id)}. Every Text needs a unique id, including map entries: <Text id={'label_' + i}>原文</Text>. Keep both Text elements and assign distinct IDs`);
      usedTextIds.set(p.id, path);
    }
    if (node.tag === 'Asset') {
      if (!own(assets, p.id)) fail(`Unbound asset ${p.id}`);
      if (p.id === 'background') fail('Background is fixed by the renderer; do not add it as an Asset. Remove this <Asset id="background" /> element; the selected background is already drawn underneath Scene. Keep the scene root transparent and add only foreground assets/text');
      if (p.fit !== undefined && !['contain', 'cover'].includes(p.fit)) fail('Unsupported asset fit');
      assetPlacement(p, geometry);
      if ((node.children ?? []).length) fail('Asset cannot have children');
    }
    if (node.tag === 'Group') {
      for (const key of ['at', 'enter', 'exit']) if (p[key] !== undefined) finite(p[key], 0, key === 'exit' ? .5 : duration, `Group.${key}`);
      if ((p.at ?? 0) + (p.enter ?? .5) > duration + .001) fail('Group enters after narration ends');
      if (p.motion !== undefined && !['none', 'fade', 'slide_left', 'slide_right', 'slide_up', 'slide_down', 'scale'].includes(p.motion)) fail('Unsupported Group motion');
    }
    const children = node.children ?? [];
    if (!Array.isArray(children)) fail('Element children must be an array');
    for (let i = 0; i < children.length; i++) visit(children[i], depth + 1, insideText || node.tag === 'Text', insideSvg || node.tag === 'svg', `${path}.children[${i}]`);
  }
  visit(tree, 0, false, false, 'Scene');
  return tree;
}

/** Trusted, derived from the validated poster tree, never supplied by a model. */
export function treeManifest(tree) {
  const texts={},assets=[];
  const textOf=node=>node==null||typeof node==='boolean'?'':typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node)?node.map(textOf).join(''):(node.children??[]).map(textOf).join('');
  const walk=node=>{
    if(Array.isArray(node)){node.forEach(walk);return;}
    if(!node||typeof node!=='object')return;
    if(node.tag==='Text')texts[node.props.id]=textOf(node);
    if(node.tag==='Asset'&&!assets.includes(node.props.id))assets.push(node.props.id);
    (node.children??[]).forEach(walk);
  };
  walk(tree);return {texts,assets};
}
export function validateFrameManifest(tree, reference) {
  const actual=treeManifest(tree);
  for(const [id,text] of Object.entries(actual.texts))if(!own(reference.texts,id)||reference.texts[id]!==text)fail(`Animation changed text ${id}; keep wording/numbers stable, animate visibility and position instead`);
  for(const id of actual.assets)if(!reference.assets.includes(id))fail(`Animation introduced unreviewed asset ${id}`);
  return actual;
}

/** Interpret browser-computed colors, not arbitrary author CSS strings. */
export function computedColorAlpha(color) {
  if(typeof color!=='string'||color==='transparent')return 0;
  const value=color.trim().toLowerCase();
  const slash=value.match(/\/\s*([\d.]+)(%)?\s*\)$/);
  if(slash)return Math.max(0,Math.min(1,Number(slash[1])/(slash[2]?100:1)));
  const rgba=value.match(/^rgba\(\s*[^,]+,\s*[^,]+,\s*[^,]+,\s*([\d.]+)(%)?\s*\)$/);
  if(rgba)return Math.max(0,Math.min(1,Number(rgba[1])/(rgba[2]?100:1)));
  return /^(?:rgb|hsl|hwb|lab|lch|oklab|oklch|color)\(/.test(value)?1:0;
}

/** Flag opaque, nearly full-canvas surfaces; never mutate the authored code. */
export function backgroundCoverage(entry,canvas) {
  if(!entry||entry.tag==='img'||entry.display==='none'||['hidden','collapse'].includes(entry.visibility))return null;
  const [x,y,w,h]=entry.box??[];
  if(![x,y,w,h].every(Number.isFinite)||w<=0||h<=0)return null;
  const width=Math.max(0,Math.min(canvas[0],x+w)-Math.max(0,x));
  const height=Math.max(0,Math.min(canvas[1],y+h)-Math.max(0,y));
  const alpha=computedColorAlpha(entry.background_color),opacity=entry.opacity??1;
  if(width/canvas[0]<.9||height/canvas[1]<.9||alpha*opacity<.95)return null;
  // Nonrectangular masks and clipping can make the bounding box misleading.
  // Keep this as a review diagnostic, with the effect included as evidence.
  return {box:entry.box,canvas,coverage_width:width/canvas[0],coverage_height:height/canvas[1],background_color:entry.background_color,
    background_alpha:alpha,opacity,effective_alpha:alpha*opacity,clip_path:entry.clip_path??'none',mask_image:entry.mask_image??'none'};
}

/** Scroll metrics include font ascent/descent, not only clipped/overflowed ink.
 * Natural auto-height text with overflow:visible is allowed to use that ink.
 * Explicit bounds and clipping remain checked; collisions/canvas are separate.
 */
export function textOverflowEvidence(entry) {
  const font=Math.max(1,Number(entry.font_size)||1);
  const constrainedX=entry.explicit_width||entry.overflow_x!=='visible';
  const constrainedY=entry.explicit_height||entry.overflow_y!=='visible';
  const horizontal=constrainedX&&entry.scroll_width>entry.client_width+Math.max(2,font*.04);
  const vertical=constrainedY&&entry.scroll_height>entry.client_height+Math.max(3,font*.12);
  if(!horizontal&&!vertical)return null;
  return {horizontal:!!horizontal,vertical:!!vertical,scroll:[entry.scroll_width,entry.scroll_height],client:[entry.client_width,entry.client_height],
    overflow_x:entry.overflow_x,overflow_y:entry.overflow_y,explicit_width:!!entry.explicit_width,explicit_height:!!entry.explicit_height};
}

export function validateCodeInput(raw) {
  keys(raw, ['code', 'program', 'canvas', 'duration', 'fps', 'assets', 'geometry', 'measurements', 'mode', 'measure_requests', 'preview_level'], 'input');
  const input = {...raw, assets: raw.assets ?? {}, geometry: raw.geometry ?? {}, measurements: raw.measurements ?? {}, measure_requests: raw.measure_requests ?? []};
  if (!['measure', 'preview', 'video'].includes(input.mode)) fail('mode must be measure, preview or video');
  if (input.preview_level === undefined) input.preview_level = 'full';
  if (!['layout', 'full'].includes(input.preview_level)) fail('preview_level must be layout or full');
  if (input.mode === 'video') input.preview_level = 'full';
  if (!Array.isArray(input.canvas) || input.canvas.length !== 2 || input.canvas.some(v => !Number.isInteger(v) || v < 64 || v > 1920 || v % 2) || input.canvas[0] * input.canvas[1] > 1920 * 1080) fail('Invalid canvas');
  finite(input.duration, .1, 60, 'duration');
  if (input.fps !== 30) fail('fps must be 30');
  object(input.assets, 'assets'); object(input.geometry, 'geometry'); object(input.measurements, 'measurements');
  if (Object.keys(input.assets).length > 40) fail('Too many assets');
  for (const [key, value] of Object.entries(input.assets)) { id(key, 'asset id'); if (typeof value !== 'string' || value.length > 4096) fail('Invalid asset server path'); }
  if (input.mode !== 'measure' && !own(input.assets, 'background')) fail('A prepared background asset is required');
  for (const [key, value] of Object.entries(input.geometry)) {
    id(key, 'geometry id'); keys(value, ['box', 'prepared_size', 'anchor_available', 'anchor_unavailable_reason'], 'geometry entry');
    if(value.box !== undefined || value.anchor_available)box(value.box, 'geometry box');
    if (typeof value.anchor_available !== 'boolean') fail('geometry.anchor_available must be boolean');
    if(value.anchor_unavailable_reason != null && (typeof value.anchor_unavailable_reason !== 'string' || value.anchor_unavailable_reason.length>500))fail('Invalid anchor_unavailable_reason');
    if (!Array.isArray(value.prepared_size) || value.prepared_size.length !== 2 || value.prepared_size.some(v => !Number.isFinite(v) || v <= 0 || v > 16384)) fail('Invalid prepared_size');
  }
  if (JSON.stringify(input.measurements).length > 80000) fail('Measurement context too large');
  for (const [key, entry] of Object.entries(input.measurements)) {
    id(key, 'measurement id'); object(entry, 'measurement');
    for (const [k, value] of Object.entries(entry)) {
      if (!['id', 'text', 'width', 'height', 'font_size', 'font_weight', 'font_family', 'max_width', 'line_height'].includes(k) || !['string', 'number', 'boolean'].includes(typeof value)) fail('Invalid measurement property');
      if (typeof value === 'number' && !Number.isFinite(value)) fail('Invalid measurement number');
      if (typeof value === 'string' && value.length > 2000) fail('Measurement string too long');
    }
  }
  if (!Array.isArray(input.measure_requests) || input.measure_requests.length > 80) fail('Too many measure_requests');
  const ids = new Set();
  for (const request of input.measure_requests) {
    keys(request, ['id', 'text', 'font_size', 'font_weight', 'font_family', 'max_width', 'line_height'], 'measure request');
    id(request.id, 'measure request id'); if (ids.has(request.id)) fail('Duplicate measure request id'); ids.add(request.id);
    if (typeof request.text !== 'string' || request.text.length > 2000) fail('Invalid measurement text');
    finite(request.font_size, 8, 512, 'font_size');
    if (request.max_width !== undefined) finite(request.max_width, 8, 1920, 'max_width');
    if (request.line_height !== undefined) finite(request.line_height, .8, 3, 'line_height');
    if (request.font_weight !== undefined && !/^(?:normal|bold|[1-9]00)$/.test(String(request.font_weight))) fail('Invalid font_weight');
    if (request.font_family !== undefined && !['sans', 'serif', FONT_SANS, FONT_SERIF].includes(request.font_family)) fail('font_family must be sans or serif');
  }
  if (input.mode !== 'measure') {
    if (typeof input.code !== 'string' && !input.program) fail('code or program is required');
    if (input.code !== undefined && (typeof input.code !== 'string' || input.code.length > 100000)) fail('Code is too large');
  }
  return input;
}

export function codeProofFrames(duration, fps = 30) {
  const end = Math.max(0, Math.round(duration * fps) - 1);
  return [['early',Math.min(.5,duration/4)],['middle',duration/2],['hold',duration-Math.min(1,duration*.15)],['end',Math.max(0,duration-.1)]]
    .map(([id,time])=>({id,file:`frame_${id}.png`,frame:Math.max(0,Math.min(end,Math.round(time*fps)))}))
    .map(item=>({...item,time:item.frame/fps}));
}
