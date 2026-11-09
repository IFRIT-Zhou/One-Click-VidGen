"""Read-only visual evidence for model-authored layered posters.

These sheets crop/resample existing pixels and add labelled diagnostic overlays.
They do not generate source content, choose a material variant, repair a mask,
or judge semantic completeness.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from pathlib import Path
import unicodedata

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps


VERSION = 'ocv-code-inspection-v4'
MAX_INPUT_PIXELS = 40_000_000
MAX_TOTAL_PIXELS = 12_000_000
MAX_MATERIAL_IMAGES = 8
MAX_MATERIAL_EDGE_IMAGES = 8
MAX_PREVIEW_IMAGES = 12
PANEL_SIDE = 600
MATERIAL_PANEL_SIDE = 360
MATERIAL_CONTEXT_HEIGHT = 260
MATERIAL_EDGE_SHEET_SIZE = (824, 570)
GAP = 16
HEADER = 76
FOOTER = 36


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _rows(value, first, fallback=None):
    rows = _get(value, first)
    if rows is None and fallback:
        rows = _get(value, fallback)
    return list(rows or [])[:64]


def _canvas(canvas):
    if (not isinstance(canvas, (tuple, list)) or len(canvas) != 2
            or any(type(v) is not int or not 64 <= v <= 4096 for v in canvas)
            or canvas[0]*canvas[1] > 8_500_000):
        raise ValueError('Inspection canvas must contain two bounded integer pixel dimensions')
    return tuple(canvas)


def _open(path, label):
    try:
        if path is None or not Path(path).is_file() or Path(path).stat().st_size > 64*1024*1024:
            raise ValueError('missing or oversized image')
        with Image.open(path) as source:
            if (source.width*source.height > MAX_INPUT_PIXELS
                    or source.width <= 0 or source.height <= 0
                    or getattr(source, 'n_frames', 1) != 1):
                raise ValueError('invalid dimensions or multiple frames')
            return ImageOps.exif_transpose(source).convert('RGBA')
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError(f'{label}: invalid bound raster image ({exc})') from None


def _core(sources, canvas):
    image = _open(sources.get('core'), 'core')
    width, height = image.size
    delta = (abs(canvas[1]-height*canvas[0]/width) if width >= height
             else abs(canvas[0]-width*canvas[1]/height))
    if delta > 1.000001:
        image.close()
        raise ValueError('core and canvas aspect ratios differ; cannot invent a comparison transform')
    return image


def _region(value):
    if (not isinstance(value, (tuple, list)) or len(value) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
            or value[0] >= value[2] or value[1] >= value[3]):
        return None
    clipped = [max(0., min(1., float(v))) for v in value]
    return clipped if clipped[0] < clipped[2] and clipped[1] < clipped[3] else None


def _pixel_window(region, size):
    width, height = size
    return [max(0, min(width, math.floor(region[0]*width))),
            max(0, min(height, math.floor(region[1]*height))),
            max(0, min(width, math.ceil(region[2]*width))),
            max(0, min(height, math.ceil(region[3]*height)))]


def _font(size=18):
    for name in ('arial.ttf', 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _english(value):
    return str(value).encode('ascii', errors='replace').decode('ascii')[:100]


def _checker(size):
    image = Image.new('RGBA', size, (215, 219, 224, 255))
    draw = ImageDraw.Draw(image)
    cell = max(8, min(size)//24)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x//cell+y//cell) % 2:
                draw.rectangle((x, y, x+cell-1, y+cell-1), fill=(165, 171, 179, 255))
    return image


def _sheet(panels, headings, identity, *, footer, common_size=None, panel_side=PANEL_SIDE):
    extent = common_size or (max(im.width for im in panels), max(im.height for im in panels))
    scale = min(panel_side/extent[0], panel_side/extent[1], 3.)
    size = (max(1, round(extent[0]*scale)), max(1, round(extent[1]*scale)))
    result = Image.new('RGB', (len(panels)*size[0]+(len(panels)+1)*GAP, size[1]+HEADER+FOOTER), '#15202b')
    draw = ImageDraw.Draw(result)
    draw.text((GAP, 8), _english(identity), fill='white', font=_font(20))
    for index, (panel, heading) in enumerate(zip(panels, headings)):
        x = GAP+index*(size[0]+GAP)
        draw.text((x, 39), _english(heading), fill='#cbd6e2', font=_font(16))
        tile = _checker(size)
        scaled = panel.resize((max(1, round(panel.width*scale)), max(1, round(panel.height*scale))), Image.Resampling.LANCZOS)
        # All panels share the same pixel-to-display scale and top-left origin.
        tile.alpha_composite(scaled, (0, 0))
        result.paste(tile.convert('RGB'), (x, HEADER))
        tile.close(); scaled.close()
    draw.text((GAP, result.height-26), _english(footer), fill='#cbd6e2', font=_font(15))
    return result, scale


def _expanded_source_window(crop, size):
    """Show pixels outside all four crop edges, without proposing a new crop."""
    width, height = size
    left, top, right, bottom = crop
    pad_x = max(16, math.ceil((right-left)*.25), math.ceil(width*.04))
    pad_y = max(16, math.ceil((bottom-top)*.25), math.ceil(height*.04))
    return [max(0, left-pad_x), max(0, top-pad_y),
            min(width, right+pad_x), min(height, bottom+pad_y)]


def _context_view(target, source, window, box, crop_window, *, context_outline=None):
    """Place immutable source pixels with explicitly labelled display overlays."""
    x, y, width, height = box
    patch = source.crop(window)
    scale = min(width/patch.width, height/patch.height, 3.)
    drawn_size = (max(1, round(patch.width*scale)), max(1, round(patch.height*scale)))
    left = x+(width-drawn_size[0])//2
    top = y+(height-drawn_size[1])//2
    scaled = patch.resize(drawn_size, Image.Resampling.LANCZOS)
    flat = _checker(drawn_size)
    flat.alpha_composite(scaled)
    target.paste(flat.convert('RGB'), (left, top))
    sx, sy = drawn_size[0]/patch.width, drawn_size[1]/patch.height
    draw = ImageDraw.Draw(target)

    def outline(bounds, color):
        px = [left+round((bounds[0]-window[0])*sx), top+round((bounds[1]-window[1])*sy),
              left+round((bounds[2]-window[0])*sx), top+round((bounds[3]-window[1])*sy)]
        # Pixel windows are right/bottom exclusive; keep markers inside the
        # displayed image, including crops touching the full source boundary.
        px = [max(left, min(left+drawn_size[0]-1, px[0])),
              max(top, min(top+drawn_size[1]-1, px[1])),
              max(left, min(left+drawn_size[0]-1, px[2])),
              max(top, min(top+drawn_size[1]-1, px[3]))]
        draw.rectangle(px, outline=color, width=2)

    if context_outline is not None:
        outline(context_outline, '#50e6ff')
    outline(crop_window, '#ffd54f')
    patch.close(); scaled.close(); flat.close()
    return {'sheet_content_box': [left, top, drawn_size[0], drawn_size[1]],
            'sheet_content_box_format': 'xywh',
            'source_pixel_window': list(window), 'source_pixel_window_format': 'ltrb_right_bottom_exclusive',
            'display_scale_xy': [sx, sy],
            'mapping': 'sheet_x=left+(source_x-window_left)*scale_x; same for y'}


def _material_context_sheet(triptych, source, region, identity, source_id):
    """One bounded image: full source + neighborhood, then actual alpha views.

    The top row can expose content already missing in the original crop. The
    lower row only exposes differences introduced by preparation. They answer
    different questions and are never interpreted as semantic pass/fail here.
    """
    crop = _pixel_window(region, source.size)
    if crop[0] >= crop[2] or crop[1] >= crop[3]:
        raise ValueError('source crop has no pixel area')
    context = _expanded_source_window(crop, source.size)
    width = 3*MATERIAL_PANEL_SIDE+4*GAP
    row_height = MATERIAL_CONTEXT_HEIGHT+HEADER+FOOTER
    sheet = Image.new('RGB', (width, row_height+triptych.height), '#15202b')
    draw = ImageDraw.Draw(sheet)
    draw.text((GAP, 8), _english(identity+' | source='+str(source_id)), fill='white', font=_font(20))
    panel_width = (width-3*GAP)//2
    draw.text((GAP, 39), 'Full source (not the cropped original)', fill='#cbd6e2', font=_font(16))
    draw.text((2*GAP+panel_width, 39), 'Neighborhood beyond current crop', fill='#cbd6e2', font=_font(16))
    source_view = _context_view(sheet, source, [0, 0, *source.size],
        [GAP, HEADER, panel_width, MATERIAL_CONTEXT_HEIGHT], crop, context_outline=context)
    context_view = _context_view(sheet, source, context,
        [2*GAP+panel_width, HEADER, panel_width, MATERIAL_CONTEXT_HEIGHT], crop)
    draw.text((GAP, row_height-26), 'YELLOW = current crop; CYAN = neighborhood. Both are inspection marks, NOT original artwork.',
              fill='#cbd6e2', font=_font(14))
    triptych_left = (width-triptych.width)//2
    sheet.paste(triptych, (triptych_left, row_height))
    source_width, source_height = source.size
    normalized = lambda b: [b[0]/source_width, b[1]/source_height, b[2]/source_width, b[3]/source_height]
    evidence = {'available': True, 'source_id': source_id, 'source_size': list(source.size),
        'coordinate_space': 'EXIF-oriented bound source image, NOT core canvas unless source_id=core',
        'requested_source_crop': list(region), 'crop_pixel_window': crop,
        'pixel_aligned_source_crop': normalized(crop), 'context_pixel_window': context,
        'context_normalized_window': normalized(context), 'full_source_view': source_view,
        'neighborhood_view': context_view, 'variant_triptych_sheet_offset': [triptych_left, row_height],
        'overlays': {'yellow': 'current source crop boundary', 'cyan': 'expanded neighborhood boundary',
                     'not_part_of_source_content': True},
        'semantic_crop_verdict': 'not_inferred',
        'inspection_instruction': 'First compare the full source and pixels OUTSIDE the yellow crop border. '
            'A cropped original can already be incomplete or include foreign text/frame fragments. '
            'Then compare the lower original/prepared alpha views. Equal lower views do not prove a complete crop.'}
    return sheet, evidence


def _material_edge_sheet(source, region, identity, source_id):
    """Enlarge all four crop edges without deciding whether content crosses them.

    Vertical-edge bands are rotated 90 degrees counterclockwise for inspection,
    not distorted or replaced. Exact affine mappings refer to source pixel-edge
    coordinates; outside-source areas are never synthesized.
    """
    sw, sh = source.size
    left, top, right, bottom = _pixel_window(region, source.size)
    cw, ch = right-left, bottom-top
    normal_x, normal_y = max(12, math.ceil(cw*.25)), max(12, math.ceil(ch*.25))
    tangent_x, tangent_y = max(6, math.ceil(cw*.08)), max(6, math.ceil(ch*.08))
    definitions = [
        ('top', [left-tangent_x, top-normal_y, right+tangent_x, top+normal_y],
         [left, top, right, top], False, top > 0, 'below', 'above'),
        ('right', [right-normal_x, top-tangent_y, right+normal_x, bottom+tangent_y],
         [right, top, right, bottom], True, right < sw, 'below', 'above'),
        ('bottom', [left-tangent_x, bottom-normal_y, right+tangent_x, bottom+normal_y],
         [left, bottom, right, bottom], False, bottom < sh, 'above', 'below'),
        ('left', [left-normal_x, top-tangent_y, left+normal_x, bottom+tangent_y],
         [left, top, left, bottom], True, left > 0, 'above', 'below'),
    ]
    sheet = Image.new('RGB', MATERIAL_EDGE_SHEET_SIZE, '#15202b')
    draw = ImageDraw.Draw(sheet)
    draw.text((GAP, 7), _english(identity+' | source='+str(source_id)+' | four crop-edge checks'),
              fill='white', font=_font(18))
    draw.text((GAP, 32), 'Inspect source pixels on BOTH sides. Yellow lines are diagnostic, NOT original artwork.',
              fill='#cbd6e2', font=_font(14))
    panel_width = (sheet.width-3*GAP)//2
    panel_height = 230
    views = {}
    for index, (name, raw_window, boundary, rotated, outside_exists, inside_side, outside_side) in enumerate(definitions):
        window = [max(0, raw_window[0]), max(0, raw_window[1]),
                  min(sw, raw_window[2]), min(sh, raw_window[3])]
        patch = source.crop(window)
        if rotated:
            rotated_patch = patch.transpose(Image.Transpose.ROTATE_90)
            patch.close(); patch = rotated_patch
        col, row = index % 2, index // 2
        px, py = GAP+col*(panel_width+GAP), 66+row*(panel_height+GAP)
        orientation = ' | rotated 90 CCW' if rotated else ''
        draw.text((px, py), name.upper()+orientation, fill='#ffd54f', font=_font(15))
        normalized = [window[0]/sw, window[1]/sh, window[2]/sw, window[3]/sh]
        draw.text((px, py+21), 'source xyxy: '+','.join(f'{v:.4f}' for v in normalized),
                  fill='#cbd6e2', font=_font(12))
        directions = f'INSIDE {inside_side.upper()} | OUTSIDE {outside_side.upper()}'
        if not outside_exists:
            directions = f'INSIDE {inside_side.upper()} | OUTSIDE: no source pixels'
        draw.text((px, py+39), directions, fill='white', font=_font(12))
        image_box = [px, py+60, panel_width, 154]
        scale = min(image_box[2]/patch.width, image_box[3]/patch.height, 6.)
        size = (max(1, round(patch.width*scale)), max(1, round(patch.height*scale)))
        dx, dy = px+(panel_width-size[0])//2, image_box[1]+(image_box[3]-size[1])//2
        resized = patch.resize(size, Image.Resampling.LANCZOS)
        flat = _checker(size); flat.alpha_composite(resized)
        sheet.paste(flat.convert('RGB'), (dx, dy))
        sx, sy = size[0]/patch.width, size[1]/patch.height
        affine = ([0., sx, dx-window[1]*sx, -sy, 0., dy+window[2]*sy] if rotated
                  else [sx, 0., dx-window[0]*sx, 0., sy, dy-window[1]*sy])
        def project(x, y):
            return (max(dx, min(dx+size[0]-1, round(affine[0]*x+affine[1]*y+affine[2]))),
                    max(dy, min(dy+size[1]-1, round(affine[3]*x+affine[4]*y+affine[5]))))
        draw.line([project(*boundary[:2]), project(*boundary[2:])], fill='#ffd54f', width=2)
        draw.text((px, py+215), 'Full-source boundary reached; nothing is padded.' if not outside_exists
                  else 'Look for continuity across the line; no automatic verdict.', fill='#cbd6e2', font=_font(11))
        views[name] = {'source_id': source_id, 'source_size': [sw, sh],
            'source_pixel_window': window, 'source_normalized_window': normalized,
            'source_pixel_window_format': 'ltrb_right_bottom_exclusive', 'source_normalized_window_format': 'ltrb',
            'crop_edge_pixel_line': boundary,
            'crop_edge_full_source_normalized': [boundary[0]/sw, boundary[1]/sh, boundary[2]/sw, boundary[3]/sh],
            'sheet_content_box': [dx, dy, *size], 'sheet_content_box_format': 'xywh',
            'rotation_degrees_ccw': 90 if rotated else 0,
            'source_pixel_to_sheet_affine': affine,
            'mapping': 'sheet_x=a*source_x+b*source_y+c; sheet_y=d*source_x+e*source_y+f; source pixel centers are x+0.5,y+0.5',
            'inside_display_side': inside_side, 'outside_display_side': outside_side if outside_exists else None,
            'outside_source_pixels_available': outside_exists, 'source_boundary_reached': not outside_exists,
            'outside_unavailable_reason': None if outside_exists else 'Crop edge coincides with full-source boundary; outside pixels do not exist, no padding was generated.',
            'diagnostic_line_color': '#ffd54f', 'diagnostic_overlay_not_source_content': True,
            'semantic_crossing_verdict': 'not_inferred'}
        patch.close(); resized.close(); flat.close()
    draw.text((GAP, sheet.height-16), 'Coordinates always refer to the complete EXIF-oriented bound source. Page margins are not source pixels.',
              fill='#cbd6e2', font=_font(11))
    return sheet, views


def _base_report(kind, canvas, limit):
    return {'version': VERSION, 'kind': kind, 'canvas': list(canvas), 'image_count': 0,
            'total_pixels': 0, 'limits': {'max_images': limit, 'max_total_pixels': MAX_TOTAL_PIXELS,
                'max_sheet_width': 3*PANEL_SIDE+4*GAP}, 'items': [], 'skipped': [],
            'automatic_material_selection': False,
            'semantic_mask_verdict': 'not_inferred',
            'notes': ['Alpha removal is not automatically an error. Compare the original silhouette and details visually.',
                      'Transparent/background pixels cannot establish semantic completeness; the reviewing model must inspect the original contour.',
                      'Source content is only cropped/resampled/composited, never regenerated. Labelled bounds and alpha tints are diagnostic overlays, not source artwork.']}


def _save(sheet, folder, image_id, images, report):
    pixels = sheet.width*sheet.height
    if report['image_count'] >= report['limits']['max_images'] or report['total_pixels']+pixels > MAX_TOTAL_PIXELS:
        report['skipped'].append({'id': image_id, 'reason': 'inspection image/pixel budget exceeded'})
        return False
    target = folder/(image_id+'.png')
    if target.exists():
        raise FileExistsError('Inspection output will not overwrite an existing sheet: '+target.name)
    folder.mkdir(parents=True, exist_ok=True)
    sheet.save(target, 'PNG')
    images[image_id] = target
    report['image_count'] += 1
    report['total_pixels'] += pixels
    return True


def build_material_inspection(inventory, sources, assets, output_dir, canvas):
    """Return primary images, review-only edge images, and JSON-safe evidence.

    Accepts legacy Design, V2 Inventory, or equivalent dictionaries. Source and
    asset paths are taken only from the supplied server-owned mappings.
    ``review_images`` are supplementary edge sheets for material review only;
    callers must merge them explicitly and need not resend them to code authoring.
    All images, including review-only sheets, count toward the shared budget.
    """
    canvas = _canvas(canvas)
    core = _core(sources, canvas); core.close()
    folder, images, review_images = Path(output_dir), {}, {}
    report = _base_report('materials', canvas, MAX_MATERIAL_IMAGES+MAX_MATERIAL_EDGE_IMAGES)
    report['limits'].update(max_primary_images=MAX_MATERIAL_IMAGES, max_review_images=MAX_MATERIAL_EDGE_IMAGES)
    for index, subject in enumerate(_rows(inventory, 'subjects')):
        identity = f'subject_{index}'
        if index >= MAX_MATERIAL_IMAGES:
            report['skipped'].append({'id': identity, 'reason': 'subject inspection limit'})
            continue
        source_id = _get(subject, 'source', _get(subject, 'source_id'))
        region = _region(_get(subject, 'source_crop', _get(subject, 'crop')))
        # Existing original/prepared pairs can still expose a damaged mask
        # even when the optional source ROI is missing or invalid. Never
        # invent that ROI; only source-crop fallback requires valid bounds.
        original_bound = identity+'_original' in assets
        if not original_bound and (not region or source_id not in sources):
            report['skipped'].append({'id': identity, 'reason': 'no valid bound source crop'})
            continue
        original = prepared = missing = source = None
        try:
            source_context = {'available': False, 'source_id': source_id,
                'reason': 'No valid bound source crop; original/prepared alone cannot establish source-crop completeness.'}
            if region and source_id in sources:
                try:
                    source = _open(sources[source_id], str(source_id))
                except ValueError as exc:
                    source_context['reason'] = str(exc)
            if identity+'_original' in assets:
                original = _open(assets[identity+'_original'], identity+' original')
                original_origin = identity+'_original'
            else:
                if source is None:
                    raise ValueError(source_context['reason'])
                original = source.crop(_pixel_window(region, source.size))
                original_origin = 'bound source crop'
            prepared = _open(assets.get(identity), identity+' prepared')
            same_size = original.size == prepared.size
            original_alpha, prepared_alpha = original.getchannel('A'), prepared.getchannel('A')
            stats = {'prepared_alpha_min_max': list(prepared_alpha.getextrema()),
                     'prepared_nonzero_alpha_box': prepared_alpha.getbbox(), 'alpha_comparison_available': same_size}
            if same_size:
                lost = ImageChops.subtract(original_alpha, prepared_alpha)
                histogram = lost.histogram()
                stats.update(alpha_reduced_pixels=sum(histogram[1:]),
                             alpha_reduced_fraction=round(sum(histogram[1:])/(original.width*original.height), 6))
                red = Image.new('RGBA', original.size, (255, 32, 90, 255))
                tint = lost.point(lambda value: round(value*.72))
                missing = Image.composite(red, original, tint)
                lost.close(); red.close(); tint.close()
            else:
                missing = Image.new('RGBA', original.size, (85, 85, 85, 255))
                stats['note'] = 'Different prepared dimensions: no automatic alignment or alpha-loss comparison was inferred.'
            original_alpha.close(); prepared_alpha.close()
            panels = [original, prepared, missing]
            headings = [f'Original {original.width}x{original.height}', f'Prepared {prepared.width}x{prepared.height}',
                        'Red = alpha reduction' if same_size else 'No aligned alpha comparison']
            sheet, scale = _sheet(panels, headings, identity,
                footer='Same scale; alpha reduction is evidence, NOT a semantic failure verdict.',
                panel_side=MATERIAL_PANEL_SIDE)
            if source is not None and region:
                triptych = sheet
                sheet, source_context = _material_context_sheet(triptych, source, region, identity, source_id)
                triptych.close()
            image_id = f'material_inspection_{index:02d}'
            if _save(sheet, folder, image_id, images, report):
                item = {'image_id': image_id, 'asset_id': identity, 'source_id': source_id,
                    'source_crop': region, 'original_origin': original_origin,
                    'source_context': source_context,
                    'source_coordinate_note': None if region else 'Source ROI unavailable; comparison uses only the bound original/prepared raster pair.',
                    'original_size': list(original.size), 'prepared_size': list(prepared.size),
                    'display_scale': scale, 'panel_alignment': 'same pixel scale; top-left origin',
                    'treatment': _get(subject, 'treatment'), 'mask': stats,
                    'edge_evidence': {'available': False, 'reason': source_context.get('reason')}, 'edge_views': {}}
                if source is not None and region:
                    edge_sheet, edge_views = _material_edge_sheet(source, region, identity, source_id)
                    edge_id = f'material_edges_{index:02d}'
                    if _save(edge_sheet, folder, edge_id, review_images, report):
                        item['edge_evidence'] = {'available': True, 'image_id': edge_id, 'audience': 'material_review_only',
                            'check_order': ['top', 'right', 'bottom', 'left'],
                            'instruction': 'Inspect each edge for subject contours continuing outside the crop and unrelated text/frame fragments inside it. Do not infer completeness from alpha agreement.'}
                        item['edge_views'] = edge_views
                    else:
                        item['edge_evidence']['reason'] = 'Edge evidence unavailable: shared image/pixel budget exceeded; crop completeness was NOT established.'
                    edge_sheet.close()
                report['items'].append(item)
            sheet.close()
        except ValueError as exc:
            report['skipped'].append({'id': identity, 'reason': str(exc)})
        finally:
            for image in (original, prepared, missing, source):
                if image is not None:
                    image.close()
    report['primary_image_count'] = len(images)
    report['review_image_count'] = len(review_images)
    return {'images': images, 'review_images': review_images, 'report': report}


def _key(text):
    # This is only a soft correspondence hint, never factual conservation.
    return ''.join(char for char in unicodedata.normalize('NFKC', str(text or ''))
                   if not char.isspace() and not unicodedata.category(char).startswith('P'))


def _measured_region(row, canvas):
    value = _get(row, 'box')
    if (not isinstance(value, (tuple, list)) or len(value) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
            or value[2] <= 0 or value[3] <= 0):
        return None
    return _region([value[0]/canvas[0], value[1]/canvas[1],
                    (value[0]+value[2])/canvas[0], (value[1]+value[3])/canvas[1]])


def _subject_region(subject, index, inventory, measured, canvas):
    explicit = _region(_get(subject, 'source_box'))
    if explicit:
        return explicit
    source_id = _get(subject, 'source', _get(subject, 'source_id'))
    if source_id == 'core':
        return _region(_get(subject, 'source_crop', _get(subject, 'crop')))
    identity = f'subject_{index}'
    for owner in (inventory, measured):
        for name in ('source_geometry', 'geometry'):
            geometry = _get(owner, name, {})
            for row in _rows(geometry, 'subjects'):
                if _get(row, 'asset_id') == identity and _get(row, 'anchor_available') is True:
                    return _region(_get(row, 'source_anchor_box'))
        runtime = _get(owner, 'runtime_geometry', {})
        row = runtime.get(identity) if isinstance(runtime, dict) else None
        if row and _get(row, 'anchor_available') is True:
            return _measured_region(row, canvas)
    return None


def _window(source_region, actual_regions):
    regions = [source_region, *actual_regions]
    left, top = min(r[0] for r in regions), min(r[1] for r in regions)
    right, bottom = max(r[2] for r in regions), max(r[3] for r in regions)
    px, py = max(.015, (right-left)*.12), max(.02, (bottom-top)*.22)
    return _region([left-px, top-py, right+px, bottom+py])


def _dom_evidence(rows, window, canvas):
    result = []
    seen = set()
    for row in rows:
        region = _measured_region(row, canvas)
        identity = _get(row, 'id')
        if not region or identity in seen:
            continue
        if min(region[2], window[2]) <= max(region[0], window[0]) or min(region[3], window[3]) <= max(region[1], window[1]):
            continue
        seen.add(identity)
        item = {key: _get(row, key) for key in ('id', 'text', 'box', 'font_size', 'line_height', 'font_family', 'font_weight', 'visible', 'opacity')}
        item['text'] = str(item['text'] or '')[:400]
        item['rects'] = list(_get(row, 'rects', []) or [])[:24]
        item['fragments'] = [{key: _get(fragment, key) for key in ('text', 'font_size', 'visible', 'opacity', 'rects')}
                             for fragment in list(_get(row, 'fragments', []) or [])[:16]]
        for fragment in item['fragments']:
            fragment['text'] = str(fragment['text'] or '')[:160]
            fragment['rects'] = list(fragment['rects'] or [])[:12]
        result.append(item)
        if len(result) >= 24:
            break
    return result


def _scale_hint(source, actual, canvas):
    if not actual:
        return None
    left, top = min(row[0] for row in actual), min(row[1] for row in actual)
    right, bottom = max(row[2] for row in actual), max(row[3] for row in actual)
    source_px = [source[0]*canvas[0], source[1]*canvas[1], (source[2]-source[0])*canvas[0], (source[3]-source[1])*canvas[1]]
    rendered_px = [left*canvas[0], top*canvas[1], (right-left)*canvas[0], (bottom-top)*canvas[1]]
    return {'source_approximate_box_pixels': source_px, 'rendered_union_box_pixels': rendered_px,
            'width_ratio': rendered_px[2]/source_px[2], 'height_ratio': rendered_px[3]/source_px[3],
            'center_shift_pixels': [(rendered_px[0]+rendered_px[2]/2)-(source_px[0]+source_px[2]/2),
                                    (rendered_px[1]+rendered_px[3]/2)-(source_px[1]+source_px[3]/2)],
            'soft_hint_only': True, 'explanation':
                'The source box is an estimated ROI that may include whitespace, a text panel or decoration. '
                'The rendered box is a DOM layout/line box, not measured glyph ink. These bounds are not '
                'comparable as ink height or font size; ratios give no automatic size recommendation '
                'and are not validation errors. Compare the same-window pixels before judging typography.'}


def _bound_source_measurements(report, core_path, core_size, canvas):
    """Reject stale/mis-sized tool observations, without inventing a fallback box."""
    if not isinstance(report, dict):
        return None, 'local source measurements unavailable'
    if list(report.get('source_size') or []) != core_size or list(report.get('canvas') or []) != list(canvas):
        return None, 'source measurement dimensions do not match this core/canvas'
    digest = hashlib.sha256()
    with Path(core_path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024*1024), b''):
            digest.update(chunk)
    if report.get('source_sha256') != digest.hexdigest():
        return None, 'source measurement fingerprint does not match this core'
    return report, None


def build_preview_inspection(inventory, sources, poster_path, measured, output_dir, canvas, *, source_measurements=None):
    """Compare original and rendered pixels through identical normalized ROIs.

    Source OCR and browser geometry remain independent measurements. A trusted
    source report may replace an estimated text ROI, never a source pixel. An
    unresolved text match gets a shared full-canvas comparison rather than a
    misleading crop/scale hint. Independent-reference crops are not core ROIs.
    """
    canvas = _canvas(canvas)
    original = _core(sources, canvas)
    core_size = list(original.size)
    core = original.resize(canvas, Image.Resampling.LANCZOS); original.close()
    try:
        poster = _open(poster_path, 'rendered poster')
    except ValueError:
        core.close()
        raise
    if poster.size != canvas:
        core.close(); poster.close()
        raise ValueError('Rendered poster dimensions must exactly match the declared canvas')
    report = _base_report('preview', canvas, MAX_PREVIEW_IMAGES)
    report['core_size'] = core_size
    report['coordinate_contract'] = (
        'Both panels use the same canvas-normalized window, including the union of source ROI and matched actual bounds. '
        'source_status=located uses text matched to local OCR observations bound to this exact core and canvas; '
        'it is not a one-pixel accuracy guarantee. Other source text ROIs are estimates, not verified alignment '
        'targets. OCR detection bounds and DOM layout/ink bounds have different semantics; their ratio is not a '
        'font-size measurement or an automatic size recommendation. Unresolved localization needs full-source '
        'inspection/relocalization before a position correction, not movement toward the estimated ROI.')
    images, candidates = {}, []
    tool_rows, unresolved = {}, []
    if source_measurements is not None:
        bound, reason = _bound_source_measurements(source_measurements, sources['core'], core_size, canvas)
        if bound is not None:
            from .vibe_source_measurements import compare_text_geometry
            rows = compare_text_geometry(bound, inventory, measured, canvas)
            tool_rows = {row['label_id']: row for row in rows}
            report['source_measurements_status'] = {
                'available': True, 'source_sha256': bound['source_sha256'],
                'status': bound.get('status'), 'coordinate_space': 'canvas_normalized',
                'accuracy_guarantee': 'none; confidence/evidence must be inspected',
            }
        else:
            report['source_measurements_status'] = {'available': False, 'reason': reason}
        report['text_measurements'] = list(tool_rows.values())
    actual_texts, actual_assets = _rows(measured, 'texts'), _rows(measured, 'assets')
    for index, label in enumerate(_rows(inventory, 'labels', 'texts')):
        identity = f'label_{index}'
        estimated = _region(_get(label, 'source_box', _get(label, 'box')))
        if not estimated and source_measurements is None:
            report['skipped'].append({'id': identity, 'reason': 'no valid core text ROI'}); continue
        text = _get(label, 'text', '')
        matches = [row for row in actual_texts if _key(_get(row, 'text')) == _key(text) and _key(text)]
        # Split typography (e.g. numeric value and unit) remains inspectable.
        if not matches:
            matches = [row for row in actual_texts if len(_key(_get(row, 'text'))) >= 2 and _key(_get(row, 'text')) in _key(text)]
        actual = [r for row in matches if (r := _measured_region(row, canvas))]
        geometry = tool_rows.get(identity)
        if source_measurements is not None:
            status = (geometry or {}).get('source_status', 'unresolved')
            region = _region((geometry or {}).get('source_box')) if status == 'located' else None
            if region is None:
                unresolved.append({'id': identity, 'text': text, 'source_status': status,
                    'estimated_source_box': estimated, 'geometry_measurement': geometry,
                    'matched_render_ids': [_get(row, 'id') for row in matches]})
                continue
            # Correspondence is supplied by the same tool that found the source
            # text; do not silently replace its ambiguity decision with a fuzzy
            # DOM substring match used by the legacy inspection path.
            actual = [r for item in geometry.get('actual_boxes', []) if (r := _region(item))]
            match_ids = geometry.get('matched_render_ids', [])
            source_area = (region[2]-region[0])*(region[3]-region[1])
            score = max([abs(math.log(max(1e-8, (r[2]-r[0])*(r[3]-r[1]))/source_area)) for r in actual] or [0.])
        else:
            # Legacy callers may still show the requested pixels, but a model's
            # estimated ROI must not generate precise-looking repair commands.
            region, status, score = estimated, 'estimated', 0.
            match_ids = [_get(row, 'id') for row in matches]
        candidates.append({'id': identity, 'kind': 'text', 'text': text, 'source_box': region,
            'source_status': status, 'estimated_source_box': estimated,
            'actual_boxes': actual, 'matched_render_ids': match_ids, 'priority': score,
            'geometry_measurement': geometry,
            'measurement_note': 'Use tool evidence only after checking its confidence and correspondence; '
                'estimated source ROIs are not alignment targets. OCR boxes are not font-size measurements.'})
    text_candidates = sorted(candidates, key=lambda row: -row['priority'])
    subject_candidates = []
    for index, subject in enumerate(_rows(inventory, 'subjects')):
        identity = f'subject_{index}'
        region = _subject_region(subject, index, inventory, measured, canvas)
        if not region:
            report['skipped'].append({'id': identity, 'reason': 'no verified core ROI; independent reference crop is not a poster location'}); continue
        matches = [row for row in actual_assets if _get(row, 'id') in (identity, identity+'_original')]
        subject_candidates.append({'id': identity, 'kind': 'subject', 'source_box': region,
            'actual_boxes': [r for row in matches if (r := _measured_region(row, canvas))],
            'matched_render_ids': [_get(row, 'id') for row in matches]})
    # Include both material placement and typography; largest geometric text
    # deviations go first, not any particular wording, number or project.
    candidates = []
    for index in range(max(len(subject_candidates), len(text_candidates))):
        if index < len(text_candidates): candidates.append(text_candidates[index])
        if index < len(subject_candidates): candidates.append(subject_candidates[index])
    if unresolved:
        # One shared full comparison, rather than many empty/incorrect source
        # ROIs monopolising the compact evidence budget by false size ratios.
        candidates.insert(min(1, len(candidates)), {
            'id': 'text_localization_unresolved', 'kind': 'text_localization',
            'source_status': 'needs_relocalization', 'source_box': None,
            'actual_boxes': [], 'matched_render_ids': [], 'window_override': [0., 0., 1., 1.],
            'unresolved_texts': unresolved,
            'measurement_note': 'Full source and current poster. The listed texts have no unique reliable '
                'source localization. Do not move or resize toward an estimated box; inspect/relocalize '
                'the source first. Absence from an estimated crop is not absence from the source.',
        })
    try:
        for index, row in enumerate(candidates):
            if index >= MAX_PREVIEW_IMAGES:
                report['skipped'].append({'id': row['id'], 'reason': 'preview inspection limit'}); continue
            window = row.get('window_override') or _window(row['source_box'], row['actual_boxes'])
            pixels = _pixel_window(window, canvas)
            left, right = core.crop(pixels), poster.crop(pixels)
            sheet, scale = _sheet([left, right], ['Original / core', 'Rendered / poster'], row['id'],
                                  footer=f'Same window: {pixels}; same scale. Compare size, spacing and completeness.')
            image_id = f'preview_inspection_{index:02d}'
            if _save(sheet, Path(output_dir), image_id, images, report):
                report['items'].append({**row, 'image_id': image_id, 'window_normalized': window,
                    'window_pixels': pixels, 'display_scale': scale, 'panel_size': list(left.size),
                    # Text correction comes from bound source measurements,
                    # not a ratio against an unverified region of interest.
                    'scale_hint': (_scale_hint(row['source_box'], row['actual_boxes'], canvas)
                                   if row['kind'] == 'subject' else None),
                    'intersecting_texts': _dom_evidence(actual_texts, window, canvas)})
            left.close(); right.close(); sheet.close()
    finally:
        core.close(); poster.close()
    return {'images': images, 'report': report}


def compact_preview_evidence(evidence, max_images=4):
    """Select existing preview sheets in report order without changing evidence.

    The caller keeps the complete on-disk inspection. This detached view only
    reduces the images sent to a model; omitted ROIs are not quality failures.
    No pixels are edited, and no material or render result is selected.
    """
    if type(max_images) is not int or not 0 <= max_images <= MAX_PREVIEW_IMAGES:
        raise ValueError('Compact preview max_images must be a bounded nonnegative integer')
    if (not isinstance(evidence, dict) or not isinstance(evidence.get('images'), dict)
            or not isinstance(evidence.get('report'), dict)
            or not isinstance(evidence['report'].get('items'), list)):
        raise ValueError('Compact preview requires an inspection images/report mapping')
    report = deepcopy(evidence['report'])
    rows = report['items']
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError('Compact preview report items must be objects')
    selected, kept = {}, []
    skipped = list(report.get('skipped') or [])
    total_pixels = 0
    for row in rows:
        image_id = row.get('image_id')
        reason = None
        if not isinstance(image_id, str) or image_id not in evidence['images']:
            reason = 'preview evidence image binding unavailable; ROI not sent'
        elif image_id in selected:
            reason = 'duplicate preview evidence image_id; ROI not sent'
        elif len(selected) >= max_images:
            reason = 'compact preview image budget; ROI not sent'
        else:
            try:
                path = Path(evidence['images'][image_id])
                if not path.is_file():
                    raise ValueError('missing image')
                # Read dimensions only: keep the exact existing file and pixels.
                with Image.open(path) as image:
                    pixels = image.width*image.height
                    if pixels <= 0 or pixels > MAX_INPUT_PIXELS:
                        raise ValueError('invalid image dimensions')
                selected[image_id] = path
                kept.append(row)
                total_pixels += pixels
            except (OSError, TypeError, ValueError, Image.DecompressionBombError):
                reason = 'preview evidence image unavailable; ROI not sent'
        if reason:
            skipped.append({'id': row.get('id'), 'image_id': image_id,
                            'reason': reason, 'quality_failure': False})
    if len(kept) == len(rows) and len(selected) == len(evidence['images']):
        # A complete small evidence set keeps exactly the original report
        # schema/values; callers can retain existing non-compacted receipts.
        return {'images': selected, 'report': report}
    report.update(items=kept, skipped=skipped, image_count=len(selected), total_pixels=total_pixels)
    report['selection'] = {
        'kind': 'compact_preview', 'max_images': max_images,
        'source_image_count': evidence['report'].get('image_count', len(evidence['images'])),
        'source_total_pixels': evidence['report'].get('total_pixels'),
        'selected_image_ids': list(selected), 'omitted_roi_count': len(rows)-len(kept),
        'complete_inspection_preserved': True,
    }
    return {'images': selected, 'report': report}
