"""Local pixel evidence only: no model, network, GPU or renderer calls."""
import hashlib
import math
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from backend.app.vibe_code_inspection import (
    build_material_inspection, build_preview_inspection,
    compact_preview_evidence,
    MAX_MATERIAL_IMAGES, MAX_MATERIAL_EDGE_IMAGES, MAX_PREVIEW_IMAGES, MAX_TOTAL_PIXELS, GAP, HEADER,
)


class CodeInspectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.canvas = (640, 360)
        self.core = self.root/'core.png'
        image = Image.new('RGBA', self.canvas, (9, 25, 48, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((60, 70, 250, 250), fill=(30, 180, 220, 255))
        draw.rectangle((330, 120, 520, 190), fill=(240, 240, 240, 255))
        image.save(self.core)
        self.sources = {'core': self.core}

    def material(self, prepared_size=None):
        crop = (.1, .2, .4, .8)
        with Image.open(self.core) as image:
            raw = image.crop((64, 72, 256, 288)).convert('RGBA')
        original = self.root/'subject-original.png'; raw.save(original)
        prepared = self.root/'subject.png'
        alpha = Image.new('L', raw.size, 0)
        ImageDraw.Draw(alpha).ellipse((20, 20, 160, 180), fill=255)
        raw.putalpha(alpha)
        if prepared_size:
            raw = raw.resize(prepared_size)
        raw.save(prepared)
        inventory = SimpleNamespace(subjects=[SimpleNamespace(source='core', crop=crop, treatment='cutout')], labels=[])
        return inventory, {'subject_0': prepared, 'subject_0_original': original}

    def test_materials_compare_same_scale_without_mask_verdict_or_mutation(self):
        inventory, assets = self.material()
        before = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in {**self.sources, **assets}.items()}
        result = build_material_inspection(inventory, self.sources, assets, self.root/'inspection', self.canvas)
        self.assertEqual(result['report']['image_count'], 2)
        self.assertEqual(result['report']['primary_image_count'], 1)
        self.assertEqual(result['report']['review_image_count'], 1)
        row = result['report']['items'][0]
        self.assertEqual(row['original_size'], [192, 216])
        self.assertEqual(row['prepared_size'], [192, 216])
        self.assertGreater(row['mask']['alpha_reduced_fraction'], .1)
        self.assertEqual(result['report']['semantic_mask_verdict'], 'not_inferred')
        self.assertFalse(result['report']['automatic_material_selection'])
        self.assertTrue(all(isinstance(path, Path) and path.is_file() for path in result['images'].values()))
        after = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in {**self.sources, **assets}.items()}
        self.assertEqual(before, after)

    def test_source_neighborhood_exposes_pixels_already_cut_from_original(self):
        source = Image.new('RGBA', self.canvas, (8, 15, 30, 255))
        ImageDraw.Draw(source).rectangle((100, 80, 200, 280), fill=(40, 230, 70, 255))
        source.save(self.core)
        # Both variants lose the lower part before any alpha operation. A
        # triptych alone would show two identical but incomplete subjects.
        crop = [.125, .2, .375, .6]
        raw = source.crop((80, 72, 240, 216))
        raw_path = self.root/'already-truncated.png'; raw.save(raw_path)
        result = build_material_inspection({'subjects': [{'source': 'core', 'crop': crop,
            'treatment': 'cutout'}]}, self.sources,
            {'subject_0': raw_path, 'subject_0_original': raw_path}, self.root/'context', self.canvas)
        row = result['report']['items'][0]
        context = row['source_context']
        self.assertEqual(row['mask']['alpha_reduced_pixels'], 0)
        self.assertTrue(context['available'])
        self.assertEqual(context['crop_pixel_window'], [80, 72, 240, 216])
        self.assertGreater(context['context_pixel_window'][3], 240)
        self.assertEqual(context['semantic_crop_verdict'], 'not_inferred')
        self.assertTrue(context['overlays']['not_part_of_source_content'])
        view = context['neighborhood_view']
        left, top, _, _ = view['sheet_content_box']
        sx, sy = view['display_scale_xy']
        window = view['source_pixel_window']
        sample = (round(left+(150-window[0])*sx), round(top+(240-window[1])*sy))
        with Image.open(result['images'][row['image_id']]) as sheet:
            r, g, b = sheet.getpixel(sample)
            self.assertGreater(g, 200)
            self.assertLess(r, 60)
            self.assertLess(b, 90)
        # The source and frozen truncated crop have not been repaired/changed.
        with Image.open(raw_path) as unchanged:
            self.assertEqual(unchanged.size, (160, 144))

    def test_material_context_uses_independent_reference_not_core_coordinates(self):
        reference = self.root/'reference.png'
        image = Image.new('RGBA', (300, 500), (170, 65, 110, 255))
        image.save(reference)
        raw_path = self.root/'ref-crop.png'
        image.crop((30, 100, 240, 350)).save(raw_path)
        result = build_material_inspection({'subjects': [{'source': 'reference_1',
            'source_crop': [.1, .2, .8, .7], 'treatment': 'preserve'}]},
            {**self.sources, 'reference_1': reference}, {'subject_0': raw_path},
            self.root/'reference-context', self.canvas)
        row = result['report']['items'][0]
        context = row['source_context']
        self.assertEqual(context['source_id'], 'reference_1')
        self.assertEqual(context['source_size'], [300, 500])
        self.assertEqual(context['crop_pixel_window'], [30, 100, 240, 350])
        self.assertEqual(context['full_source_view']['source_pixel_window'], [0, 0, 300, 500])
        self.assertIn('NOT core canvas', context['coordinate_space'])
        view = context['full_source_view']
        left, top, w, h = view['sheet_content_box']
        self.assertAlmostEqual(w/h, 300/500, delta=.01)
        with Image.open(result['images'][row['image_id']]) as sheet:
            self.assertEqual(sheet.getpixel((left+w//2, top+h//2)), (170, 65, 110))

    def test_material_context_respects_exif_orientation(self):
        reference = self.root/'rotated.jpg'
        image = Image.new('RGB', (120, 80), '#457812')
        exif = image.getexif(); exif[274] = 6
        image.save(reference, exif=exif)
        prepared = self.root/'rotated-crop.png'
        Image.new('RGBA', (40, 60), '#457812').save(prepared)
        result = build_material_inspection({'subjects': [{'source': 'reference_1',
            'crop': [.25, .25, .75, .75]}]}, {**self.sources, 'reference_1': reference},
            {'subject_0': prepared}, self.root/'exif-context', self.canvas)
        row = result['report']['items'][0]
        self.assertEqual(row['original_size'], [40, 60])
        self.assertEqual(row['source_context']['source_size'], [80, 120])
        self.assertEqual(row['source_context']['crop_pixel_window'], [20, 30, 60, 90])

    def test_material_context_clamps_edges_and_reports_exact_pixel_alignment(self):
        for i, region in enumerate(([0, 0, .1, .1], [.9, .9, 1, 1], [.10123, .20234, .40789, .80345])):
            left, top = math.floor(region[0]*640), math.floor(region[1]*360)
            right, bottom = math.ceil(region[2]*640), math.ceil(region[3]*360)
            prepared = self.root/f'edge-{i}.png'
            with Image.open(self.core) as source:
                source.crop((left, top, right, bottom)).save(prepared)
            result = build_material_inspection({'subjects': [{'source': 'core', 'crop': region}]},
                self.sources, {'subject_0': prepared}, self.root/f'edge-context-{i}', self.canvas)
            context = result['report']['items'][0]['source_context']
            self.assertEqual(context['crop_pixel_window'], [left, top, right, bottom])
            self.assertEqual(context['pixel_aligned_source_crop'], [left/640, top/360, right/640, bottom/360])
            expanded = context['context_pixel_window']
            self.assertTrue(0 <= expanded[0] <= left < right <= expanded[2] <= 640)
            self.assertTrue(0 <= expanded[1] <= top < bottom <= expanded[3] <= 360)
            self.assertEqual(context['context_normalized_window'],
                [expanded[0]/640, expanded[1]/360, expanded[2]/640, expanded[3]/360])

    def test_edge_evidence_exposes_crossing_in_all_directions_with_exact_affine_mapping(self):
        source = Image.new('RGBA', self.canvas, (8, 15, 30, 255))
        draw = ImageDraw.Draw(source)
        colors = {'top': (50, 220, 70), 'right': (230, 80, 50),
                  'bottom': (70, 90, 240), 'left': (225, 70, 210)}
        # Four marks extend through the proposed crop. No preparation defect:
        # the original crop and actual prepared image are identical.
        draw.rectangle((180, 90, 200, 150), fill=(*colors['top'], 255))
        draw.rectangle((280, 170, 340, 190), fill=(*colors['right'], 255))
        draw.rectangle((180, 210, 200, 270), fill=(*colors['bottom'], 255))
        draw.rectangle((80, 170, 140, 190), fill=(*colors['left'], 255))
        source.save(self.core)
        crop = [120/640, 120/360, 300/640, 240/360]
        raw_path = self.root/'four-edges-raw.png'; source.crop((120, 120, 300, 240)).save(raw_path)
        before = hashlib.sha256(self.core.read_bytes()).hexdigest()
        result = build_material_inspection({'subjects': [{'source': 'core', 'crop': crop}]},
            self.sources, {'subject_0': raw_path, 'subject_0_original': raw_path}, self.root/'four-edges', self.canvas)
        row = result['report']['items'][0]
        self.assertEqual(row['mask']['alpha_reduced_pixels'], 0)
        self.assertEqual(row['edge_evidence']['check_order'], ['top', 'right', 'bottom', 'left'])
        self.assertEqual(row['edge_evidence']['audience'], 'material_review_only')
        edge_id = row['edge_evidence']['image_id']
        self.assertNotIn(edge_id, result['images'])
        samples = {'top': (190.5, 105.5), 'right': (325.5, 180.5),
                   'bottom': (190.5, 260.5), 'left': (95.5, 180.5)}
        with Image.open(result['review_images'][edge_id]) as sheet:
            for name, (x, y) in samples.items():
                view = row['edge_views'][name]
                self.assertTrue(view['outside_source_pixels_available'])
                self.assertEqual(view['source_id'], 'core')
                self.assertEqual(view['sheet_content_box_format'], 'xywh')
                self.assertEqual(view['semantic_crossing_verdict'], 'not_inferred')
                self.assertEqual(view['rotation_degrees_ccw'], 90 if name in ('left', 'right') else 0)
                a,b,c,d,e,f = view['source_pixel_to_sheet_affine']
                sample = (math.floor(a*x+b*y+c), math.floor(d*x+e*y+f))
                self.assertEqual(sheet.getpixel(sample), colors[name])
                window = view['source_pixel_window']
                self.assertEqual(view['source_normalized_window'],
                    [window[0]/640, window[1]/360, window[2]/640, window[3]/360])
                self.assertIn(view['outside_display_side'], ('above', 'below'))
                self.assertNotEqual(view['outside_display_side'], view['inside_display_side'])
        self.assertEqual(hashlib.sha256(self.core.read_bytes()).hexdigest(), before)

    def test_edge_evidence_at_full_source_boundary_never_invents_outside_pixels(self):
        result = build_material_inspection({'subjects': [{'source': 'core', 'crop': [0, 0, 1, 1]}]},
            self.sources, {'subject_0': self.core}, self.root/'full-source-edges', self.canvas)
        row = result['report']['items'][0]
        self.assertEqual(set(row['edge_views']), {'top', 'right', 'bottom', 'left'})
        for name, view in row['edge_views'].items():
            self.assertFalse(view['outside_source_pixels_available'])
            self.assertTrue(view['source_boundary_reached'])
            self.assertIsNone(view['outside_display_side'])
            self.assertIn('no padding', view['outside_unavailable_reason'])
            l,t,r,b = view['source_pixel_window']
            self.assertTrue(0 <= l < r <= 640)
            self.assertTrue(0 <= t < b <= 360)
            boundary = view['crop_edge_full_source_normalized']
            axis = 1 if name in ('top', 'bottom') else 0
            self.assertEqual(boundary[axis], 0 if name in ('top', 'left') else 1)

    def test_edge_evidence_all_eight_subjects_fit_shared_budget_without_silent_drops(self):
        square = self.root/'square.png'; Image.new('RGBA', (400, 400), '#123456').save(square)
        subjects = [{'source': 'core', 'crop': [.1, .2, .4, .8]} for _ in range(MAX_MATERIAL_IMAGES)]
        assets = {f'subject_{i}': square for i in range(MAX_MATERIAL_IMAGES)}
        result = build_material_inspection({'subjects': subjects}, self.sources, assets,
            self.root/'all-edge-budget', self.canvas)
        self.assertEqual(len(result['images']), 8)
        self.assertEqual(len(result['review_images']), MAX_MATERIAL_EDGE_IMAGES)
        self.assertEqual(result['report']['image_count'], 16)
        self.assertLessEqual(result['report']['total_pixels'], MAX_TOTAL_PIXELS)
        self.assertEqual(result['report']['skipped'], [])
        self.assertTrue(all(row['edge_evidence']['available'] for row in result['report']['items']))
        actual_pixels = 0
        for path in {**result['images'], **result['review_images']}.values():
            with Image.open(path) as im:
                actual_pixels += im.width*im.height
        self.assertEqual(result['report']['total_pixels'], actual_pixels)

    def test_missing_original_is_cropped_from_bound_source_and_size_mismatch_is_not_stretched(self):
        inventory, assets = self.material(prepared_size=(96, 108))
        del assets['subject_0_original']
        result = build_material_inspection(inventory, self.sources, assets, self.root/'fallback', self.canvas)
        row = result['report']['items'][0]
        self.assertEqual(row['original_origin'], 'bound source crop')
        self.assertEqual(row['original_size'], [192, 216])
        self.assertEqual(row['prepared_size'], [96, 108])
        self.assertFalse(row['mask']['alpha_comparison_available'])
        self.assertNotIn('alpha_reduced_fraction', row['mask'])
        self.assertIn('same pixel scale', row['panel_alignment'])

    def test_fully_transparent_mask_remains_visual_evidence_not_automatic_material_choice(self):
        inventory, assets = self.material()
        Image.new('RGBA', (192, 216), (0, 0, 0, 0)).save(assets['subject_0'])
        result = build_material_inspection(inventory, self.sources, assets, self.root/'empty-mask', self.canvas)
        self.assertEqual(result['report']['items'][0]['mask']['prepared_alpha_min_max'], [0, 0])
        self.assertEqual(result['report']['items'][0]['mask']['alpha_reduced_fraction'], 1)
        self.assertEqual(result['report']['semantic_mask_verdict'], 'not_inferred')
        self.assertEqual(len(result['images']), 1)

    def test_bound_original_pair_is_inspectable_without_guessing_invalid_source_roi(self):
        inventory, assets = self.material()
        inventory.subjects[0].crop = [0, 0, math.nan, 1]
        result = build_material_inspection(inventory, self.sources, assets, self.root/'invalid-roi-pair', self.canvas)
        self.assertEqual(len(result['images']), 1)
        self.assertIsNone(result['report']['items'][0]['source_crop'])
        self.assertIn('bound original/prepared', result['report']['items'][0]['source_coordinate_note'])
        self.assertFalse(result['report']['items'][0]['source_context']['available'])
        self.assertFalse(result['report']['items'][0]['edge_evidence']['available'])
        self.assertEqual(result['report']['items'][0]['edge_views'], {})
        self.assertEqual(result['review_images'], {})

    def test_invalid_optional_regions_skip_but_missing_or_invalid_core_fails(self):
        inventory = {'subjects': [{'source': 'core', 'source_crop': [0, 0, math.nan, 1]}]}
        result = build_material_inspection(inventory, self.sources, {}, self.root/'skip', self.canvas)
        self.assertFalse(result['images']); self.assertTrue(result['report']['skipped'])
        for sources in ({}, {'core': self.root/'missing.png'}):
            with self.assertRaisesRegex(ValueError, 'core'):
                build_material_inspection(inventory, sources, {}, self.root/'bad', self.canvas)
        with self.assertRaisesRegex(ValueError, 'aspect'):
            build_preview_inspection({}, self.sources, self.core, {}, self.root/'aspect', (640, 640))

    def test_legacy_preview_uses_identical_windows_without_measuring_against_guessed_roi(self):
        inventory = {'labels': [{'text': 'Trial (A)', 'source_box': [.5, .3, .8, .5]}], 'subjects': []}
        measured = {'texts': [{'id': 'heading', 'text': 'Trial A', 'box': [320, 108, 240, 108],
            'font_size': 60, 'visible': True, 'opacity': 1,
            'rects': [{'left': 320, 'top': 108, 'right': 560, 'bottom': 216}],
            'fragments': [{'text': 'Trial A', 'font_size': 60, 'visible': True, 'opacity': 1, 'rects': []}]}]}
        result = build_preview_inspection(inventory, self.sources, self.core, measured, self.root/'preview', self.canvas)
        row = result['report']['items'][0]
        self.assertEqual(row['matched_render_ids'], ['heading'])
        self.assertIsNone(row['scale_hint'])
        self.assertEqual(row['source_status'], 'estimated')
        self.assertEqual(row['priority'], 0)
        self.assertIn('not alignment targets', row['measurement_note'])
        self.assertIn('font-size measurement', result['report']['coordinate_contract'])
        self.assertEqual(row['intersecting_texts'][0]['fragments'][0]['text'], 'Trial A')
        self.assertLessEqual(row['window_normalized'][0], .5)
        self.assertGreaterEqual(row['window_normalized'][2], .875)
        # Core and poster are identical; both displayed pixel windows must be too.
        with Image.open(next(iter(result['images'].values()))) as sheet:
            width = (sheet.width-3*GAP)//2
            height = round(row['panel_size'][1]*row['display_scale'])
            first = sheet.crop((GAP, HEADER, GAP+width, HEADER+height))
            second = sheet.crop((2*GAP+width, HEADER, 2*GAP+2*width, HEADER+height))
            self.assertEqual(first.tobytes(), second.tobytes())

    def source_report(self, size=None, canvas=None):
        return {'source_sha256': hashlib.sha256(self.core.read_bytes()).hexdigest(),
                'source_size': list(size or self.canvas), 'canvas': list(canvas or self.canvas),
                'status': 'ready'}

    def test_tool_localized_text_replaces_wrong_estimate_without_editing_pixels(self):
        inventory = {'labels': [{'text': 'A finding', 'source_box': [.05, .85, .2, .99]}]}
        measured = {'texts': [{'id': 'footer', 'text': 'A finding', 'box': [320, 288, 192, 36]}]}
        tool_row = {'label_id': 'label_0', 'text': 'A finding', 'source_status': 'located',
                    'source_box': [.5, .8, .8, .9], 'estimated_source_box': [.05, .85, .2, .99],
                    'actual_boxes': [[.5, .8, .8, .9]], 'matched_render_ids': ['footer'],
                    'delta_px': {'center': [0, 0]}, 'confidence': .98}
        before = hashlib.sha256(self.core.read_bytes()).hexdigest()
        with patch('backend.app.vibe_source_measurements.compare_text_geometry', return_value=[tool_row]) as compare:
            result = build_preview_inspection(inventory, self.sources, self.core, measured,
                self.root/'tool-localized', self.canvas, source_measurements=self.source_report())
        compare.assert_called_once()
        row = result['report']['items'][0]
        self.assertEqual(row['source_status'], 'located')
        self.assertEqual(row['source_box'], [.5, .8, .8, .9])
        self.assertEqual(row['estimated_source_box'], [.05, .85, .2, .99])
        self.assertGreater(row['window_normalized'][0], .4)
        self.assertIsNone(row['scale_hint'])
        self.assertEqual(row['geometry_measurement']['delta_px'], {'center': [0, 0]})
        self.assertEqual(result['report']['text_measurements'], [tool_row])
        self.assertTrue(result['report']['source_measurements_status']['available'])
        self.assertEqual(before, hashlib.sha256(self.core.read_bytes()).hexdigest())

    def test_unresolved_and_ambiguous_texts_share_one_full_source_comparison(self):
        inventory = {'labels': [{'text': f'Finding {i}', 'source_box': [.02, .02, .03, .03]}
                                for i in range(20)]}
        tool_rows = [{'label_id': f'label_{i}', 'text': f'Finding {i}',
                     'source_status': 'ambiguous' if i % 2 else 'unresolved',
                     'source_box': None, 'actual_boxes': [], 'candidates': []}
                    for i in range(20)]
        with patch('backend.app.vibe_source_measurements.compare_text_geometry', return_value=tool_rows):
            result = build_preview_inspection(inventory, self.sources, self.core, {},
                self.root/'unresolved', self.canvas, source_measurements=self.source_report())
        self.assertEqual(len(result['images']), 1)
        row = result['report']['items'][0]
        self.assertEqual(row['kind'], 'text_localization')
        self.assertEqual(row['source_status'], 'needs_relocalization')
        self.assertIsNone(row['source_box'])
        self.assertIsNone(row['scale_hint'])
        self.assertEqual(row['window_normalized'], [0, 0, 1, 1])
        self.assertEqual(len(row['unresolved_texts']), 20)
        self.assertEqual(result['report']['text_measurements'], tool_rows)
        self.assertIn('not absence from the source', row['measurement_note'])

    def test_unavailable_localization_never_reinstates_guessed_source_coordinates(self):
        inventory = {'labels': [{'text': 'Finding', 'source_box': [.1, .9, .2, .99]}]}
        result = build_preview_inspection(inventory, self.sources, self.core, {},
            self.root/'unavailable-localization', self.canvas,
            source_measurements={'status': 'unavailable', 'reason': 'models missing'})
        self.assertFalse(result['report']['source_measurements_status']['available'])
        row = result['report']['items'][0]
        self.assertIsNone(row['source_box'])
        self.assertEqual(row['window_normalized'], [0, 0, 1, 1])
        self.assertIsNone(row['scale_hint'])

    def test_source_measurements_must_be_bound_to_current_image_and_transform(self):
        inventory = {'labels': [{'text': 'Finding', 'source_box': [.2, .2, .4, .4]}]}
        variants = [dict(self.source_report(), source_sha256='0'*64),
                    self.source_report(size=(1280, 720)), self.source_report(canvas=(1280, 720))]
        for index, report in enumerate(variants):
            with patch('backend.app.vibe_source_measurements.compare_text_geometry') as compare:
                result = build_preview_inspection(inventory, self.sources, self.core, {},
                    self.root/f'stale-{index}', self.canvas, source_measurements=report)
            compare.assert_not_called()
            self.assertFalse(result['report']['source_measurements_status']['available'])
            self.assertIsNone(result['report']['items'][0]['source_box'])

    def test_different_source_and_canvas_dimensions_keep_identical_normalized_windows(self):
        # Source extraction uses original pixels; preview geometry uses canvas
        # pixels. The report must preserve, not conflate, those coordinate spaces.
        original = Image.new('RGB', (1280, 720), '#18354c')
        ImageDraw.Draw(original).rectangle((640, 360, 1000, 480), fill='#eeeeee')
        original.save(self.core)
        poster = self.root/'scaled-poster.png'
        original.resize(self.canvas, Image.Resampling.LANCZOS).save(poster)
        row = {'label_id': 'label_0', 'text': 'Finding', 'source_status': 'located',
               'source_box': [.5, .5, .78125, 2/3], 'actual_boxes': [[.5, .5, .78125, 2/3]],
               'matched_render_ids': ['result'], 'delta_px': {'center': [0, 0]}, 'confidence': .99}
        with patch('backend.app.vibe_source_measurements.compare_text_geometry', return_value=[row]):
            result = build_preview_inspection({'labels': [{'text': 'Finding'}]}, self.sources,
                poster, {}, self.root/'scaled-source', self.canvas,
                source_measurements=self.source_report(size=(1280, 720)))
        self.assertEqual(result['report']['core_size'], [1280, 720])
        self.assertEqual(result['report']['canvas'], [640, 360])
        self.assertEqual(result['report']['items'][0]['source_box'], row['source_box'])
        with Image.open(next(iter(result['images'].values()))) as sheet:
            geometry = result['report']['items'][0]
            width = (sheet.width-3*GAP)//2
            height = round(geometry['panel_size'][1]*geometry['display_scale'])
            first = sheet.crop((GAP, HEADER, GAP+width, HEADER+height))
            second = sheet.crop((2*GAP+width, HEADER, 2*GAP+2*width, HEADER+height))
            self.assertEqual(first.tobytes(), second.tobytes())

    def test_source_measurement_module_integration_reports_actual_pixel_displacement(self):
        from backend.app.vibe_source_measurements import build_source_measurements
        raw = {'status': 'ready', 'source_size': [640, 360],
               'source_sha256': hashlib.sha256(self.core.read_bytes()).hexdigest(),
               'coordinate_space': 'source_pixels',
               'texts': [{'id': 'ocr_footer', 'text': 'Source finding',
                          'box': [320, 288, 512, 324], 'confidence': .98}]}
        source = build_source_measurements(self.core, raw, self.canvas)
        measured = {'texts': [{'id': 'footer', 'text': 'Source finding',
                               'box': [352, 300, 192, 36], 'visible': True}]}
        result = build_preview_inspection(
            {'labels': [{'text': 'Source finding', 'source_box': [.01, .01, .1, .1]}]},
            self.sources, self.core, measured, self.root/'module-integration', self.canvas,
            source_measurements=source)
        row = result['report']['items'][0]
        self.assertEqual(row['source_status'], 'located')
        self.assertEqual(row['source_box'], [.5, .8, .8, .9])
        self.assertEqual(row['geometry_measurement']['delta_px']['center'], [32, 12])
        self.assertEqual(row['geometry_measurement']['actual_status'], 'located')
        self.assertFalse(row['geometry_measurement']['automatic_quality_verdict'])
        self.assertIsNone(row['scale_hint'])

    def test_external_reference_crop_is_not_guessed_as_core_location(self):
        inventory = {'subjects': [{'source': 'reference_1', 'source_crop': [.1, .1, .8, .8]}]}
        result = build_preview_inspection(inventory, self.sources, self.core, {}, self.root/'external', self.canvas)
        self.assertEqual(len(result['images']), 0)
        self.assertIn('independent reference', result['report']['skipped'][0]['reason'])
        measured = {'runtime_geometry': {'subject_0': {'box': [64, 72, 192, 216], 'anchor_available': True}}}
        result = build_preview_inspection(inventory, self.sources, self.core, measured, self.root/'anchored', self.canvas)
        self.assertEqual(result['report']['items'][0]['source_box'], [.1, .2, .4, .8])

    def test_geometry_manifest_and_legacy_design_core_crop_both_work(self):
        inventory, assets = self.material()
        measured = {'assets': [{'id': 'subject_0_original', 'box': [64, 72, 192, 216]}]}
        result = build_preview_inspection(inventory, self.sources, self.core, measured, self.root/'legacy', self.canvas)
        self.assertEqual(result['report']['items'][0]['matched_render_ids'], ['subject_0_original'])
        inventory = {'subjects': [{'source_id': 'reference'}], 'source_geometry': {'subjects': [
            {'asset_id': 'subject_0', 'anchor_available': True, 'source_anchor_box': [.1, .2, .4, .8]}]}}
        result = build_preview_inspection(inventory, self.sources, self.core, {}, self.root/'geometry', self.canvas)
        self.assertEqual(len(result['images']), 1)

    def test_roi_clips_finite_edges_and_rejects_reversed_or_nan_coordinates(self):
        inventory = {'texts': [
            {'text': 'A', 'source_box': [-.1, -.2, .2, .3]},
            {'text': 'B', 'source_box': [.8, .2, .1, .4]},
            {'text': 'C', 'source_box': [0, 0, float('inf'), .4]}]}
        result = build_preview_inspection(inventory, self.sources, self.core, {}, self.root/'regions', self.canvas)
        self.assertEqual(len(result['images']), 1)
        self.assertEqual(len(result['report']['skipped']), 2)
        self.assertTrue(all(0 <= value <= 1 for value in result['report']['items'][0]['window_normalized']))

    def test_image_and_total_pixel_budgets_and_no_overwrite(self):
        labels = [{'text': f'Label {i}', 'source_box': [0, 0, 1, 1]} for i in range(20)]
        result = build_preview_inspection({'labels': labels}, self.sources, self.core, {}, self.root/'limits', self.canvas)
        self.assertLessEqual(len(result['images']), MAX_PREVIEW_IMAGES)
        self.assertLessEqual(result['report']['total_pixels'], MAX_TOTAL_PIXELS)
        self.assertGreater(len(result['report']['skipped']), 0)
        with self.assertRaises(FileExistsError):
            build_preview_inspection({'labels': labels}, self.sources, self.core, {}, self.root/'limits', self.canvas)
        inventory, assets = self.material()
        subjects = inventory.subjects*12
        mapped = {f'subject_{i}': assets['subject_0'] for i in range(12)}
        result = build_material_inspection({'subjects': subjects}, self.sources, mapped, self.root/'material-limits', self.canvas)
        self.assertLessEqual(len(result['images']), MAX_MATERIAL_IMAGES)
        self.assertLessEqual(result['report']['total_pixels'], MAX_TOTAL_PIXELS)

    def test_poster_dimensions_must_match_canvas(self):
        small = self.root/'small.png'; Image.new('RGB', (320, 180)).save(small)
        with self.assertRaisesRegex(ValueError, 'exactly match'):
            build_preview_inspection({}, self.sources, small, {}, self.root/'mismatch', self.canvas)

    def test_compact_preview_preserves_interleaved_order_files_and_complete_report(self):
        order = ['label_4', 'subject_0', 'label_1', 'subject_1', 'label_2', 'label_3']
        images = {}
        rows = []
        for index, identity in enumerate(order):
            image_id = f'preview_{index}'
            target = self.root/(image_id+'.png')
            Image.new('RGB', (20+index, 10), (index, 10, 20)).save(target)
            images[image_id] = target
            rows.append({'id': identity, 'image_id': image_id, 'source_box': [0, 0, 1, 1]})
        evidence = {'images': dict(reversed(list(images.items()))), 'report': {
            'kind': 'preview', 'items': rows, 'skipped': [{'id': 'previous', 'reason': 'invalid ROI'}],
            'image_count': 6, 'total_pixels': 1350, 'limits': {'max_images': 12}}}
        before = deepcopy(evidence)
        hashes = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in images.items()}
        result = compact_preview_evidence(evidence)
        self.assertEqual([row['id'] for row in result['report']['items']], order[:4])
        self.assertEqual(list(result['images']), [f'preview_{i}' for i in range(4)])
        self.assertEqual(result['report']['image_count'], 4)
        self.assertEqual(result['report']['total_pixels'], 860)
        self.assertEqual(result['report']['selection']['omitted_roi_count'], 2)
        self.assertEqual([row['id'] for row in result['report']['skipped']], ['previous', *order[4:]])
        self.assertTrue(all(row['quality_failure'] is False for row in result['report']['skipped'][1:]))
        self.assertEqual(evidence, before)
        result['report']['items'][0]['source_box'][0] = .5
        result['report']['limits']['max_images'] = 1
        self.assertEqual(evidence, before)
        self.assertEqual(hashes, {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in images.items()})

    def test_compact_preview_skips_missing_and_duplicate_images_without_spending_slots(self):
        evidence = {'images': {'missing': self.root/'absent.png', 'good': self.core,
                              'other': self.core, 'unreferenced': self.core},
                    'report': {'items': [
                        {'id': 'unbound', 'image_id': 'not_bound'},
                        {'id': 'missing', 'image_id': 'missing'},
                        {'id': 'first', 'image_id': 'good'},
                        {'id': 'duplicate', 'image_id': 'good'},
                        {'id': 'second', 'image_id': 'other'}], 'skipped': []}}
        result = compact_preview_evidence(evidence, max_images=2)
        self.assertEqual(list(result['images']), ['good', 'other'])
        self.assertEqual([row['id'] for row in result['report']['items']], ['first', 'second'])
        self.assertEqual(len(result['report']['skipped']), 3)
        self.assertNotIn('unreferenced', result['images'])
        self.assertEqual(len(evidence['report']['items']), 5)

    def test_compact_preview_zero_budget_and_invalid_contracts(self):
        evidence = {'images': {'core': self.core}, 'report': {'items': [{'id': 'roi', 'image_id': 'core'}]}}
        result = compact_preview_evidence(evidence, max_images=0)
        self.assertFalse(result['images'])
        self.assertFalse(result['report']['items'])
        self.assertEqual(result['report']['skipped'][0]['id'], 'roi')
        for value in (-1, 13, True, 1.5, '4'):
            with self.assertRaises(ValueError):
                compact_preview_evidence(evidence, max_images=value)
        for value in ({}, {'images': {}, 'report': {'items': ['bad']}}):
            with self.assertRaises(ValueError):
                compact_preview_evidence(value)

    def test_compact_preview_small_evidence_preserves_report_schema_without_match_ids(self):
        evidence = {'images': {'one': self.core}, 'report': {
            'items': [{'id': 'roi', 'image_id': 'one', 'source_box': [0, 0, 1, 1]}],
            'skipped': [], 'image_count': 1, 'total_pixels': 640*360}}
        result = compact_preview_evidence(evidence)
        self.assertEqual(result, evidence)
        self.assertIsNot(result, evidence)
        self.assertIsNot(result['report'], evidence['report'])
        self.assertIsNot(result['report']['items'][0], evidence['report']['items'][0])
        self.assertNotIn('selection', result['report'])


if __name__ == '__main__':
    unittest.main()
