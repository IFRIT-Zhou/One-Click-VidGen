"""Bounded, read-only source localization and browser/source geometry evidence.

OCR detections and OpenCV edges are observations, not semantic truth or a
one-pixel accuracy guarantee. This module never rewrites copy, moves elements,
adopts a scene, or lets an estimated model ROI become a verified source box.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import unicodedata

from PIL import Image, ImageOps


VERSION = 'ocv-source-measurements-v2'
MAX_TEXTS = 128
MAX_LINES = 32
MAX_BOXES = 32
MAX_LABELS = 64
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_BYTES = 64 * 1024**2
MAX_MATCH_PARTS = 6
MAX_CANDIDATES = 12
MIN_CONFIDENCE = .8


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _size(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(type(v) is not int or not 1 <= v <= 20000 for v in value)):
        raise ValueError('Source geometry size must contain two bounded integer dimensions')
    if value[0]*value[1] > MAX_IMAGE_PIXELS:
        raise ValueError('Source geometry image is too large')
    return list(value)


def _box(value, size=None):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(not _finite(v) for v in value)
            or value[0] >= value[2] or value[1] >= value[3]):
        return None
    result = [float(v) for v in value]
    if size is not None and not (0 <= result[0] < result[2] <= size[0]
                                 and 0 <= result[1] < result[3] <= size[1]):
        return None
    return result


def _normal(box, size):
    return [round(box[0]/size[0], 9), round(box[1]/size[1], 9),
            round(box[2]/size[0], 9), round(box[3]/size[1], 9)]


def _pixels(box, size):
    return [round(box[0]*size[0], 4), round(box[1]*size[1], 4),
            round(box[2]*size[0], 4), round(box[3]*size[1], 4)]


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _iou(a, b):
    intersection = max(0., min(a[2], b[2])-max(a[0], b[0])) * max(0., min(a[3], b[3])-max(a[1], b[1]))
    union = (a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
    return intersection/union if union > 0 else 0.


def _key(value):
    """Only typographic equivalence; never drop decimal/math/data punctuation."""
    # NFKC would collapse 10⁹ into 109, changing numeric meaning. Preserve
    # exponent/subscript roles before normalizing full-width typography.
    raised = dict(zip('⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻', '0123456789+-'))
    lowered = dict(zip('₀₁₂₃₄₅₆₇₈₉₊₋', '0123456789+-'))
    value = ''.join('^'+raised[c] if c in raised else '_'+lowered[c] if c in lowered else c
                    for c in str(value or ''))
    text = unicodedata.normalize('NFKC', value).casefold()
    equivalents = {'–': '-', '—': '-', '−': '-', '‐': '-', '‑': '-',
                    '“': '"', '”': '"', '‘': "'", '’': "'"}
    return ''.join(equivalents.get(c, c) for c in text if not c.isspace())


def _open_source(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError('Source measurement requires a bounded local raster file')
    hasher = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b''):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    with Image.open(path) as image:
        if getattr(image, 'n_frames', 1) != 1 or image.width*image.height > MAX_IMAGE_PIXELS:
            raise ValueError('Source measurement requires a single bounded raster image')
        rgb = ImageOps.exif_transpose(image).convert('RGB')
    return rgb, digest


def _geometric_candidates(image, canvas, image_id):
    """Lightweight edge candidates only, with explicit resize provenance."""
    try:
        import cv2
        import numpy as np
    except (ImportError, OSError):
        return [], [], {'status': 'unavailable', 'reason': 'OpenCV or NumPy is not installed'}
    sw, sh = image.size
    factor = min(1., 2048/max(sw, sh))
    size = (max(1, round(sw*factor)), max(1, round(sh*factor)))
    small = image.resize(size, Image.Resampling.LANCZOS) if size != image.size else image.copy()
    try:
        gray = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 60, 160)
        sx, sy = sw/size[0], sh/size[1]
        raw_lines = cv2.HoughLinesP(edges, 1, np.pi/180, threshold=max(20, round(min(size)*.04)),
            minLineLength=max(24, round(min(size)*.06)), maxLineGap=max(3, round(min(size)*.006)))
        line_values = []
        for line in ([] if raw_lines is None else raw_lines[:4096]):
            x1, y1, x2, y2 = [int(v) for v in line[0]]
            points = [x1*sx, y1*sy, x2*sx, y2*sy]
            if (points[0], points[1]) > (points[2], points[3]):
                points = [*points[2:], *points[:2]]
            length = math.hypot(points[2]-points[0], points[3]-points[1])
            line_values.append((length, points))
        lines = []
        for length, points in sorted(line_values, key=lambda row: -row[0]):
            if any(max(abs(points[i]-r['source_pixels'][i]) for i in range(4)) <= max(sx, sy)*3
                   for r in lines):
                continue
            angle = math.degrees(math.atan2(points[3]-points[1], points[2]-points[0]))
            normalized = _normal(points, [sw, sh])
            lines.append(dict(id=f'{image_id}_line_{len(lines)}', image_id=image_id, kind='line_candidate',
                source_pixels=[round(v, 4) for v in points], normalized=normalized,
                canvas_pixels=_pixels(normalized, canvas), length_source_pixels=round(length, 3),
                orientation='horizontal' if abs(angle) <= 3 else 'vertical' if abs(abs(angle)-90) <= 3 else 'oblique',
                semantic_role='unassigned', verified=False))
            if len(lines) >= MAX_LINES:
                break
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        raw_boxes = []
        for contour in contours[:10000]:
            x, y, w, h = cv2.boundingRect(contour)
            area = w*h
            if w < 18 or h < 12 or not .0005 <= area/(size[0]*size[1]) <= .95:
                continue
            polygon = cv2.approxPolyDP(contour, cv2.arcLength(contour, True)*.025, True)
            if not 4 <= len(polygon) <= 8:
                continue
            raw_boxes.append((area, [x*sx, y*sy, (x+w)*sx, (y+h)*sy]))
        boxes = []
        for area, bounds in sorted(raw_boxes, key=lambda row: -row[0]):
            if any(_iou(bounds, b['source_pixels']) >= .9 for b in boxes):
                continue
            normalized = _normal(bounds, [sw, sh])
            boxes.append(dict(id=f'{image_id}_box_{len(boxes)}', image_id=image_id, kind='contour_box_candidate',
                source_pixels=[round(v, 4) for v in bounds], normalized=normalized,
                canvas_pixels=_pixels(normalized, canvas), semantic_role='unassigned', verified=False))
            if len(boxes) >= MAX_BOXES:
                break
        return lines, boxes, dict(status='ready', backend='opencv', version=cv2.__version__,
            analysis_size=list(size), source_scale_xy=[sx, sy],
            line_candidates_truncated=len(line_values) > len(lines),
            box_candidates_truncated=len(raw_boxes) > len(boxes),
            note='Pixel-derived candidates, not confirmed text panels or axes. Glow, decorative lines and residual background frames can be detected too.')
    except Exception as exc:
        # Optional geometry must not discard valid OCR when an OpenCV build or
        # unusual image makes an edge operation unavailable. No fake clearance.
        return [], [], {'status': 'unavailable', 'reason': 'OpenCV candidate detection failed',
                         'error_type': type(exc).__name__}
    finally:
        small.close()


def build_source_measurements(image_path, ocr_result, canvas):
    """Bind local OCR and geometry to exact source bytes and canvas dimensions."""
    canvas = _size(canvas)
    image, digest = _open_source(image_path)
    try:
        # Aliases such as core/background can change, and equally sized images
        # can have unrelated edges. Candidate ordinals are local to these exact
        # bytes, never shared semantic identities or evidence of a residual.
        image_id = 'image_'+digest[:16]
        source_size = list(image.size)
        warnings = []
        valid = isinstance(ocr_result, dict) and ocr_result.get('status') == 'ready'
        if not valid:
            warnings.append('OCR unavailable: no text position has been established')
        elif ocr_result.get('source_size') != source_size:
            valid = False
            warnings.append('OCR source dimensions do not match the EXIF-oriented image; coordinates were discarded')
        elif ocr_result.get('source_sha256') != digest:
            valid = False
            warnings.append('OCR source hash does not match this image; stale coordinates were discarded')
        elif ocr_result.get('coordinate_space', 'source_pixels') != 'source_pixels':
            valid = False
            warnings.append('OCR coordinates are not full source pixels; coordinates were discarded')
        rows = ocr_result.get('texts', []) if valid else []
        if not isinstance(rows, list):
            rows = []
            valid = False
            warnings.append('OCR text list is invalid')
        texts, rejected = [], 0
        for index, row in enumerate(rows[:2048]):
            box = _box(_get(row, 'box'), source_size)
            value, score = _get(row, 'text'), _get(row, 'confidence')
            if (not box or not isinstance(value, str) or not value.strip() or len(value) > 400
                    or not _finite(score) or not 0 <= score <= 1):
                rejected += 1
                continue
            if len(texts) >= MAX_TEXTS:
                break
            normalized = _normal(box, source_size)
            texts.append(dict(id=f'ocr_{index}', image_id=image_id, text=value, confidence=float(score),
                source_pixels=box, normalized=normalized, canvas_pixels=_pixels(normalized, canvas),
                origin='local_ocr', accuracy='detected_region_not_pixel_exact', verified=False))
        if rejected:
            warnings.append(f'{rejected} malformed OCR observations were discarded')
        truncated = len(rows) > len(texts)+rejected
        if truncated:
            warnings.append('OCR observations were truncated; unlisted text is not proven absent')
        lines, boxes, geometry = _geometric_candidates(image, canvas, image_id)
        geometry.update(image_id=image_id, source_sha256=digest)
        return dict(version=VERSION, image_id=image_id, source_sha256=digest, source_size=source_size, canvas=canvas,
            coordinate_space='source_normalized_ltrb',
            coordinate_provenance=dict(image_id=image_id, source_sha256=digest,
                source_orientation='exif_transposed', source_size=source_size, canvas_size=canvas,
                source_pixels='full_source_pixels', normalized='full_source_fraction',
                canvas_pixels='normalized_source_scaled_to_canvas',
                text_and_box_format='left_top_right_bottom', line_format='x1_y1_x2_y2'),
            candidate_id_contract='Line/box IDs are bound to image_id and these source bytes. An ordinal from another image is not the same observation; matching geometry does not establish shared semantic identity. Compare coordinates only in their declared space and confirm any claimed residual on the current image.',
            coordinate_contract='Text/box coordinates are [left,top,right,bottom]; line coordinates are endpoints [x1,y1,x2,y2], not a bounding box. normalized refers to the full EXIF-oriented source, never a crop display. canvas_pixels are scaled from that source into the declared canvas. OCR regions are detections, not exact glyph ink; OpenCV lines/boxes are unassigned candidates. Model-estimated ROIs do not override these observations.',
            status='ready' if valid else 'ocr_unavailable',
            ocr=dict(status='ready' if valid else 'unavailable', backend=_get(ocr_result, 'backend'),
                     version=_get(ocr_result, 'version'),
                     runtime_fingerprint=_get(ocr_result, 'runtime_fingerprint'),
                     model_version=_get(ocr_result, 'model_version'),
                     truncated=truncated, rejected=rejected,
                     region=_get(ocr_result, 'region'), confidence_threshold=MIN_CONFIDENCE),
            texts=texts, lines=lines, boxes=boxes, geometry=geometry, warnings=warnings)
    finally:
        image.close()


def merge_source_measurements(full, partial):
    """Add tool-requested local OCR without promoting it to full-page coverage.

    Both reports must already be bound to the same immutable image/canvas.
    Identical text at another location is retained as a distinct candidate.
    """
    if (not isinstance(full, dict) or not isinstance(partial, dict)
            or any(not full.get(key) or full.get(key) != partial.get(key)
                   for key in ('source_sha256', 'source_size', 'canvas'))):
        raise ValueError('Cannot merge source measurements from different image bytes, sizes or canvases')
    result = deepcopy(full)
    if full.get('version') != VERSION:
        # The unmodified old report remains on disk as history. It cannot be
        # promoted into the current baseline merely by merging a fresh crop.
        result.update(status='ocr_unavailable', texts=[], lines=[], boxes=[])
        result['ocr'] = {**result.get('ocr', {}), 'status': 'unavailable', 'historical_only': True}
        result.setdefault('warnings', []).append('Base source measurement version is stale or missing; historical coordinates were not reused. Rebuild the full source baseline.')
        return result
    if partial.get('version') != VERSION:
        result.setdefault('warnings', []).append('Supplemental source measurement version is stale or missing; kept as history only, not merged into current source coordinates')
        return result
    if partial.get('status') != 'ready':
        result.setdefault('warnings', []).append('Supplemental OCR unavailable; previous source observations retained')
        return result
    full_ocr = full.get('ocr') if isinstance(full.get('ocr'), dict) else {}
    partial_ocr = partial.get('ocr') if isinstance(partial.get('ocr'), dict) else {}
    for key in ('runtime_fingerprint', 'model_version'):
        # Historical fixtures with neither identifier remain compatible. Once
        # a real runtime supplies an identifier, a missing counterpart is not
        # evidence that the two measurements came from the same model.
        before, after = full_ocr.get(key), partial_ocr.get(key)
        if (before is not None or after is not None) and before != after:
            result.setdefault('warnings', []).append(f'Supplemental OCR {key} differs or is missing; kept as history only, not merged into current source coordinates')
            return result
    for key in ('backend', 'version'):
        before, after = full_ocr.get(key), partial_ocr.get(key)
        if before is not None and after is not None and before != after:
            result.setdefault('warnings', []).append(f'Supplemental OCR {key} differs; kept as history only, not merged into current source coordinates')
            return result
    # Recent focused detections are not silently lost behind a full-page cap.
    # Overlapping same-text observations collapse only when they refer to the
    # same physical location; all different locations remain ambiguous.
    texts = []
    for row in partial.get('texts', [])[:MAX_TEXTS]:
        if not isinstance(row, dict):
            continue
        item = deepcopy(row)
        item['origin'] = 'supplemental_local_ocr'
        item['ocr_region'] = deepcopy(partial.get('ocr', {}).get('region'))
        texts.append(item)
    for row in full.get('texts', [])[:MAX_TEXTS]:
        bounds = _box(row.get('normalized'), [1, 1]) if isinstance(row, dict) else None
        if not bounds:
            continue
        duplicate = next((r for r in texts if _key(r.get('text')) == _key(row.get('text'))
                          and _box(r.get('normalized'), [1, 1])
                          and _iou(r['normalized'], bounds) >= .65), None)
        if duplicate is None:
            texts.append(deepcopy(row))
        elif _finite(row.get('confidence')) and row['confidence'] > duplicate.get('confidence', -1):
            duplicate.update(deepcopy(row), origin='local_ocr_with_supplemental_confirmation')
    truncated = len(texts) > MAX_TEXTS or bool(full.get('ocr', {}).get('truncated'))
    result['texts'] = texts[:MAX_TEXTS]
    for index, row in enumerate(result['texts']):
        row['id'] = f'ocr_{index}'
    result['status'] = 'ready'
    result['ocr'] = {**result.get('ocr', {}), 'status': 'ready', 'truncated': truncated,
        'supplemental_observations': True,
        'coverage': 'full_plus_regions' if full.get('status') == 'ready' and not full.get('ocr', {}).get('region') else 'regions_only'}
    regions = list(result['ocr'].get('supplemental_regions') or [])
    region = partial.get('ocr', {}).get('region')
    if region not in regions:
        regions.append(deepcopy(region))
    result['ocr']['supplemental_regions'] = regions[-12:]
    if truncated:
        result.setdefault('warnings', []).append('Combined OCR observations were truncated; unlisted text is not proven absent')
    result['warnings'] = list(dict.fromkeys(result.get('warnings', [])))[:16]
    return result


def _adjacent(a, b):
    """Reading neighbors, never distant strings joined merely to match copy."""
    aw, ah = a[2]-a[0], a[3]-a[1]
    bw, bh = b[2]-b[0], b[3]-b[1]
    overlap_y = max(0., min(a[3], b[3])-max(a[1], b[1]))
    gap_x = b[0]-a[2]
    if overlap_y >= .5*min(ah, bh) and -.1*min(aw, bw) <= gap_x <= max(ah, bh)*2.5:
        return True
    gap_y = b[1]-a[3]
    overlap_x = max(0., min(a[2], b[2])-max(a[0], b[0]))
    return (-.15*min(ah, bh) <= gap_y <= max(ah, bh)*1.4
            and b[1] > a[1]+min(ah, bh)*.5 and overlap_x >= .25*min(aw, bw))


def _candidate(rows, method):
    box = _union([r['normalized'] for r in rows])
    return dict(ocr_ids=[r['id'] for r in rows], text=''.join(r['text'] for r in rows),
                box=box, confidence=min(r['confidence'] for r in rows), method=method)


def locate_text(report, text, estimated_box=None):
    """Locate by OCR text plus geometry; an estimate never selects ambiguity."""
    estimated = _box(estimated_box, [1, 1])
    result = dict(status='unresolved', tool_box=None, confidence=None, candidates=[],
                  estimated_source_box=estimated, evidence={
                      'source': 'local_ocr', 'estimated_box_used_for_matching': False,
                      'accuracy': 'OCR detector region, not a one-pixel guarantee',
                      'normalization': 'NFKC, whitespace, case and typographic equivalents; decimal points, signs and comparison operators retained'})
    if not isinstance(report, dict) or report.get('status') != 'ready':
        result['evidence']['reason'] = 'source_ocr_unavailable'
        return result
    target = _key(text)
    if not target or len(target) > 800:
        result['evidence']['reason'] = 'empty_or_oversized_target'
        return result
    rows = [r for r in report.get('texts', [])[:MAX_TEXTS]
            if isinstance(r, dict) and _box(r.get('normalized'), [1, 1])
            and _finite(r.get('confidence')) and 0 <= r['confidence'] <= 1
            and isinstance(r.get('text'), str) and isinstance(r.get('id'), str)]
    try:
        matching_size = _size(report.get('source_size', report.get('canvas', [1, 1])))
    except ValueError:
        matching_size = [1, 1]
    candidates = [_candidate([r], 'exact_normalized_text') for r in rows if _key(r['text']) == target]
    # Search exact adjacent reading groups. The bounded queue cannot turn a
    # full page of coincidental numbers into an unbounded combinatorial search.
    queue = deque(([_i], _key(r['text'])) for _i, r in enumerate(rows)
                  if _key(r['text']) and target.startswith(_key(r['text'])) and _key(r['text']) != target)
    examined = 0
    while queue and examined < 512 and len(candidates) < 32:
        indexes, prefix = queue.popleft()
        examined += 1
        if len(indexes) >= MAX_MATCH_PARTS:
            continue
        for index, row in enumerate(rows):
            piece = _key(row['text'])
            if index in indexes or not piece or not target.startswith(prefix+piece):
                continue
            if not _adjacent(_pixels(rows[indexes[-1]]['normalized'], matching_size),
                             _pixels(row['normalized'], matching_size)):
                continue
            combined = prefix+piece
            updated = [*indexes, index]
            if combined == target:
                candidates.append(_candidate([rows[i] for i in updated], 'adjacent_text_group'))
            else:
                queue.append((updated, combined))
    unique = []
    for candidate in candidates:
        same = next((c for c in unique if _iou(c['box'], candidate['box']) >= .92), None)
        if same is None:
            unique.append(candidate)
        elif candidate['confidence'] > same['confidence']:
            unique[unique.index(same)] = candidate
    result['candidates'] = unique[:MAX_CANDIDATES]
    result['evidence'].update(candidate_count=len(unique), candidate_list_truncated=len(unique)>MAX_CANDIDATES,
                              search_truncated=bool(queue), ocr_truncated=bool(report.get('ocr', {}).get('truncated')))
    if len(unique) > 1:
        result['status'] = 'ambiguous'
        result['evidence']['reason'] = 'multiple_text_locations; inspect candidates instead of snapping to an estimated ROI'
    elif unique and unique[0]['confidence'] >= MIN_CONFIDENCE and not queue:
        chosen = unique[0]
        result.update(status='located', tool_box=chosen['box'], confidence=chosen['confidence'])
        result['evidence']['reason'] = 'one high-confidence exact text correspondence; source pixels still available for recheck'
    elif unique:
        result['confidence'] = unique[0]['confidence']
        result['evidence']['reason'] = 'low_confidence_or_incomplete_search; enlarge or re-run local inspection'
    else:
        # A containing OCR line can be shown for manual recheck, but must not
        # be advertised as the exact box of a substring or an invented glyph.
        partial = [_candidate([r], 'containing_line_not_exact_bounds') for r in rows
                   if len(target) >= 2 and target in _key(r['text'])]
        result['candidates'] = partial[:MAX_CANDIDATES]
        result['evidence']['reason'] = 'target_not_uniquely_localized; absence is not established'
    return result


def _render_boxes(measured, text, canvas):
    rows = _get(measured, 'texts', []) or []
    rows = [r for r in list(rows)[:256] if _get(r, 'visible', True) is not False]
    target = _key(text)
    exact = [r for r in rows if target and _key(_get(r, 'text')) == target]
    groups = []
    for row in exact:
        groups.append([row])
    if not exact:
        # Use the same adjacency constraint for separately styled number/unit
        # nodes. Do not union every coincidental occurrence across the canvas.
        probes = []
        mapping = {}
        for index, row in enumerate(rows):
            box = _get(row, 'box')
            if (not isinstance(box, (list, tuple)) or len(box) != 4
                    or any(not _finite(v) for v in box) or box[2] <= 0 or box[3] <= 0):
                continue
            ltrb = [box[0], box[1], box[0]+box[2], box[1]+box[3]]
            if not _box(ltrb, canvas):
                continue
            identity = str(index)
            mapping[identity] = row
            probes.append(dict(id=identity, text=str(_get(row, 'text', '')), confidence=1., normalized=_normal(ltrb, canvas)))
        match = locate_text(dict(status='ready', texts=probes, ocr={}, canvas=canvas), text)
        for candidate in match['candidates']:
            if candidate['method'] != 'containing_line_not_exact_bounds':
                groups.append([mapping[i] for i in candidate['ocr_ids']])
    result = []
    for group in groups:
        bounds, kinds = [], []
        for row in group:
            ink = _get(row, 'ink_rects')
            if isinstance(ink, list) and ink:
                values = []
                for rect in ink[:256]:
                    # Browser ink rectangles use x/y/width/height, like DOM.
                    if isinstance(rect, dict):
                        x, y = rect.get('x', rect.get('left')), rect.get('y', rect.get('top'))
                        w = rect.get('width', rect.get('right')-x if _finite(rect.get('right')) and _finite(x) else None)
                        h = rect.get('height', rect.get('bottom')-y if _finite(rect.get('bottom')) and _finite(y) else None)
                        if all(_finite(v) for v in (x, y, w, h)) and w > 0 and h > 0:
                            values.append([x, y, x+w, y+h])
                    elif isinstance(rect, (list, tuple)) and len(rect) == 4 and all(_finite(v) for v in rect) and rect[2] > 0 and rect[3] > 0:
                        values.append([rect[0], rect[1], rect[0]+rect[2], rect[1]+rect[3]])
                if values:
                    bounds.append(_union(values)); kinds.append('browser_glyph_ink_metrics'); continue
            box = _get(row, 'box')
            if (isinstance(box, (list, tuple)) and len(box) == 4 and all(_finite(v) for v in box)
                    and box[2] > 0 and box[3] > 0):
                bounds.append([box[0], box[1], box[0]+box[2], box[1]+box[3]])
                kinds.append('browser_layout_box')
        if bounds:
            combined = _union(bounds)
            result.append(dict(box=_normal(combined, canvas), ids=[_get(r, 'id') for r in group],
                               geometry_kind='browser_glyph_ink_metrics' if all(k=='browser_glyph_ink_metrics' for k in kinds) else 'browser_layout_box',
                               out_of_canvas=not bool(_box(combined, canvas))))
    return result


def compare_text_geometry(report, inventory, measured, canvas):
    """Numeric observations for DS, never acceptance or automatic redesign."""
    canvas = _size(canvas)
    source = report if isinstance(report, dict) else {}
    if source.get('canvas') != canvas:
        source = {'status': 'unavailable'}
    labels = _get(inventory, 'labels')
    if labels is None:
        labels = _get(inventory, 'texts', [])
    results = []
    for index, label in enumerate(list(labels or [])[:MAX_LABELS]):
        text = str(_get(label, 'text', ''))
        estimated = _get(label, 'source_box', _get(label, 'box'))
        location = locate_text(source, text, estimated)
        actual = _render_boxes(measured, text, canvas)
        difference = None
        if location['status'] == 'located' and len(actual) == 1:
            original = _pixels(location['tool_box'], canvas)
            rendered = _pixels(actual[0]['box'], canvas)
            difference = dict(
                source_ltrb=original, rendered_ltrb=rendered,
                center=[round((rendered[0]+rendered[2]-original[0]-original[2])/2, 4),
                        round((rendered[1]+rendered[3]-original[1]-original[3])/2, 4)],
                edges=dict(zip(('left', 'top', 'right', 'bottom'), [round(r-s, 4) for r, s in zip(rendered, original)])),
                width=round((rendered[2]-rendered[0])-(original[2]-original[0]), 4),
                height=round((rendered[3]-rendered[1])-(original[3]-original[1]), 4),
                sign='rendered minus source, in output canvas pixels',
                actual_geometry=actual[0]['geometry_kind'],
                interpretation='Measured offset relative to the OCR region, NOT a mandatory move or font-size correction. OCR bounds and browser glyph/layout bounds differ; no pixel-exact accuracy is claimed.')
        results.append(dict(label_id=f'label_{index}', text=text, source_status=location['status'],
            source_box=location['tool_box'], estimated_source_box=location['estimated_source_box'],
            candidates=location['candidates'], confidence=location['confidence'], evidence=location['evidence'],
            actual_boxes=[row['box'] for row in actual],
            matched_render_ids=[identity for row in actual for identity in row['ids']],
            actual_status='located' if len(actual)==1 else 'ambiguous' if actual else 'unresolved',
            actual_out_of_canvas=any(row['out_of_canvas'] for row in actual), delta_px=difference,
            automatic_quality_verdict=False))
    return results
