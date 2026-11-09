"""Offline per-request subject identity contracts, independent of any project.

No model calls, file reads, invented material verdicts, or pixel processing.
"""
import copy
import json
from types import SimpleNamespace
import unittest

from backend.app import vibe_code_material_review as review


def fixture(count):
    rows=[]
    bindings=[]
    assets={'background':'bound-background.png'}
    for index in range(count):
        identity=f'subject_{index}'
        source='core' if index % 2 == 0 else 'ref_photo'
        crop=[.05, .1, .9, .85]
        rows.append(SimpleNamespace(source=source,crop=tuple(crop),embedded_text=[]))
        bindings.append(dict(asset_id=identity,source_id=source,
                             source_image_id=source,current_source_crop=crop,
                             treatment='cutout',source_size=[1600,900]))
        assets[identity]=f'bound-{identity}.png'
        if index % 2 == 0:
            assets[identity+'_original']=f'bound-{identity}-original.png'
    return SimpleNamespace(subjects=rows),assets,bindings


def reviewed_row(index, original_available):
    return dict(asset_id=f'subject_{index}',
        source_edges={edge:dict(continues_outside=False,
            evidence='对照完整来源与裁框内外像素，该边界之外仅有背景，没有同一主体继续延伸。')
            for edge in ('top','right','bottom','left')},
        source_crop_complete=True,
        source_crop_evidence='已逐边对照完整来源与当前裁片，主体全部可见轮廓均包含在取材范围中。',
        prepared_complete=True,original_complete=True if original_available else None,
        recommended_asset_id=f'subject_{index}',
        evidence='已对照原始来源及实际准备素材，主体全部边缘和附属结构均完整保留。')


def walk(value):
    yield value
    if isinstance(value,dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value,(list,tuple)):
        for child in value:
            yield from walk(child)


