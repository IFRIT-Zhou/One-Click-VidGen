import copy
import hashlib
import unittest

from backend.app.vibe_agent_context import image_roles, working_context


def candidate(name='preview_generic', code='CODE'):
    return dict(preview_id=name, code_sha256=hashlib.sha256(code.encode()).hexdigest(),
                fingerprint='material-v1', locally_valid=True, validation_error=None,
                image_ids=[name, name+'_detail_0'])


def state():
    current = candidate()
    return dict(previews={current['preview_id']: current}, latest_code={'code':'CODE'},
                events=[], observations={}, delivery_reviews={}, review_history=[])


class AgentContextTests(unittest.TestCase):
    def test_review_evidence_receipts_are_compact_and_image_roles_preserve_three_panel_provenance(self):
        value=state()
        evidence=dict(evidence_id='review_evidence_x',preview_id='preview_generic',
            code_sha256=value['previews']['preview_generic']['code_sha256'],material_fingerprint='material-v1',
            image_ids=['review_evidence_x_window_0'],report={'windows':[{'large':'measurement-detail'}]})
        value['review_evidence']={evidence['evidence_id']:evidence}
        receipt=dict(ok=True,review_evidence_id=evidence['evidence_id'],evidence=evidence['report'])
        value['events']=[dict(tool='review_evidence',summary='核实',receipt=receipt)]
        value['observations']={'review_evidence':receipt}
        before=copy.deepcopy(value)
        context=working_context(value,'material-v1')
        self.assertNotIn('evidence',context['observations']['review_evidence'])
        self.assertNotIn('evidence',context['recent_tool_results'][0]['receipt'])
        self.assertIn('current_review_evidence',context['observations']['review_evidence']['evidence_available_in'])
        role=image_roles({'review_evidence_x_window_0':'path'},{'core':'core'},value)['review_evidence_x_window_0']
        self.assertEqual(role['role'],'measured_same_window_review_evidence')
        self.assertIn('middle actual background',role['comparison_order'])
        self.assertFalse(role['is_rendered_result'])
        self.assertTrue(role['contains_rendered_comparison'])
        self.assertEqual(role['preview_id'],'preview_generic')
        value['events'].append(dict(tool='inspect_image',summary='局部',receipt=dict(
            asset_id='inspection_crop',requested_asset_id='review_evidence_x_window_0',region=[.2,.2,.8,.8])))
        cropped=image_roles({'inspection_crop':'crop'},{'core':'core'},value)['inspection_crop']
        self.assertEqual(cropped['evidence_id'],evidence['evidence_id'])
        self.assertEqual(cropped['root_image_id'],'review_evidence_x_window_0')
        self.assertEqual(cropped['region'],[.2,.2,.8,.8])
        self.assertEqual(value['review_evidence'],before['review_evidence'])

    def test_old_validation_feedback_is_scoped_and_current_error_remains_complete(self):
        value=state()
        old=candidate('old','OLD')
        old.update(locally_valid=False,validation_error='old error '*200)
        current=value['previews']['preview_generic']
        current.update(locally_valid=False,validation_error='current error '*200)
        value['previews']={'old':old,'preview_generic':current}
        value['events']=[dict(tool='preview_scene',summary='old attempt',receipt={'error':old['validation_error']}),
                         dict(tool='preview_scene',summary='current attempt',receipt={'error':current['validation_error']})]
        result=working_context(value,'material-v1')
        self.assertEqual(result['previews'][0]['validation_scope'],'historical_attempt_only')
        self.assertLessEqual(len(result['previews'][0]['validation_error']),280)
        self.assertEqual(result['previews'][1]['validation_error'],current['validation_error'])
        self.assertEqual(result['recent_tool_results'][1]['receipt']['error'],current['validation_error'])
        self.assertGreater(len(value['previews']['old']['validation_error']),280)

    def test_historical_validation_evidence_is_scoped_without_erasing_the_local_audit(self):
        value=state()
        old_feedback=dict(kind='recovered_text_conflict',
            conflict=dict(submitted={'text':'0','source_box':[.1,.2,.2,.3]},
                          existing={'text':'20','source_box':[.1,.2,.3,.3]}),
            source_measurements=dict(image_id='old_core',texts=[{'text':'0','confidence':.97}]))
        current_feedback=dict(kind='recovered_text_conflict',
            conflict=dict(submitted={'text':'40','source_box':[.7,.2,.8,.3]},
                          existing={'text':'400','source_box':[.7,.2,.9,.3]}),
            source_measurements=dict(image_id='current_core',texts=[{'text':'40','confidence':.99}]),
            next_step='Inspect this source region; no automatic correction')
        value['events']=[
            dict(tool='preview_scene',summary='old attempt '*100,
                receipt=dict(ok=False,error='old error '*100,validation_feedback=old_feedback)),
            dict(tool='preview_scene',summary='current attempt '*100,
                receipt=dict(ok=False,error='current error '*100,validation_feedback=current_feedback)),
        ]
        before=copy.deepcopy(value)
        context=working_context(value,'material-v1')
        old,current=context['recent_tool_results']
        self.assertNotIn('validation_feedback',old['receipt'])
        self.assertIn('historical attempt',old['receipt']['validation_feedback_scope'])
        self.assertIn('not an instruction for current code',old['receipt']['error_scope'])
        self.assertLessEqual(len(old['receipt']['error']),280)
        self.assertLessEqual(len(old['summary']),300)
        self.assertEqual(current['receipt']['validation_feedback'],current_feedback)
        self.assertEqual(current['receipt']['error'],value['events'][-1]['receipt']['error'])
        self.assertEqual(current['summary'],value['events'][-1]['summary'])
        self.assertNotIn('validation_feedback_scope',current['receipt'])
        self.assertNotIn('error_scope',current['receipt'])
        self.assertEqual(value,before)
        current['receipt']['validation_feedback']['conflict']['submitted']['text']='prompt-only mutation'
        self.assertEqual(value,before)

    def test_old_failed_preview_feedback_is_not_carried_as_latest_receipt_after_new_tool(self):
        value=state()
        old=dict(ok=False,error='OLD_SOURCE_CONFLICT',validation_feedback=dict(
            conflict={'text':'OLD_WRONG_DECLARATION'},source_measurements={'image_id':'old_source'}))
        value['events']=[dict(tool='preview_scene',summary='failed declaration',receipt=old),
                         dict(tool='inspect_image',summary='inspect source',
                             receipt=dict(ok=True,asset_id='new_crop',requested_asset_id='core'))]
        value['observations']['preview_scene']=copy.deepcopy(old)
        before=copy.deepcopy(value)
        context=working_context(value,'material-v1')
        self.assertNotIn('validation_feedback',context['recent_tool_results'][0]['receipt'])
        self.assertEqual(context['recent_tool_results'][1]['receipt']['asset_id'],'new_crop')
        self.assertNotIn('validation_feedback',context['observations']['preview_scene'])
        self.assertIn('historical attempt',context['observations']['preview_scene']['validation_feedback_scope'])
        self.assertEqual(value,before)

    def test_current_bound_observation_keeps_all_validation_evidence_after_inspection(self):
        value=state()
        receipt=dict(ok=False,preview_id='preview_generic',error='CURRENT_ERROR '*100,
            validation_feedback=dict(conflict={'text':'0','source_box':[.3,.4,.4,.5]},
                source_measurements={'image_id':'current_source','warnings':['ambiguous candidates']}))
        value['observations']['preview_scene']=receipt
        value['events']=[dict(tool='preview_scene',summary='current failed preview',receipt=receipt),
                         dict(tool='inspect_image',summary='check local crop',receipt={'ok':True})]
        before=copy.deepcopy(value)
        context=working_context(value,'material-v1')
        result=context['observations']['preview_scene']
        self.assertEqual(result['validation_feedback'],receipt['validation_feedback'])
        self.assertEqual(result['error'],receipt['error'])
        self.assertNotIn('validation_feedback_scope',result)
        result['validation_feedback']['source_measurements']['warnings'].append('prompt-only note')
        self.assertEqual(value,before)

    def test_observation_with_same_preview_id_cannot_keep_current_evidence_after_code_or_material_change(self):
        for code,fingerprint in [('NEW_CODE','material-v1'),('CODE','material-v2')]:
            with self.subTest(code=code,fingerprint=fingerprint):
                value=state()
                value['latest_code']['code']=code
                value['observations']['preview_scene']=dict(ok=False,preview_id='preview_generic',
                    error='SOURCE_VERSION_ERROR '*100,validation_feedback={'source_image_id':'old_source'})
                before=copy.deepcopy(value)
                context=working_context(value,fingerprint)
                result=context['observations']['preview_scene']
                self.assertIsNone(context['current_preview'])
                self.assertNotIn('validation_feedback',result)
                self.assertIn('historical',result['validation_feedback_scope'])
                self.assertLessEqual(len(result['error']),280)
                self.assertEqual(value,before)

    def test_different_preview_observation_cannot_shadow_the_current_candidate(self):
        value=state()
        value['observations']['preview_scene']=dict(ok=False,preview_id='previous_preview',
            error='OLD_CONFLICT',validation_feedback={'source_image_id':'old_source'})
        context=working_context(value,'material-v1')
        self.assertEqual(context['current_preview']['preview_id'],'preview_generic')
        self.assertNotIn('validation_feedback',context['observations']['preview_scene'])
        self.assertIn('historical',context['observations']['preview_scene']['validation_feedback_scope'])

    def test_repeated_review_keeps_all_old_findings_to_recheck(self):
        value = state()
        old = dict(preview_id='preview_generic', code_sha256=candidate()['code_sha256'],
                   material_fingerprint='material-v1', passed=False, issues=['old issue'])
        new = {**old, 'issues':['new issue']}
        value['review_history'] = [old, new]
        value['delivery_reviews']['preview_generic'] = new
        context = working_context(value, 'material-v1')['review_context']
        self.assertEqual([row['observation'] for row in context['previous_findings_to_recheck']], ['old issue','new issue'])
        self.assertEqual(context['current_review'], new)
        self.assertTrue(all(not row['current_fact'] for row in context['previous_findings_to_recheck']))

    def test_unbound_legacy_review_cannot_pass_current_candidate(self):
        value = state()
        value['observations']['delivery_review'] = dict(passed=False, issues=['legacy finding'])
        value['delivery_reviews']['different'] = dict(passed=True, issues=[], preview_id='different')
        context = working_context(value, 'material-v1')['review_context']
        self.assertIsNone(context['current_review'])
        self.assertEqual(context['previous_findings_to_recheck'][0]['observation'], 'legacy finding')

    def test_compact_reuse_result_preserves_nested_material_failures(self):
        value = state()
        value['observations']['reuse_materials'] = dict(ok=True, materials=dict(
            background_selection_error='incompatible recipe', rejected_receipts=['untrusted receipt'],
            materials=[dict(name='subject', warning='check edge')]))
        result = working_context(value,'material-v1')['observations']['reuse_materials']
        self.assertNotIn('materials', result)
        self.assertEqual(len(result['material_notices']), 3)
        self.assertIn('incompatible recipe', str(result['material_notices']))

    def test_nested_zoom_keeps_candidate_role_and_parent_coordinate_lineage(self):
        value = state()
        for image, parent, region in [('crop_a','preview_generic_detail_0',[.5,0,1,1]),
                                      ('crop_b','crop_a',[.1,.2,.9,.8])]:
            value['events'].append(dict(tool='inspect_image',receipt=dict(asset_id=image,
                requested_asset_id=parent,region=region)))
        role = image_roles({'crop_b':'path'}, {'core':'source'}, value)['crop_b']
        self.assertEqual(role['preview_id'],'preview_generic')
        self.assertEqual(role['root_image_id'],'preview_generic_detail_0')
        self.assertEqual(len(role['lineage']),2)
        self.assertIn('only one side',role['comparison_order'])

    def test_changed_code_and_material_cannot_inherit_current_verdict(self):
        value = state()
        value['latest_code']['code']='NEW_CODE_FAILED_TO_RENDER'
        context = working_context(value,'material-v1')
        self.assertIsNone(context['current_preview'])
        self.assertFalse(context['previews'][0]['current'])
        value['latest_code']['code']='CODE'
        self.assertIsNone(working_context(value,'material-v2')['current_preview'])

    def test_previous_attempt_self_verdict_does_not_contaminate_new_material_reuse(self):
        value = state()
        value['observations']['reuse_materials'] = dict(ok=True, previous_outcome='OLD_CLAIM_OF_MISSING_PANELS',
            materials=dict(background_selection_error='real_tool_failure'))
        context = working_context(value,'material-v1')
        self.assertNotIn('OLD_CLAIM', str(context))
        self.assertIn('real_tool_failure', str(context))
        self.assertIn('OLD_CLAIM', str(value['observations']))

    def test_recovered_legacy_format_error_is_not_current_feedback(self):
        value = state()
        error = dict(ok=False, error='OLD_JSON_CONTRACT_FAILURE', previous_response='invalid response')
        value['observations']['response_format'] = error
        value['events'] = [dict(tool='response_format', summary='invalid call', receipt=error),
                           dict(tool='set_inventory', summary='valid corrected call', receipt={'ok': True})]
        context = working_context(value, 'material-v1')
        self.assertNotIn('OLD_JSON_CONTRACT_FAILURE', str(context))
        self.assertIn('OLD_JSON_CONTRACT_FAILURE', str(value['events']))
        self.assertEqual(context['recent_tool_results'][0]['tool'], 'set_inventory')

    def test_pending_format_error_is_sent_once_not_in_recent_history(self):
        value = state()
        error = dict(ok=False, error='CURRENT_FORMAT_ERROR')
        value['observations']['response_format'] = error
        value['events'] = [dict(tool='response_format', summary='invalid call', receipt=error)]
        context = working_context(value, 'material-v1')
        self.assertEqual(context['observations']['response_format']['error'], 'CURRENT_FORMAT_ERROR')
        self.assertEqual(context['recent_tool_results'], [])

    def test_valid_call_with_new_tool_error_resolves_old_format_error(self):
        value = state()
        old = dict(ok=False, error='OLD_JSON_ERROR')
        new = dict(ok=False, error='SOURCE_ASSET_NOT_FOUND')
        value['observations'] = dict(response_format=old, prepare_subject=new)
        value['events'] = [dict(tool='response_format', summary='invalid call', receipt=old),
                           dict(tool='prepare_subject', summary='valid call, unavailable asset', receipt=new)]
        context = working_context(value, 'material-v1')
        self.assertNotIn('OLD_JSON_ERROR', str(context))
        self.assertIn('SOURCE_ASSET_NOT_FOUND', str(context))


if __name__ == '__main__':
    unittest.main()
