"""Lossless-of-decisions prompt projections; full measurements remain on disk.

Remove repeated coordinate representations and per-row boilerplate, never
confidence, ambiguity, source identity, actual offsets or warnings. These
projections are not validation inputs or persisted measurement fingerprints.
"""
from copy import deepcopy
import math


def recovery_conflict(error):
    """Extract typed validation evidence; never parse model-written blockers."""
    value = getattr(error, 'conflict', None)
    if isinstance(value, dict):
        return deepcopy(value)
    errors = getattr(error, 'errors', None)
    if callable(errors):
        for row in errors():
            value = getattr((row.get('ctx') or {}).get('error'), 'conflict', None)
            if isinstance(value, dict):
                return deepcopy(value)
    return None


def recovery_search_region(conflict):
    """A broad diagnostic search window, NOT a verified text location.

    Expanding around the conflicting declarations gives local OCR a chance to
    find a nearby omitted glyph. Quantization reuses measurements when the
    model merely nudges the same erroneous declaration by tiny amounts.
    """
    boxes = []
    for name in ('submitted', 'existing'):
        box = (conflict.get(name) or {}).get('source_box')
        if (isinstance(box, (list, tuple)) and len(box) == 4
                and all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in box)
                and box[0] < box[2] and box[1] < box[3]):
            boxes.append(box)
    if not boxes:
        return None
    left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
    right, bottom = max(b[2] for b in boxes), max(b[3] for b in boxes)
    pad_x, pad_y = max(.05, (right-left)*1.5), max(.03, (bottom-top)*1.5)
    return [max(0., math.floor((left-pad_x)*100)/100),
            max(0., math.floor((top-pad_y)*100)/100),
            min(1., math.ceil((right+pad_x)*100)/100),
            min(1., math.ceil((bottom+pad_y)*100)/100)]


def _pick(row, keys):
    return {key: deepcopy(row[key]) for key in keys if key in row}


def source_prompt(report):
    if not isinstance(report, dict):
        return deepcopy(report)
    result = {key: deepcopy(value) for key, value in report.items()
              if key not in {'texts', 'lines', 'boxes', 'inference_seconds', 'ocr_cache_hit'}}
    result['texts'] = [_pick(row, ('id', 'image_id', 'text', 'confidence', 'normalized', 'canvas_pixels',
                                  'origin', 'ocr_region')) for row in report.get('texts', [])]
    result['lines'] = [_pick(row, ('id', 'image_id', 'canvas_pixels', 'orientation', 'source_image_id'))
                       for row in report.get('lines', [])]
    result['boxes'] = [_pick(row, ('id', 'image_id', 'canvas_pixels', 'source_image_id'))
                       for row in report.get('boxes', [])]
    result['measurement_semantics'] = (
        'Texts: OCR detected regions, not exact glyph ink; confidence is recognition confidence. '
        'normalized is full-source LTRB; canvas_pixels is output-canvas LTRB. '
        'Lines: canvas_pixels is x1,y1,x2,y2. Boxes: canvas_pixels is LTRB. '
        'All lines/boxes are unassigned geometric candidates, NOT confirmed axes/panels/residuals. '
        'Use this report image_id/source_sha256/asset_id; never transfer a line ID between images. '
        'Missing detection does not prove absent content. Full raw measurements remain in the local audit.')
    return result


def geometry_prompt(geometry):
    return {key: source_prompt(value) for key, value in geometry.items()}


def inspection_prompt(report):
    result = deepcopy(report)
    canonical = {row.get('label_id'): row for row in result.get('text_measurements', [])
                 if isinstance(row, dict) and row.get('label_id')}

    def reference_duplicate(row):
        measurement = row.get('geometry_measurement')
        identity = measurement.get('label_id') if isinstance(measurement, dict) else None
        # Older receipts can have only a local measurement, or a differently
        # versioned canonical row. Preserve that evidence instead of replacing
        # it with a dangling or inaccurate reference.
        if identity and canonical.get(identity) == measurement:
            row.pop('geometry_measurement')
            row['geometry_available_in'] = 'inspection.text_measurements by label_id'

    # Every text's full geometry already has one canonical row below. Local
    # comparison-sheet items need its identity, not a second copy of the row.
    for item in result.get('items', []):
        item.pop('intersecting_texts', None)
        reference_duplicate(item)
        for row in item.get('unresolved_texts', []):
            reference_duplicate(row)
    for row in result.get('text_measurements', []):
        delta = row.get('delta_px')
        if isinstance(delta, dict):
            # Same interpretation appears in coordinate_contract for all rows.
            delta.pop('interpretation', None)
            delta.pop('sign', None)
    result['delta_convention'] = (
        'delta_px = rendered minus source, in output canvas pixels. It is an observation, '
        'not an automatic move/font-size instruction; OCR and browser ink/layout bounds differ. '
        'Keep uncertainty and check the current same-window images.')
    return result