class MaterialIdentityRequestTests(unittest.TestCase):
    def test_per_request_schema_requires_exact_known_identities_once(self):
        for count in (0,1,2,8):
            inventory,assets,bindings=fixture(count)
            before=copy.deepcopy(inventory)
            with self.subTest(count=count):
                schema=review.request_schema(inventory)
                subjects=schema['properties']['subjects']
                retakes=schema['properties']['retakes']
                self.assertEqual(subjects['minItems'],count)
                self.assertEqual(subjects['maxItems'],count)
                self.assertEqual(retakes['maxItems'],count)
                expected=[f'subject_{index}' for index in range(count)]
                if count:
                    self.assertEqual(schema['$defs']['SubjectMaterialReview']['properties']['asset_id']['enum'],expected)
                    self.assertEqual(schema['$defs']['SourceRetake']['properties']['asset_id']['enum'],expected)
                    clauses=subjects['allOf']
                    self.assertEqual(len(clauses),count)
                    self.assertEqual([clause['contains']['properties']['asset_id']['const'] for clause in clauses],expected)
                    for clause in clauses:
                        self.assertEqual(clause['minContains'],1)
                        self.assertEqual(clause['maxContains'],1)
                for node in walk(schema):
                    if isinstance(node,dict) and 'enum' in node:
                        self.assertTrue(node['enum'],'JSON Schema must not contain an empty enum')
                self.assertEqual(inventory,before)
                rows=[reviewed_row(i,f'subject_{i}_original' in assets) for i in range(count)]
                checked=review.validate_material_review({'subjects':rows},assets,inventory)
                self.assertEqual([row.asset_id for row in checked.subjects],expected)

    def test_contract_lists_only_bound_variants_without_fabricating_observations(self):
        for count in (0,1,2,8):
            inventory,assets,bindings=fixture(count)
            assets.update({'subject_99':'unbound.png','subject_99_original':'unbound-original.png'})
            before=copy.deepcopy((inventory,assets,bindings))
            with self.subTest(count=count):
                contract=review.request_contract(inventory,assets,bindings)
                expected=[f'subject_{index}' for index in range(count)]
                self.assertEqual(contract['required_subject_ids'],expected)
                self.assertEqual(contract['subject_count'],count)
                self.assertEqual(contract['response_mode'],'complete_report')
                self.assertEqual(len(contract['subject_checklist']),count)
                for index,row in enumerate(contract['subject_checklist']):
                    identity=expected[index]
                    self.assertEqual(row['asset_id'],identity)
                    self.assertEqual(row['available_variants'],
                        [key for key in (identity,identity+'_original') if key in assets])
                    self.assertEqual(row['source_id'],bindings[index]['source_id'])
                    self.assertEqual(row['current_source_crop'],bindings[index]['current_source_crop'])
                self.assertIs(contract['retake_rules']['allow_same_pixel_crop'],False)
                self.assertIs(contract['retake_rules']['supports_mask_edit'],False)
                for node in walk(contract):
                    if isinstance(node,dict):
                        for verdict in ('source_crop_complete','prepared_complete','original_complete',
                                        'recommended_asset_id','continues_outside','evidence'):
                            self.assertNotIn(verdict,node,'Request contract may bind IDs, never invent visual judgments')
                serialized=json.dumps(contract,ensure_ascii=False)
                self.assertNotIn('subject_99',serialized)
                self.assertNotIn('unbound.png',serialized)
                self.assertEqual((inventory,assets,bindings),before)

    def test_returned_schema_and_contract_are_detached_from_the_inputs_and_future_calls(self):
        inventory,assets,bindings=fixture(2)
        schema=review.request_schema(inventory)
        schema['$defs']['SubjectMaterialReview']['properties']['asset_id']['enum'].append('subject_7')
        self.assertEqual(review.request_schema(inventory)['$defs']['SubjectMaterialReview']['properties']['asset_id']['enum'],
                         ['subject_0','subject_1'])
        contract=review.request_contract(inventory,assets,bindings)
        contract['subject_checklist'][0]['current_source_crop'][0]=.77
        contract['subject_checklist'][0]['available_variants'].append('subject_7')
        fresh=review.request_contract(inventory,assets,bindings)
        self.assertEqual(bindings[0]['current_source_crop'][0],.05)
        self.assertEqual(fresh['subject_checklist'][0]['current_source_crop'][0],.05)
        self.assertNotIn('subject_7',fresh['subject_checklist'][0]['available_variants'])

    def test_final_validator_still_rejects_missing_duplicate_and_extra_subjects(self):
        # jsonschema is not required in the portable runtime: the advertised
        # dynamic schema above is checked structurally, and the actual server
        # validator remains the final authority for all three identity cases.
        inventory,assets,_=fixture(2)
        rows=[reviewed_row(0,True),reviewed_row(1,False)]
        candidates=[rows[:1],[rows[0],copy.deepcopy(rows[0])],
                    rows+[reviewed_row(2,False)],[rows[0],reviewed_row(2,False)]]
        for candidate in candidates:
            with self.subTest(ids=[row['asset_id'] for row in candidate]),self.assertRaises(ValueError):
                review.validate_material_review({'subjects':candidate},assets,inventory)

    def test_unbound_original_variant_cannot_be_assumed_complete(self):
        inventory,assets,bindings=fixture(2)
        contract=review.request_contract(inventory,assets,bindings)
        self.assertEqual(contract['subject_checklist'][1]['available_variants'],['subject_1'])
        with self.assertRaises(ValueError):
            review.validate_material_review({'subjects':[reviewed_row(0,True),reviewed_row(1,True)]},assets,inventory)

    def test_request_subject_capacity_is_bounded(self):
        inventory,assets,bindings=fixture(9)
        with self.assertRaises(ValueError):review.request_schema(inventory)
        with self.assertRaises(ValueError):review.request_contract(inventory,assets,bindings)


if __name__=='__main__':unittest.main()
