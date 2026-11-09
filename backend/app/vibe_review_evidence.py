"""Version-bound local evidence for a disputed visual review.

This tool measures/crops existing pixels. It never edits an asset, decides
whether a line is a residual, or substitutes an estimated ROI for OCR/DOM.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from pathlib import Path

from PIL import Image

from .vibe_code_inspection import (
    _bound_source_measurements, _canvas, _dom_evidence, _measured_region,
    _open, _pixel_window, _sheet, GAP, HEADER,
)
from .vibe_source_measurements import compare_text_geometry, locate_text


VERSION = 'ocv-review-evidence-v1'
MAX_WINDOWS = 3
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_INPUT_PIXELS = 40_000_000
MAX_TOTAL_INPUT_PIXELS = 60_000_000
MAX_ANCHORS = 16


def _region(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in value)
            or value[0] >= value[2] or value[1] >= value[3]):
        return None
    return list(value)


def _requests(value, name, max_chars):
    if value is None:
        return []
    if (not isinstance(value, (list, tuple)) or len(value) > MAX_ANCHORS
            or any(not isinstance(v, str) or not v.strip() or len(v) > max_chars for v in value)):
        raise ValueError(f'{name} must contain at most {MAX_ANCHORS} nonempty bounded strings')
    return list(dict.fromkeys(v.strip() for v in value))


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _preflight(paths):
    total = 0
    for role, path in paths.items():
        try:
            if path is None or not Path(path).is_file() or Path(path).stat().st_size > MAX_INPUT_BYTES:
                raise ValueError('missing or oversized file')
            with Image.open(path) as header:
                pixels = header.width * header.height
                if (pixels <= 0 or pixels > MAX_INPUT_PIXELS or getattr(header, 'n_frames', 1) != 1):
                    raise ValueError('invalid dimensions or multiple frames')
                total += pixels
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise ValueError(f'{role}: invalid bound raster image ({exc})') from None
    if total > MAX_TOTAL_INPUT_PIXELS:
        raise ValueError('review evidence images exceed the aggregate decode pixel budget')


def _same_aspect(size, canvas):
    width, height = size
    error = (abs(canvas[1]-height*canvas[0]/width) if width >= height
             else abs(canvas[0]-width*canvas[1]/height))
    return error <= 1.000001


def _expand(regions):
    left, top = min(b[0] for b in regions), min(b[1] for b in regions)
    right, bottom = max(b[2] for b in regions), max(b[3] for b in regions)
    # Include a measured neighborhood, not just the ink box: disputed callout
    # lines commonly sit beside a label. This is still a search window only.
    px, py = max(.05, (right-left)*.35), max(.035, (bottom-top)*.8)
    return [max(0., left-px), max(0., top-py), min(1., right+px), min(1., bottom+py)]


def _intersects(box, window, *, line=False):
    if line:
        box = [min(box[0], box[2]), min(box[1], box[3]),
               max(box[0], box[2]), max(box[1], box[3])]
        return min(box[2], window[2]) >= max(box[0], window[0]) and min(box[3], window[3]) >= max(box[1], window[1])
    return min(box[2], window[2]) > max(box[0], window[0]) and min(box[3], window[3]) > max(box[1], window[1])


def _local_candidates(report, window, binding_status):
    if report is None:
        return dict(available=False, reason=binding_status, texts=[], lines=[], boxes=[])
    result = dict(available=True, image_id=report.get('image_id'), source_sha256=report.get('source_sha256'),
        source_size=deepcopy(report.get('source_size')), canvas=deepcopy(report.get('canvas')),
        ocr=deepcopy(report.get('ocr', {})), geometry=deepcopy(report.get('geometry', {})),
        warnings=deepcopy(report.get('warnings', [])), truncated={},
        coordinate_contract='normalized: full-image LTRB for text/boxes, x1,y1,x2,y2 for lines; canvas_pixels use the same formats. '
            'All geometric candidates are unassigned observations, never proof of residuals or layer ownership.')
    fields = ('id', 'image_id', 'text', 'confidence', 'normalized', 'canvas_pixels',
              'source_pixels', 'orientation', 'kind', 'origin')
    for name, limit in (('texts', 48), ('lines', 32), ('boxes', 24)):
        matches = []
        for row in report.get(name, [])[:512]:
            if not isinstance(row, dict):
                continue
            coords = row.get('normalized')
            if name == 'lines':
                valid = (isinstance(coords, (list, tuple)) and len(coords) == 4
                    and all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in coords))
            else:
                valid = _region(coords) is not None
            if not valid or not _intersects(coords, window, line=name == 'lines'):
                continue
            item = {key: deepcopy(row[key]) for key in fields if key in row}
            item.update(verified=False, semantic_role='unassigned')
            matches.append(item)
        result[name] = matches[:limit]
        result['truncated'][name] = len(matches) > limit or len(report.get(name, [])) > 512
    return result


def _dom_rows(measurements):
    rows = []
    for kind in ('texts', 'assets', 'groups'):
        for row in list(measurements.get(kind, []) or [])[:256]:
            if isinstance(row, dict):
                rows.append((kind, row))
    return rows


def _geometry(source, measurements, canvas, query):
    return compare_text_geometry(source, {'labels': [{'text': query}]}, measurements, list(canvas))[0]


def build_review_evidence(*, core, background, poster, canvas, measurements,
                          source_measurements, background_measurements,
                          output_dir, preview_id, element_ids=None, text_queries=None, region=None):
    """Return same-window core/background/poster sheets and numeric evidence.

    ``measurements`` is the current poster browser receipt (texts/assets/groups,
    element box = canvas-pixel xywh), not a keyframe receipt. OCR/OpenCV reports
    must match each input's bytes, oriented size, and canvas. ``region`` is a
    caller-chosen search window; it never disambiguates repeated text or claims
    a verified location. Missing anchors return report.ok=False without crops.
    """
    canvas = _canvas(canvas)
    element_ids = _requests(element_ids, 'element_ids', 160)
    text_queries = _requests(text_queries, 'text_queries', 800)
    if region is not None and _region(region) is None:
        raise ValueError('region must be a nonempty full-canvas normalized LTRB search window in [0,1]')
    if not isinstance(measurements, dict) or measurements.get('poster') is False:
        raise ValueError('measurements must be the current poster browser receipt, not a motion keyframe')
    if not isinstance(preview_id, str) or not preview_id or len(preview_id) > 160:
        raise ValueError('preview_id must identify the bound current preview')
    paths = dict(core=core, background=background, poster=poster)
    _preflight(paths)
    opened = {}
    try:
        for role, path in paths.items():
            opened[role] = _open(path, role)
            if not _same_aspect(opened[role].size, canvas):
                raise ValueError(f'{role} and canvas aspect ratios differ; cannot invent a same-window transform')
        bindings = {role: dict(role=role, source_sha256=_sha(path), source_size=list(opened[role].size),
                               coordinate_space='EXIF-oriented full image') for role, path in paths.items()}
        bound_source, source_reason = _bound_source_measurements(source_measurements, core, list(opened['core'].size), canvas)
        bound_background, background_reason = _bound_source_measurements(background_measurements, background,
            list(opened['background'].size), canvas)
        report = dict(version=VERSION, ok=True, preview_id=preview_id, canvas=list(canvas),
            source_bindings=bindings, anchors=[], windows=[], warnings=[],
            measurement_binding=dict(core=dict(available=bound_source is not None, reason=source_reason),
                                     background=dict(available=bound_background is not None, reason=background_reason)),
            semantics='Every sheet shows the SAME normalized window: core original, actual prepared background, current rendered poster. '
                'A search window is not a verified text location. OCR boxes are detected regions, DOM boxes are browser geometry. '
                'Numeric deltas are observations, not mandatory corrections. No residual, layer-origin, or quality verdict is inferred.',
            automatic_quality_verdict=False)
        proposed = []
        rows = _dom_rows(measurements)

        def propose(window, anchor):
            for previous in proposed:
                if all(abs(a-b) <= 1e-6 for a, b in zip(previous['region'], window)):
                    previous['anchor_indexes'].append(anchor)
                    return
            proposed.append(dict(region=window, anchor_indexes=[anchor]))

        for identity in element_ids:
            found = [(kind, row) for kind, row in rows if row.get('id') == identity]
            usable = [(kind, row, _measured_region(row, canvas)) for kind, row in found
                      if row.get('visible', True) is not False and row.get('painted', True) is not False]
            usable = [(kind, row, box) for kind, row, box in usable if box]
            anchor = dict(kind='browser_element', element_id=identity, status='located' if len(usable) == 1
                          else 'ambiguous' if usable else 'unresolved', matches=[])
            index = len(report['anchors'])
            for kind, row, box in usable:
                item = dict(kind=kind, element_id=identity, box=deepcopy(row.get('box')),
                    box_format='canvas_pixel_xywh', normalized_ltrb=box,
                    source='browser_dom', text=str(row.get('text', ''))[:800])
                regions = [box]
                if kind == 'texts' and item['text']:
                    item['text_geometry'] = _geometry(bound_source, {'texts': [row]}, canvas, item['text'])
                    source_box = item['text_geometry'].get('source_box')
                    if item['text_geometry']['source_status'] == 'located' and _region(source_box):
                        regions.append(source_box)
                anchor['matches'].append(item)
                propose(_expand(regions), index)
            report['anchors'].append(anchor)

        for query in text_queries:
            location = locate_text(bound_source, query)
            anchor = dict(kind='text_query', text=query, core=location,
                background=locate_text(bound_background, query),
                text_geometry=_geometry(bound_source, measurements, canvas, query))
            index = len(report['anchors'])
            report['anchors'].append(anchor)
            # An ambiguous OCR occurrence gets its own diagnostic window. We
            # do not choose the candidate nearest the model's estimated ROI.
            for candidate in location.get('candidates', []):
                box = _region(candidate.get('box'))
                if box:
                    propose(_expand([box]), index)

        if region is not None:
            index = len(report['anchors'])
            report['anchors'].append(dict(kind='requested_search_window', region=list(region), verified=False,
                meaning='caller requested a region to inspect; not a measurement or semantic location'))
            propose(list(region), index)
        if not proposed:
            report.update(ok=False, error='No measured anchor or explicit search window is available.',
                next='Choose an existing browser element_id, a locally detected exact text query, or provide a normalized search region. '
                     'Unresolved OCR does not establish that text is absent; no position was guessed.')
            return dict(images={}, report=report)
        chosen = proposed[:MAX_WINDOWS]
        report['window_limit'] = MAX_WINDOWS
        report['omitted_window_count'] = max(0, len(proposed)-MAX_WINDOWS)
        if report['omitted_window_count']:
            report['warnings'].append('Only the first three requested/measured windows are shown; remaining candidates stay in anchors. '
                                      'Request a smaller anchor set to inspect omitted windows.')
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        images = {}
        for number, choice in enumerate(chosen):
            window = choice['region']
            common_size = (max(1, math.ceil((window[2]-window[0])*canvas[0])),
                           max(1, math.ceil((window[3]-window[1])*canvas[1])))
            panels, mappings = [], []
            try:
                for role, image in opened.items():
                    pixel_window = _pixel_window(window, image.size)
                    crop = image.crop(pixel_window)
                    try:
                        panels.append(crop.resize(common_size, Image.Resampling.LANCZOS))
                    finally:
                        crop.close()
                    mappings.append(dict(**bindings[role], source_pixel_window=pixel_window,
                        source_pixel_window_format='ltrb_right_bottom_exclusive',
                        normalized_window=list(window), resampled_to_canvas_window=list(common_size)))
                token = hashlib.sha256((preview_id+repr(window)+repr(bindings)).encode()).hexdigest()[:20]
                identity = f'review_evidence_{token}_{number}'
                sheet, scale = _sheet(panels, ['CORE / original', 'BACKGROUND / actual input', 'POSTER / rendered'],
                    f'{preview_id} | measured same-window comparison {number+1}',
                    footer='Same normalized window. No residual/source/quality verdict; inspect pixels with bound measurements.',
                    common_size=common_size, panel_side=600)
                try:
                    if sheet.width > 2000:
                        raise ValueError('review evidence sheet exceeds display width budget')
                    path = output/(identity+'.png')
                    sheet.save(path)
                    images[identity] = path
                    panel_width = max(1, round(common_size[0]*scale))
                    panel_height = max(1, round(common_size[1]*scale))
                    for i, mapping in enumerate(mappings):
                        mapping['sheet_box_xywh'] = [GAP+i*(panel_width+GAP), HEADER, panel_width, panel_height]
                        mapping['display_scale_from_canvas_window'] = scale
                    selected_rows = [row for _, row in rows]
                    report['windows'].append(dict(image_id=identity, region=window,
                        region_role='diagnostic_search_window_not_verified_location', anchor_indexes=choice['anchor_indexes'],
                        sheet_size=list(sheet.size), panels=mappings,
                        core_candidates=_local_candidates(bound_source, window, source_reason),
                        background_candidates=_local_candidates(bound_background, window, background_reason),
                        browser_elements=_dom_evidence(selected_rows, window, canvas),
                        browser_coordinate_contract='box is canvas-pixel xywh; rects/fragments use their explicit browser fields. '
                            'Browser geometry identifies rendered elements, not the ownership of matching source pixels.',
                        automatic_quality_verdict=False))
                finally:
                    sheet.close()
            finally:
                for panel in panels:
                    panel.close()
        return dict(images=images, report=report)
    finally:
        for image in opened.values():
            image.close()
