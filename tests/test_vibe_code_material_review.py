"""Pure semantic review contracts; no pixel inference or API calls."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from pydantic import ValidationError

from backend.app.vibe_code_material_review import MaterialReview, PROMPT, VERSION, validate_material_review, validate_material_selection, fingerprint


def entry(index=0, **changes):
    return dict(asset_id=f'subject_{index}', source_crop_complete=True,
                source_edges={edge: dict(continues_outside=False,
                    evidence=f'{edge}边内侧可见完整轮廓，外侧仅有背景，没有同主体继续延伸的像素。')
                    for edge in ('top', 'right', 'bottom', 'left')},
                source_crop_evidence='已对照完整源图与裁框四周，主体全部可见轮廓均在当前源裁区内，没有框外延伸。',
                prepared_complete=False, original_complete=True,
                recommended_asset_id=f'subject_{index}_original',
                evidence='原始裁片保留全部外围组成；prepared 左上方轮廓缺失，因此推荐完整原始裁片。', **changes)


class MaterialReviewTests(unittest.TestCase):
    def setUp(self):
        self.inventory = {'subjects': [{}]}
        self.assets = {'subject_0': 'prepared.png', 'subject_0_original': 'original.png'}

    def review(self, row=None, issues=None, assets=None, inventory=None):
        return validate_material_review({'subjects': [row or entry()], 'issues': issues or []},
            self.assets if assets is None else assets, self.inventory if inventory is None else inventory)

    def test_complete_original_recommended_and_report_roundtrips_for_persistence(self):
        result = self.review()
        self.assertIsInstance(result, MaterialReview)
        restored = MaterialReview.model_validate_json(result.model_dump_json())
        self.assertFalse(restored.subjects[0].prepared_complete)
        self.assertEqual(restored.subjects[0].recommended_asset_id, 'subject_0_original')
        self.assertEqual(validate_material_review(restored, self.assets, self.inventory), restored)

    def test_each_subject_covered_exactly_once_and_no_invented_ids(self):
        for subjects in ([], [entry(), entry()], [entry(1)]):
            with self.subTest(subjects=subjects), self.assertRaises(ValueError):
                validate_material_review({'subjects': subjects}, self.assets, self.inventory)
        with self.assertRaises(ValidationError):
            validate_material_review({'subjects': [dict(entry(), asset_id='subject_99')]}, self.assets, self.inventory)
        inventory = SimpleNamespace(subjects=[{}, {}])
        assets = {**self.assets, 'subject_1': 'prepared1.png', 'subject_1_original': 'original1.png'}
        self.assertEqual(len(validate_material_review({'subjects': [entry(), entry(1)]}, assets, inventory).subjects), 2)

    def test_recommendation_cannot_select_missing_broken_or_other_subject_variant(self):
        for recommendation in ('subject_0', 'subject_1', 'subject_1_original'):
            with self.subTest(recommendation=recommendation), self.assertRaises(ValueError):
                self.review(dict(entry(), recommended_asset_id=recommendation))
        with self.assertRaises(ValueError):
            self.review(assets={'subject_0': 'prepared.png'})
        with self.assertRaises(ValidationError):
            self.review(dict(entry(), recommended_asset_id='C:/arbitrary.png'))

    def test_provided_original_requires_verdict_and_absent_original_requires_null(self):
        with self.assertRaises(ValueError):
            self.review(dict(entry(), original_complete=None))
        row = dict(entry(), prepared_complete=True, original_complete=None, recommended_asset_id='subject_0')
        self.assertTrue(self.review(row, assets={'subject_0': 'prepared.png'}).subjects[0].prepared_complete)
        with self.assertRaises(ValueError):
            self.review(row, assets={})

    def test_no_complete_version_requires_null_and_explicit_per_subject_retake_issue(self):
        row = dict(entry(), original_complete=False, recommended_asset_id=None)
        with self.assertRaises(ValueError):
            self.review(row)
        with self.assertRaises(ValueError):
            self.review(row, issues=['subject_0 这个素材不太好看'])
        result = self.review(row, issues=['subject_0 需要重新取材，原始裁片和prepared均截掉左上轮廓。'])
        self.assertIsNone(result.subjects[0].recommended_asset_id)
        with self.assertRaises(ValueError):
            self.review(dict(entry(), recommended_asset_id=None))

    def test_unavailable_versions_never_become_fictitious_complete_materials(self):
        row = dict(entry(), prepared_complete=False, original_complete=None, recommended_asset_id=None)
        result = self.review(row, assets={}, issues=['subject_0 needs a source retake; no complete bound raster is available.'])
        self.assertIsNone(result.subjects[0].recommended_asset_id)
        with self.assertRaises(ValueError):
            self.review(dict(row, prepared_complete=True), assets={})

    def test_booleans_detailed_evidence_and_extra_fields_are_strict(self):
        for changes in ({'prepared_complete': 'true'}, {'original_complete': 1},
                        {'source_crop_complete': 'true'}, {'source_crop_complete': 1},
                        {'source_crop_evidence': '完整'}, {'source_crop_evidence': ' '*30},
                        {'evidence': '完整'}, {'color': '#fff'}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.review(dict(entry(), **changes))

    def test_new_source_verdict_and_evidence_are_required_and_old_version_rejected(self):
        self.assertEqual(VERSION, 'ocv-code-material-review-v3')
        for key in ('source_edges', 'source_crop_complete', 'source_crop_evidence'):
            row = entry(); row.pop(key)
            with self.subTest(key=key), self.assertRaises(ValidationError):
                self.review(row)
        for old_version in ('ocv-code-material-review-v1', 'ocv-code-material-review-v2'):
            with self.subTest(old_version=old_version), self.assertRaises(ValidationError):
                validate_material_review({'version': old_version, 'subjects': [entry()]},
                                         self.assets, self.inventory)
        self.assertIn('完整source原图', PROMPT)
        self.assertIn('邻域', PROMPT)
        self.assertIn('不是当前裁片内坐标', PROMPT)
        self.assertIn('source_context.available=false', PROMPT)
        self.assertIn('source_crop_complete必须为null', PROMPT)
        self.assertIn('Full source', PROMPT)
        self.assertIn('semantic_crop_verdict=not_inferred', PROMPT)
        self.assertIn('黄色标示当前裁区，青色标示邻域范围', PROMPT)
        self.assertIn('source_edges.top/right/bottom/left', PROMPT)
        self.assertIn('不是本轮事实', PROMPT)

    def test_four_edge_fields_are_required_strict_and_detailed(self):
        for edge in ('top', 'right', 'bottom', 'left'):
            row = entry(); row['source_edges'].pop(edge)
            with self.subTest(missing=edge), self.assertRaises(ValidationError):
                self.review(row)
            for change in ({'continues_outside': 'false'}, {'continues_outside': 0},
                           {'evidence': '完整'}, {'evidence': ' '*30}, {'other': False}):
                row = entry(); row['source_edges'][edge].update(change)
                with self.subTest(edge=edge, change=change), self.assertRaises(ValidationError):
                    self.review(row)
            row = entry(); row['source_edges'][edge].pop('continues_outside')
            with self.subTest(missing_verdict=edge), self.assertRaises(ValidationError):
                self.review(row)
        row = entry(); row['source_edges']['center'] = copy.deepcopy(row['source_edges']['top'])
        with self.assertRaises(ValidationError):
            self.review(row)

    def test_edge_extension_and_uncertainty_cannot_be_overruled_by_complete_crop(self):
        for edge in ('top', 'right', 'bottom', 'left'):
            for verdict, expected in ((True, False), (None, None)):
                row = entry(); row['source_edges'][edge]['continues_outside'] = verdict
                row.update(prepared_complete=False, original_complete=False, recommended_asset_id=None)
                for crop_verdict in (True, False, None):
                    row['source_crop_complete'] = crop_verdict
                    with self.subTest(edge=edge, verdict=verdict, crop_verdict=crop_verdict):
                        if crop_verdict is expected:
                            reviewed = self.review(row, issues=['subject_0需要重新取材，逐边检查发现截断或证据不足。'])
                            self.assertIs(reviewed.subjects[0].source_crop_complete, expected)
                        else:
                            with self.assertRaisesRegex(ValidationError, '逐边检查'):
                                self.review(row)
        row = entry(); row.update(source_crop_complete=False, prepared_complete=False,
                                  original_complete=False, recommended_asset_id=None)
        row['source_edges']['top']['continues_outside'] = None
        row['source_edges']['bottom']['continues_outside'] = True
        self.assertFalse(self.review(row, issues=['subject_0需要重新取材，下边缘已确认截断，上边缘暂时不清楚。']).subjects[0].source_crop_complete)

    def test_source_image_boundary_can_be_complete_without_inventing_missing_content(self):
        row = entry()
        row['source_edges']['bottom']['evidence'] = '下边已达到完整源图的最下方，主体在原图中本就触边；没有更多源像素可补，不要求生成。'
        self.assertTrue(self.review(row).subjects[0].source_crop_complete)

    def test_only_exact_top_level_json_object_marker_is_compatible(self):
        data = {'type': 'json_object', 'subjects': [entry()]}
        before = copy.deepcopy(data)
        result = validate_material_review(data, self.assets, self.inventory)
        self.assertNotIn('type', result.model_dump())
        self.assertEqual(data, before)
        for bad_type in ('object', 'JSON_OBJECT', None, True, {'type': 'json_object'}):
            with self.subTest(bad_type=bad_type), self.assertRaises(ValidationError):
                validate_material_review(dict(data, type=bad_type), self.assets, self.inventory)
        for extra in ({'unexpected': 1}, {'response_format': {'type': 'json_object'}}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                validate_material_review(dict(data, **extra), self.assets, self.inventory)
        with self.assertRaises(ValidationError):
            self.review(dict(entry(), type='json_object'))

    def test_incomplete_or_uncertain_source_cannot_claim_any_complete_variant(self):
        for source_verdict in (False, None):
            for variant in ('prepared_complete', 'original_complete'):
                row = dict(entry(), source_crop_complete=source_verdict,
                           prepared_complete=False, original_complete=False, recommended_asset_id=None)
                row[variant] = True
                with self.subTest(source_verdict=source_verdict, variant=variant), self.assertRaisesRegex(ValueError, '源裁区'):
                    self.review(row, issues=['subject_0 需要重新取材，裁框截断外围轮廓。'])
            row = dict(entry(), source_crop_complete=source_verdict,
                       prepared_complete=False, original_complete=False, recommended_asset_id=None)
            result = self.review(row, issues=['subject_0 需要重新取材，当前无法确定安全的完整源裁区。'])
            self.assertIsNone(result.subjects[0].recommended_asset_id)
            with self.assertRaisesRegex(ValueError, '源裁区'):
                validate_material_selection(result, {'assets': [{'id': 'subject_0_original'}]})

    def test_bounded_retake_requires_known_unique_subjects_valid_source_coords_and_issue(self):
        row = dict(entry(), source_crop_complete=False,
                   prepared_complete=False, original_complete=False, recommended_asset_id=None)
        retake = dict(asset_id='subject_0', source_crop=[.1, .2, .6, .8],
                      reason='对照完整源图，下边缘仍有主体延伸，应补齐同一主体的可见脚部。')
        data = dict(subjects=[row], issues=['subject_0 需要重新取材，下边缘截断主体。'], retakes=[retake])
        before = copy.deepcopy(data)
        result = validate_material_review(data, self.assets, self.inventory)
        self.assertEqual(list(result.retakes[0].source_crop), [.1, .2, .6, .8])
        self.assertEqual(data, before)
        self.assertEqual(result.model_dump()['retakes'][0]['asset_id'], 'subject_0')
        for changes in ({'retakes': [retake, retake]},
                        {'retakes': [dict(retake, asset_id='subject_1')]},
                        {'retakes': [dict(retake, source_crop=[.6, .2, .1, .8])]},
                        {'retakes': [dict(retake, source_crop=[False, .2, .6, .8])]},
                        {'retakes': [dict(retake, source_crop=[-.1, .2, .6, .8])]},
                        {'retakes': [dict(retake, source='other')]},
                        {'issues': []},
                        {'subjects': [dict(row, recommended_asset_id='subject_0_original')]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_material_review(dict(data, **changes), self.assets, self.inventory)

    def test_complete_subject_with_foreign_frame_can_request_retake_but_cannot_render_pending(self):
        row = dict(entry(), prepared_complete=True, recommended_asset_id=None)
        retake = dict(asset_id='subject_0', source_crop=[.1, .2, .5, .7],
                      reason='主体全部可见，但裁片外缘夹带邻近卡框，可缩窄右侧无主体区域排除。')
        data = dict(subjects=[row], issues=['subject_0 需要重新取材，排除右侧相邻旧卡框。'], retakes=[retake])
        result = validate_material_review(data, self.assets, self.inventory)
        self.assertTrue(result.subjects[0].source_crop_complete)
        self.assertTrue(result.subjects[0].original_complete)
        self.assertIsNone(result.subjects[0].recommended_asset_id)
        for identity in ('subject_0', 'subject_0_original'):
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, '重新取材'):
                validate_material_selection(result, {'assets': [{'id': identity}]})

    def test_retake_cannot_be_requested_for_empty_inventory(self):
        with self.assertRaises(ValueError):
            validate_material_review({'subjects': [], 'retakes': [dict(asset_id='subject_0',
                source_crop=[0, 0, 1, 1], reason='没有主体却请求重裁')]}, {}, {'subjects': []})

    def test_no_subjects_is_valid_and_validation_does_not_mutate_inputs(self):
        self.assertEqual(validate_material_review({'subjects': []}, {}, {'subjects': []}).subjects, [])
        data = {'subjects': [entry()]}; before = copy.deepcopy(data)
        result = validate_material_review(data, self.assets, self.inventory)
        result.subjects[0].evidence = '局部修改返回模型，不应该改变传入数据的原始审查证据内容。'
        self.assertEqual(data, before)
        self.assertIn('opacity', PROMPT)
        self.assertIn('外围', PROMPT)

    def test_uncertain_variant_is_not_recommended_or_selected(self):
        row = dict(entry(), prepared_complete=None)
        review = self.review(row)
        with self.assertRaisesRegex(ValueError, '无法确认完整'):
            validate_material_selection(review, {'assets': [{'id': 'subject_0'}]})
        self.assertTrue(validate_material_selection(review, {'assets': [{'id': 'background'}, {'id': 'subject_0_original'}]}))
        with self.assertRaises(ValueError):
            self.review(dict(row, recommended_asset_id='subject_0'))

    def test_actual_damaged_selection_retains_evidence_and_never_rewrites_id(self):
        review = self.review()
        measured = {'assets': [{'id': 'subject_0', 'visible': True}]}
        before = copy.deepcopy(measured)
        with self.assertRaisesRegex(ValueError, 'subject_0_original') as caught:
            validate_material_selection(review, measured)
        self.assertIn(review.subjects[0].evidence, str(caught.exception))
        self.assertEqual(measured, before)
        for identity in ('subject_1', 'unreviewed', '../other'):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                validate_material_selection(review, {'assets': [{'id': identity}]})

    def test_fingerprint_uses_actual_bytes_crop_notes_and_not_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {}
            for name, content in [('core', b'original reference bytes'), ('cutout', b'RGBA actual bytes'), ('original', b'original crop bytes')]:
                paths[name] = root/(name+'.png'); paths[name].write_bytes(content)
            inventory = {'subjects': [{'source': 'core', 'crop': [.1, .2, .8, .9], 'treatment': 'cutout', 'embedded_text': []}]}
            sources = {'core': paths['core']}
            assets = {'subject_0': paths['cutout'], 'subject_0_original': paths['original']}
            key = fingerprint(inventory, sources, assets, notes={'required_contour': 'all peripheral components'})
            moved = root/'copy.png'; moved.write_bytes(paths['cutout'].read_bytes())
            self.assertEqual(key, fingerprint(inventory, sources, dict(assets, subject_0=moved), notes={'required_contour': 'all peripheral components'}))
            moved.write_bytes(b'changed alpha pixels')
            self.assertNotEqual(key, fingerprint(inventory, sources, dict(assets, subject_0=moved), notes={'required_contour': 'all peripheral components'}))
            changed = copy.deepcopy(inventory); changed['subjects'][0]['crop'][0] = .12
            self.assertNotEqual(key, fingerprint(changed, sources, assets, notes={'required_contour': 'all peripheral components'}))
            self.assertNotEqual(key, fingerprint(inventory, sources, assets, notes={'required_contour': 'different requirement'}))
            paths['core'].write_bytes(b'changed source original')
            self.assertNotEqual(key, fingerprint(inventory, sources, assets, notes={'required_contour': 'all peripheral components'}))
            self.assertEqual(len(key), 64)

    def test_fingerprint_rejects_missing_or_over_limit_files_without_path_disclosure(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'private-name.png'
            with self.assertRaises(ValueError) as caught:
                fingerprint(self.inventory, {'core': path}, {})
            self.assertNotIn(str(path), str(caught.exception))
            with path.open('wb') as stream:
                stream.truncate(64*1024*1024+1)
            with self.assertRaisesRegex(ValueError, '64MB'):
                fingerprint(self.inventory, {'core': path}, {})


if __name__ == '__main__':
    unittest.main()
