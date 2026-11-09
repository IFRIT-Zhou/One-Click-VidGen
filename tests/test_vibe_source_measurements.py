"""Source localization is tool evidence, not a model estimate or quality pass."""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from backend.app import vibe_source_measurements as source


class SourceMeasurementsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.path = self.folder/'poster.png'
        self.size = [1000, 600]
        self.canvas = [1500, 900]
        image = Image.new('RGB', self.size, '#08213d')
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 150, 370, 280), outline='#4eeaff', width=3)
        draw.line((100, 430, 850, 430), fill='white', width=2)
        image.save(self.path)
        image.close()

    def ocr(self, texts=None, **kwargs):
        result = dict(status='ready', source_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),
            source_size=self.size, coordinate_space='source_pixels', backend='rapidocr', version='test',
            texts=texts if texts is not None else [dict(id='detector_0', text='亚组观察结果',
                box=[420, 550, 580, 580], confidence=.98)])
        result.update(kwargs)
        return result

    def report(self, texts=None, **kwargs):
        return source.build_source_measurements(self.path, self.ocr(texts, **kwargs), self.canvas)

    def test_build_exact_source_binding_and_three_coordinate_spaces(self):
        before = self.path.read_bytes()
        report = self.report()
        self.assertEqual(report['status'], 'ready')
        self.assertEqual(report['source_sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(report['source_size'], self.size)
        self.assertEqual(report['canvas'], self.canvas)
        row = report['texts'][0]
        self.assertEqual(row['source_pixels'], [420., 550., 580., 580.])
        self.assertEqual(row['normalized'], [.42, .916666667, .58, .966666667])
        self.assertEqual(row['canvas_pixels'], [630., 825., 870., 870.])
        self.assertFalse(row['verified'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ['poster.png'])

    def test_geometry_is_bounded_candidate_not_semantic_frame_claim(self):
        report = self.report()
        self.assertEqual(report['geometry']['status'], 'ready')
        self.assertTrue(report['lines'])
        self.assertTrue(report['boxes'])
        self.assertLessEqual(len(report['lines']), source.MAX_LINES)
        self.assertLessEqual(len(report['boxes']), source.MAX_BOXES)
        for row in [*report['lines'], *report['boxes']]:
            self.assertFalse(row['verified'])
            self.assertEqual(row['semantic_role'], 'unassigned')
            self.assertTrue(all(math.isfinite(n) for n in row['source_pixels']))

    def test_geometry_ids_and_coordinate_provenance_are_bound_to_image_bytes(self):
        report = self.report()
        image_id = 'image_'+hashlib.sha256(self.path.read_bytes()).hexdigest()[:16]
        self.assertEqual(report['image_id'], image_id)
        self.assertEqual(report['geometry']['image_id'], image_id)
        provenance = report['coordinate_provenance']
        self.assertEqual(provenance['source_sha256'], report['source_sha256'])
        self.assertEqual(provenance['image_id'], image_id)
        self.assertEqual(provenance['source_size'], self.size)
        self.assertEqual(provenance['canvas_size'], self.canvas)
        self.assertEqual(provenance['line_format'], 'x1_y1_x2_y2')
        self.assertEqual(provenance['text_and_box_format'], 'left_top_right_bottom')
        for name, kind in [('lines', 'line'), ('boxes', 'box')]:
            for index, row in enumerate(report[name]):
                self.assertEqual(row['id'], f'{image_id}_{kind}_{index}')
                self.assertEqual(row['image_id'], image_id)
                self.assertFalse(row['verified'])
                self.assertEqual(row['semantic_role'], 'unassigned')
        self.assertEqual(report['texts'][0]['image_id'], image_id)

    def test_same_dimensions_do_not_make_core_and_background_candidates_the_same(self):
        core = self.report()
        other = self.folder/'background.png'
        with Image.open(self.path) as image:
            # Preserve the drawn geometry but change the immutable source.
            image.putpixel((0, 0), (255, 255, 255))
            image.save(other)
        background = source.build_source_measurements(other, {'status': 'unavailable'}, self.canvas)
        self.assertEqual(core['source_size'], background['source_size'])
        self.assertEqual(core['canvas'], background['canvas'])
        self.assertNotEqual(core['image_id'], background['image_id'])
        for kind in ('lines', 'boxes'):
            self.assertTrue(core[kind])
            self.assertTrue(background[kind])
            core_ids = {row['id'] for row in core[kind]}
            background_ids = {row['id'] for row in background[kind]}
            self.assertFalse(core_ids & background_ids)
        # Core geometry cannot be looked up as an observed background residual.
        cited_core_line = core['lines'][0]['id']
        self.assertFalse(any(row['id'] == cited_core_line for row in background['lines']))
        with self.assertRaises(ValueError):
            source.merge_source_measurements(core, background)

    def test_same_bytes_keep_identity_across_paths_and_canvas_scaling(self):
        core = self.report()
        copy = self.folder/'same-background-name.png'
        copy.write_bytes(self.path.read_bytes())
        other_canvas = [2000, 1200]
        report = source.build_source_measurements(copy, self.ocr(), other_canvas)
        self.assertEqual(report['image_id'], core['image_id'])
        self.assertEqual(report['coordinate_provenance']['canvas_size'], other_canvas)
        for kind in ('lines', 'boxes'):
            self.assertEqual([row['id'] for row in report[kind]], [row['id'] for row in core[kind]])
            self.assertEqual(report[kind][0]['source_pixels'], core[kind][0]['source_pixels'])
            self.assertNotEqual(report[kind][0]['canvas_pixels'], core[kind][0]['canvas_pixels'])

    def test_wrong_source_ocr_is_not_promoted_by_current_image_provenance(self):
        other = self.folder/'other.png'
        with Image.new('RGB', self.size, 'black') as image:
            image.save(other)
        report = source.build_source_measurements(other, self.ocr(), self.canvas)
        self.assertEqual(report['image_id'], 'image_'+hashlib.sha256(other.read_bytes()).hexdigest()[:16])
        self.assertEqual(report['status'], 'ocr_unavailable')
        self.assertEqual(report['texts'], [])
        measured = {'texts': [{'id': 'foot', 'text': '亚组观察结果', 'box': [630, 825, 240, 45]}]}
        result = source.compare_text_geometry(report, {'texts': [{'text': '亚组观察结果'}]}, measured, self.canvas)[0]
        self.assertEqual(result['source_status'], 'unresolved')
        self.assertIsNone(result['delta_px'])
        self.assertFalse(result['automatic_quality_verdict'])

    def test_ocr_unavailable_retains_geometry_and_never_claims_text_absent(self):
        report = source.build_source_measurements(self.path, {'status': 'unavailable'}, self.canvas)
        self.assertEqual(report['status'], 'ocr_unavailable')
        self.assertEqual(report['texts'], [])
        self.assertTrue(report['lines'])
        result = source.locate_text(report, '亚组观察结果', [.01, .01, .2, .1])
        self.assertEqual(result['status'], 'unresolved')
        self.assertIsNone(result['tool_box'])

    def test_stale_or_unbound_ocr_cannot_receive_new_image_binding(self):
        for wrong in (None, '0'*64):
            with self.subTest(hash=wrong):
                report = self.report(source_sha256=wrong)
                self.assertEqual(report['status'], 'ocr_unavailable')
                self.assertEqual(report['texts'], [])
        self.assertEqual(self.report(source_size=[600, 1000])['texts'], [])
        self.assertEqual(self.report(coordinate_space='crop_pixels')['texts'], [])

    def test_exif_dimensions_are_used_for_source_binding(self):
        self.path = self.folder/'rotated.jpg'
        image = Image.new('RGB', (120, 80), 'blue')
        exif = image.getexif(); exif[274] = 6
        image.save(self.path, exif=exif); image.close()
        self.size = [80, 120]
        report = source.build_source_measurements(self.path,
            self.ocr([dict(text='title', box=[10, 20, 60, 40], confidence=.9)]), [400, 600])
        self.assertEqual(report['source_size'], [80, 120])
        self.assertEqual(report['texts'][0]['canvas_pixels'], [50., 100., 300., 200.])

    def test_malformed_ocr_and_truncation_are_visible(self):
        rows = [dict(text=f'row {i}', box=[1, i+1, 80, i+2], confidence=.95) for i in range(140)]
        report = self.report([dict(text='bad', box=[0, 1, float('nan'), 20], confidence=.9),
                              dict(text='bad2', box=[0, 1, 10, 20], confidence=True), *rows])
        self.assertEqual(len(report['texts']), source.MAX_TEXTS)
        self.assertTrue(report['ocr']['truncated'])
        self.assertEqual(report['ocr']['rejected'], 2)
        self.assertTrue(report['warnings'])

    def test_optional_opencv_failure_does_not_discard_ocr(self):
        import cv2
        with patch.object(cv2, 'Canny', side_effect=RuntimeError('broken runtime')):
            report = self.report()
        self.assertEqual(report['status'], 'ready')
        self.assertEqual(report['geometry']['status'], 'unavailable')
        self.assertTrue(report['texts'])
        self.assertEqual(report['lines'], [])

    def test_wrong_model_roi_is_not_used_to_place_footer(self):
        report = self.report()
        result = source.locate_text(report, '亚组观察结果', [.17, .93, .32, .99])
        self.assertEqual(result['status'], 'located')
        self.assertEqual(result['tool_box'], report['texts'][0]['normalized'])
        self.assertFalse(result['evidence']['estimated_box_used_for_matching'])
        self.assertNotEqual(result['tool_box'], result['estimated_source_box'])

    def test_blank_roi_or_absent_text_remains_unresolved(self):
        result = source.locate_text(self.report(), '不存在的文字', [.17, .93, .32, .99])
        self.assertEqual(result['status'], 'unresolved')
        self.assertIsNone(result['tool_box'])
        self.assertIsNone(result['confidence'])
        self.assertIn('absence is not established', result['evidence']['reason'])

    def test_duplicate_copy_does_not_snap_to_estimated_nearest_box(self):
        report = self.report([dict(text='患者', box=[100, 100, 170, 130], confidence=.99),
                              dict(text='患者', box=[700, 100, 770, 130], confidence=.91)])
        result = source.locate_text(report, '患者', [.1, .16, .17, .22])
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual(len(result['candidates']), 2)
        self.assertIsNone(result['tool_box'])

    def test_low_confidence_is_candidate_not_authoritative_box(self):
        report = self.report([dict(text='结果', box=[10, 10, 80, 40], confidence=.45)])
        result = source.locate_text(report, '结果')
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(len(result['candidates']), 1)
        self.assertIsNone(result['tool_box'])

    def test_typographic_normalization_without_numeric_conflation(self):
        report = self.report([dict(text='Ｃｈｉｌｄ－Ｐｕｇｈ Ａ｜匹配后', box=[100, 50, 650, 100], confidence=.95),
                              dict(text='P=0.043', box=[100, 300, 300, 340], confidence=.95),
                              dict(text='PLT≥50×10⁹/L', box=[600, 300, 850, 340], confidence=.95)])
        self.assertEqual(source.locate_text(report, 'Child-Pugh A|匹配后')['status'], 'located')
        self.assertEqual(source.locate_text(report, 'P=0043')['status'], 'unresolved')
        self.assertEqual(source.locate_text(report, 'PLT≤50×10⁹/L')['status'], 'unresolved')
        self.assertEqual(source.locate_text(report, 'PLT≥50×109/L')['status'], 'unresolved')
        self.assertEqual(source.locate_text(report, 'PLT≥50×10^9/L')['status'], 'located')

    def test_exact_adjacent_lines_can_form_single_label(self):
        report = self.report([dict(text='中国科学技术大学', box=[100, 100, 390, 130], confidence=.98),
                              dict(text='附属第一医院', box=[100, 140, 340, 170], confidence=.96)])
        result = source.locate_text(report, '中国科学技术大学\n附属第一医院')
        self.assertEqual(result['status'], 'located')
        self.assertEqual(result['tool_box'], [.1, .166666667, .39, .283333333])
        self.assertEqual(result['confidence'], .96)
        self.assertEqual(result['candidates'][0]['method'], 'adjacent_text_group')

    def test_separate_distant_columns_are_not_merged(self):
        report = self.report([dict(text='Child-Pugh', box=[100, 100, 250, 130], confidence=.95),
                              dict(text='B', box=[850, 100, 880, 130], confidence=.95)])
        self.assertEqual(source.locate_text(report, 'Child-Pugh B')['status'], 'unresolved')

    def test_partial_ocr_line_cannot_pretend_to_locate_exact_substring(self):
        report = self.report([dict(text='49对患者', box=[100, 200, 400, 270], confidence=.99)])
        result = source.locate_text(report, '49')
        self.assertEqual(result['status'], 'unresolved')
        self.assertIsNone(result['tool_box'])
        self.assertEqual(result['candidates'][0]['method'], 'containing_line_not_exact_bounds')

    def test_duplicate_overlapping_detection_is_not_two_locations(self):
        report = self.report([dict(text='结果', box=[100, 100, 250, 140], confidence=.9),
                              dict(text='结果', box=[101, 100, 251, 140], confidence=.95)])
        result = source.locate_text(report, '结果')
        self.assertEqual(result['status'], 'located')
        self.assertEqual(len(result['candidates']), 1)

    def test_compare_reports_numeric_offset_not_font_size_instruction(self):
        report = self.report()
        inventory = {'texts': [{'text': '亚组观察结果', 'source_box': [.17, .93, .32, .99]}]}
        measured = {'texts': [{'id': 'foot', 'text': '亚组观察结果', 'box': [200, 800, 240, 45]}]}
        row = source.compare_text_geometry(report, inventory, measured, self.canvas)[0]
        self.assertEqual(row['source_status'], 'located')
        self.assertEqual(row['matched_render_ids'], ['foot'])
        self.assertEqual(row['delta_px']['center'], [-430., -25.])
        self.assertEqual(row['delta_px']['edges']['left'], -430.)
        self.assertFalse(row['automatic_quality_verdict'])
        self.assertIn('NOT a mandatory move', row['delta_px']['interpretation'])

    def test_compare_prefers_browser_ink_and_keeps_layout_type_explicit(self):
        report = self.report()
        inventory = SimpleNamespace(labels=[SimpleNamespace(text='亚组观察结果', box=[0, 0, .1, .1])])
        measured = {'texts': [{'id': 'foot', 'text': '亚组观察结果', 'box': [0, 800, 1500, 90],
            'ink_rects': [{'left': 630., 'top': 825., 'right': 870., 'bottom': 870.}]}]}
        row = source.compare_text_geometry(report, inventory, measured, self.canvas)[0]
        self.assertEqual(row['delta_px']['center'], [0., 0.])
        self.assertEqual(row['delta_px']['actual_geometry'], 'browser_glyph_ink_metrics')
        self.assertEqual(row['actual_boxes'], [[.42, .916666667, .58, .966666667]])

    def test_ambiguous_render_instances_do_not_produce_fake_delta(self):
        report = self.report()
        measured = {'texts': [{'id': 'one', 'text': '亚组观察结果', 'box': [100, 100, 200, 40]},
                             {'id': 'two', 'text': '亚组观察结果', 'box': [100, 500, 200, 40]}]}
        row = source.compare_text_geometry(report, {'texts': [{'text': '亚组观察结果'}]}, measured, self.canvas)[0]
        self.assertEqual(row['actual_status'], 'ambiguous')
        self.assertIsNone(row['delta_px'])

    def test_split_number_and_unit_dom_nodes_can_match_one_source_line(self):
        report = self.report([dict(text='49对患者', box=[300, 200, 500, 250], confidence=.96)])
        measured = {'texts': [{'id': 'number', 'text': '49', 'box': [450, 300, 100, 75]},
                             {'id': 'unit', 'text': '对患者', 'box': [550, 300, 200, 75]}]}
        row = source.compare_text_geometry(report, {'texts': [{'text': '49对患者'}]}, measured, self.canvas)[0]
        self.assertEqual(row['actual_status'], 'located')
        self.assertEqual(row['matched_render_ids'], ['number', 'unit'])
        self.assertEqual(row['delta_px']['center'], [0., 0.])

    def test_unresolved_and_wrong_canvas_produce_no_adjustment_delta(self):
        report = self.report()
        measured = {'texts': [{'id': 'foot', 'text': '亚组观察结果', 'box': [200, 800, 240, 45]}]}
        row = source.compare_text_geometry(report, {'texts': [{'text': '亚组观察结果'}]}, measured, [1000, 600])[0]
        self.assertEqual(row['source_status'], 'unresolved')
        self.assertIsNone(row['delta_px'])

    def test_render_outside_canvas_not_clipped_away(self):
        report = self.report()
        measured = {'texts': [{'id': 'foot', 'text': '亚组观察结果', 'box': [-20, 800, 240, 45]}]}
        row = source.compare_text_geometry(report, {'texts': [{'text': '亚组观察结果'}]}, measured, self.canvas)[0]
        self.assertTrue(row['actual_out_of_canvas'])
        self.assertLess(row['actual_boxes'][0][0], 0)

    def test_merge_regional_observations_preserves_binding_and_duplicates(self):
        full = self.report([dict(text='结果', box=[100, 100, 250, 140], confidence=.9)])
        partial = self.report([dict(text='结果', box=[102, 100, 252, 140], confidence=.95),
                               dict(text='结果', box=[700, 100, 850, 140], confidence=.95)],
                              region=[0, .1, 1, .3])
        before = deepcopy(full)
        result = source.merge_source_measurements(full, partial)
        self.assertEqual(full, before)
        self.assertEqual(len(result['texts']), 2)
        self.assertEqual(result['ocr']['coverage'], 'full_plus_regions')
        self.assertEqual(result['texts'][0]['origin'], 'supplemental_local_ocr')
        self.assertEqual(source.locate_text(result, '结果')['status'], 'ambiguous')
        self.assertEqual(result['image_id'], full['image_id'])
        self.assertEqual(result['coordinate_provenance'], full['coordinate_provenance'])
        self.assertEqual(result['lines'], full['lines'])
        self.assertEqual(result['boxes'], full['boxes'])
        self.assertTrue(all(row['image_id'] == full['image_id'] for row in result['texts']))

    def test_merge_refuses_stale_binding(self):
        full = self.report()
        for key, changed in [('source_sha256', '0'*64), ('source_size', [1001, 600]), ('canvas', [1920, 1080])]:
            partial = deepcopy(full); partial[key] = changed
            with self.subTest(key=key), self.assertRaises(ValueError):
                source.merge_source_measurements(full, partial)

    def test_partial_only_does_not_claim_full_source_coverage(self):
        full = source.build_source_measurements(self.path, {'status': 'unavailable'}, self.canvas)
        partial = self.report(region=[.4, .8, .7, 1.])
        result = source.merge_source_measurements(full, partial)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['ocr']['coverage'], 'regions_only')
        self.assertEqual(source.locate_text(result, '亚组观察结果')['status'], 'located')

    def test_merge_failed_supplement_does_not_destroy_known_positions(self):
        full = self.report()
        partial = source.build_source_measurements(self.path, {'status': 'unavailable'}, self.canvas)
        result = source.merge_source_measurements(full, partial)
        self.assertEqual(result['texts'], full['texts'])
        self.assertIn('Supplemental OCR unavailable', result['warnings'][-1])

    def test_build_preserves_ocr_runtime_and_model_identity(self):
        report = self.report(runtime_fingerprint='a'*64, model_version='det-rec-v1')
        self.assertEqual(report['ocr']['runtime_fingerprint'], 'a'*64)
        self.assertEqual(report['ocr']['model_version'], 'det-rec-v1')

    def test_matching_ocr_fingerprint_and_model_can_merge(self):
        full = self.report(runtime_fingerprint='a'*64, model_version='det-rec-v1')
        partial = self.report([dict(text='局部补充', box=[100, 100, 300, 150], confidence=.95)],
            region=[0, 0, .4, .4], runtime_fingerprint='a'*64, model_version='det-rec-v1')
        result = source.merge_source_measurements(full, partial)
        self.assertEqual(len(result['texts']), 2)
        self.assertEqual(result['ocr']['runtime_fingerprint'], 'a'*64)

    def test_old_measurement_version_is_not_merged_as_current_fact(self):
        full = self.report()
        for version in (None, 'ocv-source-measurements-v1', 'ocv-source-measurements-old'):
            partial = self.report([dict(text='旧坐标', box=[100, 100, 300, 150], confidence=.99)])
            partial['version'] = version
            with self.subTest(version=version):
                result = source.merge_source_measurements(full, partial)
                self.assertEqual(result['texts'], full['texts'])
                self.assertIn('history only', result['warnings'][-1])
                self.assertEqual(source.locate_text(result, '旧坐标')['status'], 'unresolved')

    def test_old_baseline_cannot_be_revalidated_by_merging_new_region(self):
        full = self.report()
        full.pop('version')
        partial = self.report(region=[.4, .8, .7, 1.])
        result = source.merge_source_measurements(full, partial)
        self.assertEqual(result['status'], 'ocr_unavailable')
        self.assertEqual(result['texts'], [])
        self.assertTrue(result['ocr']['historical_only'])
        self.assertEqual(source.locate_text(result, '亚组观察结果')['status'], 'unresolved')

    def test_fingerprint_mismatch_or_one_sided_missing_never_merges(self):
        for old, new in [('a'*64, 'b'*64), ('a'*64, None), (None, 'a'*64)]:
            full = self.report(runtime_fingerprint=old)
            partial = self.report([dict(text='旧坐标', box=[100, 100, 300, 150], confidence=.99)],
                runtime_fingerprint=new)
            with self.subTest(old=old, new=new):
                result = source.merge_source_measurements(full, partial)
                self.assertEqual(result['texts'], full['texts'])
                self.assertIn('runtime_fingerprint', result['warnings'][-1])

    def test_model_version_mismatch_or_one_sided_missing_never_merges(self):
        for old, new in [('v1', 'v2'), ('v1', None), (None, 'v1')]:
            full = self.report(runtime_fingerprint='a'*64, model_version=old)
            partial = self.report([dict(text='旧坐标', box=[100, 100, 300, 150], confidence=.99)],
                runtime_fingerprint='a'*64, model_version=new)
            with self.subTest(old=old, new=new):
                result = source.merge_source_measurements(full, partial)
                self.assertEqual(result['texts'], full['texts'])
                self.assertIn('model_version', result['warnings'][-1])

    def test_both_missing_fingerprints_keep_unversioned_test_adapter_compatibility(self):
        full = self.report()
        partial = self.report([dict(text='局部补充', box=[100, 100, 300, 150], confidence=.95)])
        result = source.merge_source_measurements(full, partial)
        self.assertEqual(len(result['texts']), 2)
        self.assertTrue(result['ocr']['supplemental_observations'])

    def test_different_declared_ocr_software_version_is_historical(self):
        full = self.report(version='worker-v1')
        partial = self.report([dict(text='旧坐标', box=[100, 100, 300, 150], confidence=.99)], version='worker-v2')
        result = source.merge_source_measurements(full, partial)
        self.assertEqual(result['texts'], full['texts'])
        self.assertIn('OCR version differs', result['warnings'][-1])


if __name__ == '__main__':
    unittest.main()
