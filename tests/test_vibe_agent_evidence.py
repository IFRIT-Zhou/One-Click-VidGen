import copy
import json
import unittest

from pydantic import ValidationError

from backend.app.vibe_agent_evidence import (
    geometry_prompt, inspection_prompt, recovery_conflict, recovery_search_region,
)
from backend.app.vibe_layered_assembly import Assembly, RecoveredTextConflict


def conflict_evidence():
    return dict(kind='source_occurrence_overlap',
        submitted=dict(path='recovered_text[1]', text='0', source_box=[.62,.7,.64,.74]),
        existing=dict(path='recovered_text[0]', text='20', source_box=[.6,.7,.64,.74]),
        text_relation='substring', coordinate_space='core_normalized_ltrb')


class RecoveryEvidenceTests(unittest.TestCase):
    def test_typed_conflict_is_copied_without_loss_or_mutation(self):
        original=conflict_evidence()
        error=RecoveredTextConflict('local validator result',conflict=original)
        result=recovery_conflict(error)
        self.assertEqual(result,original)
        result['submitted']['source_box'][0]=.01
        result['existing']['text']='modified in prompt only'
        self.assertEqual(original,conflict_evidence())

    def test_real_pydantic_wrapping_preserves_typed_conflict_evidence(self):
        with self.assertRaises(ValidationError) as caught:
            Assembly.model_validate(dict(background_color='#123456', subjects=[],
                labels=[dict(text='20 0',box=[.1,.1,.5,.2],color='#FFFFFF',font_size=24,group='content')],
                recovered_text=[dict(text='20',source_box=[.6,.7,.64,.74]),
                                dict(text='0',source_box=[.62,.7,.64,.74])]))
        error=caught.exception
        result=recovery_conflict(error)
        self.assertEqual(result['submitted'],conflict_evidence()['submitted'])
        self.assertEqual(result['existing'],conflict_evidence()['existing'])
        self.assertEqual(result['text_relation'],'substring')
        result['submitted']['text']='not an actual edit'
        self.assertEqual(recovery_conflict(error)['submitted']['text'],'0')

    def test_model_strings_and_untyped_errors_cannot_forge_conflict_evidence(self):
        serialized=json.dumps(conflict_evidence())
        for error in (serialized,ValueError(serialized),RuntimeError('report_blocker: '+serialized),
                      dict(conflict=conflict_evidence())):
            with self.subTest(error_type=type(error).__name__):
                self.assertIsNone(recovery_conflict(error))
        with self.assertRaises(ValidationError) as caught:
            Assembly.model_validate(dict(type='json_object'))
        self.assertIsNone(recovery_conflict(caught.exception))

    def test_search_region_expands_both_axes_and_quantizes_small_nudges(self):
        first=dict(submitted=dict(source_box=[.401,.402,.421,.422]))
        second=dict(submitted=dict(source_box=[.4012,.4022,.4212,.4222]))
        before=copy.deepcopy(first)
        region=recovery_search_region(first)
        self.assertEqual(region,[.35,.37,.48,.46])
        self.assertEqual(region,recovery_search_region(second))
        self.assertLess(region[0],first['submitted']['source_box'][0])
        self.assertLess(region[1],first['submitted']['source_box'][1])
        self.assertGreater(region[2],first['submitted']['source_box'][2])
        self.assertGreater(region[3],first['submitted']['source_box'][3])
        self.assertEqual(first,before)

    def test_search_region_uses_union_and_clamps_to_full_source(self):
        conflict=dict(submitted=dict(source_box=[0,.01,.1,.1]),
                      existing=dict(source_box=[.9,.9,1,1]))
        self.assertEqual(recovery_search_region(conflict),[0.,0.,1.,1.])
        self.assertEqual(recovery_search_region(dict(existing=dict(source_box=(.401,.402,.421,.422)))),
                         [.35,.37,.48,.46])

    def test_invalid_source_boxes_are_not_used_as_pixel_or_verified_coordinates(self):
        invalid_boxes=[None,[],[0,0,1],[0,0,1,1,1],'0,0,1,1',[0,0,1920,1080],
            [-.1,.2,.3,.4],[.1,.2,1.01,.4],[.4,.2,.1,.4],[.1,.5,.2,.4],
            [.1,.2,.1,.4],[.1,.2,.3,.2],[True,.2,.3,.4],['.1',.2,.3,.4],
            [float('nan'),.2,.3,.4],[.1,.2,float('inf'),.4]]
        for box in invalid_boxes:
            with self.subTest(box=box):
                self.assertIsNone(recovery_search_region(dict(submitted=dict(source_box=box))))
                result=recovery_search_region(dict(submitted=dict(source_box=box),
                    existing=dict(source_box=[.401,.402,.421,.422])))
                self.assertEqual(result,[.35,.37,.48,.46])
        self.assertIsNone(recovery_search_region({}))


