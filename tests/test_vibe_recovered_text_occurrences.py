"""Text recovery is source-occurrence based, never global substring based."""
import copy
import json
import unittest

from pydantic import ValidationError

from backend.app.vibe_layered_assembly import Assembly, RecoveredTextConflict, validate_content


def label(text, box=(.05,.05,.45,.15)):
    return dict(text=text, box=list(box), color='#FFFFFF', font_size=24, group='content')


def recovery(text, box=(.1,.7,.15,.75)):
    return dict(text=text, source_box=list(box))


def scene(texts, recovered=(), subjects=()):
    return Assembly.model_validate(dict(background_color='#123456', subjects=list(subjects),
        labels=[label(text) for text in texts], recovered_text=list(recovered)))


class RecoveredTextOccurrenceTests(unittest.TestCase):
    def test_numeric_fragment_is_not_the_same_as_a_decimal_elsewhere(self):
        inventory = {'labels':[label('78.0%')], 'subjects':[]}
        before = copy.deepcopy(inventory)
        self.assertTrue(validate_content(scene(['78.0%','0'],[recovery('0')]), inventory))
        self.assertEqual(inventory,before)

    def test_short_unit_or_percentage_can_occur_at_a_separate_source_position(self):
        for word, full in (('%','82.8%'),('L','26—50×10⁹/L'),('20','120人')):
            with self.subTest(word=word):
                inventory = {'labels':[label(full)],'subjects':[]}
                self.assertTrue(validate_content(scene([full,word],[recovery(word)]), inventory))

    def test_distinct_axis_ticks_count_as_distinct_source_occurrences(self):
        ticks = ['0','20','40','60','80','100','%']
        recovered = []
        for origin in (.05,.55):
            for index, text in enumerate(ticks):
                x = origin+index*.05
                recovered.append(recovery(text,[x,.7,x+.035,.75]))
        original = ['26—50×10⁹/L','≤25×10⁹/L','82.8%','78.0%','52.6%','51.9%']
        inventory = {'labels':[label(t) for t in original], 'subjects':[]}
        self.assertTrue(validate_content(scene(original+ticks+ticks,recovered), inventory))

    def test_same_text_in_inventory_and_a_distinct_known_source_occurrence_is_allowed(self):
        inventory = {'labels':[label('0',[.1,.2,.15,.25])], 'subjects':[]}
        self.assertTrue(validate_content(scene(['0','0'],[recovery('0',[.6,.2,.65,.25])]), inventory))

    def test_inventory_source_box_takes_precedence_over_render_like_box(self):
        inventory = {'labels':[dict(label('0'),source_box=[.6,.7,.65,.75])], 'subjects':[]}
        with self.assertRaisesRegex(ValueError,'同一原文位置'):
            validate_content(scene(['0','0'],[recovery('0',[.6,.7,.65,.75])]), inventory)

    def test_duplicate_whole_or_fragment_at_same_source_location_is_rejected(self):
        box = [.1,.2,.4,.3]
        for original, claim in (('研究结果','研究结果'),('78.0%','0'),('随访24个月','24个月'),('0','0%')):
            with self.subTest(original=original,claim=claim), self.assertRaisesRegex(ValueError,'同一原文位置'):
                validate_content(scene([original,claim],[recovery(claim,box)]),
                    {'labels':[label(original,box)],'subjects':[]})

    def test_same_recovered_occurrence_cannot_be_declared_twice(self):
        for second in ([.1,.7,.15,.75],[.11,.7,.16,.75],[.09,.69,.16,.76]):
            with self.subTest(second=second),self.assertRaisesRegex(ValueError,'重复或片段重叠'):
                scene(['0','0'],[recovery('0'),recovery('0',second)])

    def test_split_recovery_cannot_double_count_a_longer_recovered_occurrence(self):
        with self.assertRaisesRegex(ValueError,'重复或片段重叠'):
            scene(['20','0'],[recovery('20'),recovery('0')])

    def test_shared_border_and_float_noise_are_not_duplicate_glyph_evidence(self):
        for second_left in (.15,.15-1e-12):
            with self.subTest(left=second_left):
                model = scene(['0','0'],[recovery('0'),recovery('0',[second_left,.7,.2,.75])])
                self.assertTrue(validate_content(model,{'labels':[],'subjects':[]}))
        with self.assertRaisesRegex(ValueError,'重复或片段重叠'):
            scene(['0','0'],[recovery('0'),recovery('0',[.15-1e-6,.7,.2,.75])])

    def test_unknown_old_source_position_requires_localization_not_global_dedup(self):
        with self.assertRaisesRegex(ValueError,'旧项没有可核实'):
            validate_content(scene(['0','0'],[recovery('0')]),{'labels':[{'text':'0'}],'subjects':[]})
        # A short substring alone is not an existing occurrence, even when a
        # legacy inventory has no text coordinates.
        self.assertTrue(validate_content(scene(['78.0%','0'],[recovery('0')]),
            {'labels':[{'text':'78.0%'}],'subjects':[]}))

    def test_embedded_subject_crop_is_not_a_precise_text_position(self):
        subject = dict(asset_id='subject_0',box=[.1,.1,.8,.8],group='subject',embedded_text=['医院'])
        model = scene(['医院'],[recovery('医院')],[subject])
        inventory = {'labels':[], 'subjects':[dict(source='core',crop=[0,0,1,1],box=[0,0,1,1],embedded_text=['医院'])]}
        with self.assertRaisesRegex(ValueError,'主体大裁区不等于文字位置'):
            validate_content(model,inventory)

    def test_recovery_cannot_hide_missing_visible_text_or_invent_extra_display(self):
        inventory={'labels':[label('78.0%')],'subjects':[]}
        for texts, recovered in ((['78.0%'],[recovery('0')]),(['78.0%','0'],[]),
                                  (['78.0%','0','0'],[recovery('0')]),(['0'],[recovery('0')])):
            with self.subTest(texts=texts,recovered=recovered),self.assertRaises(ValueError):
                validate_content(scene(texts,recovered),inventory)

    def test_actual_numerical_rewrite_still_fails(self):
        inventory={'labels':[label('78.0%')],'subjects':[]}
        for actual in ('780%','87.0%','78%','78.0‰'):
            with self.subTest(actual=actual),self.assertRaises(ValueError):
                validate_content(scene([actual,'0'],[recovery('0')]),inventory)

    def test_distinct_recoveries_are_auditable_not_implicitly_verified(self):
        model=scene(['0','0'],[recovery('0'),recovery('0',[.6,.7,.65,.75])])
        self.assertTrue(validate_content(model,{'labels':[],'subjects':[]}))
        self.assertEqual([row['source_box'] for row in model.model_dump()['recovered_text']],
                         [(.1,.7,.15,.75),(.6,.7,.65,.75)])
        self.assertTrue(all('verified' not in row for row in model.model_dump()['recovered_text']))

    def test_conflict_identifies_the_two_substring_claims_not_all_equal_axis_ticks(self):
        claims = [recovery('0',[.1,.7,.12,.74]),
                  recovery('20',[.6,.7,.64,.74]),
                  recovery('0',[.62,.7,.64,.74])]
        before = copy.deepcopy(claims)
        with self.assertRaises(ValidationError) as caught:
            scene(['0','20','0'],claims)
        error = caught.exception.errors()[0]['ctx']['error']
        self.assertIsInstance(error,RecoveredTextConflict)
        conflict = error.conflict
        self.assertEqual(conflict['kind'],'source_occurrence_overlap')
        self.assertEqual(conflict['text_relation'],'substring')
        self.assertEqual(conflict['submitted'],dict(path='recovered_text[2]',text='0',source_box=[.62,.7,.64,.74]))
        self.assertEqual(conflict['existing'],dict(path='recovered_text[1]',text='20',source_box=[.6,.7,.64,.74]))
        self.assertEqual(conflict['coordinate_space'],'core_normalized_ltrb')
        self.assertEqual(conflict['overlap']['source_box'],[.62,.7,.64,.74])
        self.assertAlmostEqual(conflict['overlap']['submitted_fraction'],1)
        self.assertAlmostEqual(conflict['overlap']['existing_fraction'],.5)
        self.assertIn('不表示不同坐标轴',str(error))
        self.assertIn('不要复制邻近文字框',str(error))
        self.assertEqual(claims,before)
        self.assertEqual(json.loads(json.dumps(conflict)),conflict)

    def test_exact_text_conflict_preserves_original_inventory_text_and_path(self):
        box=[.1,.2,.4,.3]
        inventory={'labels':[label(' 20 ',box)],'subjects':[]}
        with self.assertRaises(RecoveredTextConflict) as caught:
            validate_content(scene([' 20 ','20'],[recovery('20',box)]),inventory)
        conflict=caught.exception.conflict
        self.assertEqual(conflict['text_relation'],'same_text')
        self.assertEqual(conflict['existing'],dict(path='inventory.labels[0]',text=' 20 ',source_box=box))
        self.assertEqual(conflict['submitted']['text'],'20')

    def test_unlocalized_conflict_is_explicit_and_never_invents_a_subject_text_box(self):
        inventory={'labels':[], 'subjects':[dict(source='core',crop=[0,0,1,1],box=[0,0,1,1],embedded_text=['医院'])]}
        with self.assertRaises(RecoveredTextConflict) as caught:
            validate_content(scene(['医院','医院'],[recovery('医院')]),inventory)
        conflict=caught.exception.conflict
        self.assertEqual(conflict['kind'],'unlocalized_same_text')
        self.assertEqual(conflict['text_relation'],'same_text')
        self.assertEqual(conflict['existing'],dict(path='inventory.subjects[0].embedded_text[0]',text='医院',source_box=None))
        self.assertNotIn('overlap',conflict)
        self.assertEqual(json.loads(json.dumps(conflict)),conflict)


if __name__ == '__main__':
    unittest.main()