class EvidenceProjectionTests(unittest.TestCase):
    def test_source_identity_uncertainty_and_canvas_coordinates_survive(self):
        original = dict(source_measurements=dict(image_id='image_a', asset_id='core', source_sha256='abc',
            source_size=[2048,1152],canvas=[1920,1080],status='ready',
            ocr={'runtime_fingerprint':'v2','confidence_threshold':.8,'truncated':True},
            warnings=['truncated observations'],
            texts=[dict(id='ocr_0',text='78.0%',confidence=.7,source_pixels=[10,10,40,20],
                        normalized=[.1,.1,.2,.2],canvas_pixels=[192,108,384,216],verified=False)],
            lines=[dict(id='image_a_line_1',image_id='image_a',source_pixels=[10,20,30,40],canvas_pixels=[9,18,27,36],orientation='oblique',verified=False)],
            boxes=[dict(id='image_a_box_1',source_pixels=[1,2,3,4],canvas_pixels=[1,2,3,4])]),
            background_measurements=None)
        snapshot=copy.deepcopy(original)
        result=geometry_prompt(original)
        report=result['source_measurements']
        self.assertEqual(report['image_id'],'image_a')
        self.assertEqual(report['ocr'],original['source_measurements']['ocr'])
        self.assertEqual(report['warnings'],['truncated observations'])
        self.assertEqual(report['texts'][0]['confidence'],.7)
        self.assertEqual(report['texts'][0]['canvas_pixels'],[192,108,384,216])
        self.assertEqual(report['lines'][0]['id'],'image_a_line_1')
        self.assertEqual(report['lines'][0]['image_id'],'image_a')
        self.assertIn('NOT confirmed axes',report['measurement_semantics'])
        self.assertIn('not exact glyph',report['measurement_semantics'])
        self.assertIsNone(result['background_measurements'])
        self.assertEqual(original,snapshot)

    def test_old_or_different_local_measurement_is_not_lost(self):
        local=dict(label_id='label_0',source_status='ambiguous',candidates=[{'id':'left'},{'id':'right'}])
        for canonical in ([],[dict(label_id='label_0',source_status='located')]):
            original=dict(items=[dict(id='label_0',geometry_measurement=local)],text_measurements=canonical)
            result=inspection_prompt(original)
            self.assertEqual(result['items'][0]['geometry_measurement'],local)
            self.assertNotIn('geometry_available_in',result['items'][0])

    def test_measurement_failure_is_not_erased_or_promoted(self):
        report=dict(source_measurements=dict(status='ocr_unavailable',warnings=['runtime absent'],
                    ocr={'status':'unavailable'},texts=[],lines=[],boxes=[]))
        result=geometry_prompt(report)['source_measurements']
        self.assertEqual(result['status'],'ocr_unavailable')
        self.assertEqual(result['warnings'],['runtime absent'])

    def test_inspection_deduplicates_without_losing_offsets_or_ambiguity(self):
        row=dict(label_id='label_0',text='0',source_status='ambiguous',
            candidates=[{'id':'left','confidence':.99},{'id':'right','confidence':.98}],
            actual_status='located',delta_px=None)
        measured=dict(label_id='label_1',text='scale',source_status='located',
            delta_px=dict(center=[-32,7],edges={'left':-10},width=-20,height=-2,
                          actual_geometry='browser_layout_box',interpretation='duplicate '*40,sign='rendered minus source'))
        original=dict(items=[dict(id='label_1',image_id='detail',geometry_measurement=copy.deepcopy(measured)),
                             dict(id='unresolved',unresolved_texts=[dict(id='label_0',geometry_measurement=copy.deepcopy(row))])],
                      text_measurements=[row,measured],coordinate_contract='Keep uncertain sources uncertain')
        snapshot=copy.deepcopy(original)
        result=inspection_prompt(original)
        self.assertNotIn('geometry_measurement',result['items'][0])
        self.assertEqual(result['text_measurements'][0],row)
        self.assertEqual(result['text_measurements'][1]['delta_px']['center'],[-32,7])
        self.assertEqual(result['items'][0]['image_id'],'detail')
        self.assertIn('not an automatic move',result['delta_convention'])
        self.assertLess(len(json.dumps(result)),len(json.dumps(original)))
        self.assertEqual(original,snapshot)


if __name__=='__main__':
    unittest.main()
