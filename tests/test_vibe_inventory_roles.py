"""Generic inventory roles: semantic declarations, never name heuristics.

Offline only; no project files, model calls, or renderer. A plain text panel
can be reconstructed as code or retained as a complete material; real photos
and evidence must keep their original pixels regardless of the chosen name.
"""
import copy
import unittest

from backend.app.vibe_layered_inventory import Inventory, InventorySubject


def subject(**changes):
    return dict(name='示意物件', source='core', source_crop=[.1, .2, .4, .7],
                treatment='preserve', embedded_text=[], **changes)


class InventoryRoleTests(unittest.TestCase):
    def test_legacy_subject_without_role_stays_an_object(self):
        raw=subject()
        before=copy.deepcopy(raw)
        result=InventorySubject.model_validate(raw)
        self.assertEqual(result.role,'object')
        self.assertEqual(raw,before)

    def test_plain_text_panel_can_use_a_complete_material_without_forcing_code_rebuild(self):
        for treatment in ('preserve','cutout'):
            raw=subject(role='text_panel')
            raw.update(treatment=treatment,embedded_text=['最终评价'])
            before=copy.deepcopy(raw)
            with self.subTest(treatment=treatment):
                result=InventorySubject.model_validate(raw)
                self.assertEqual(result.role,'text_panel')
                self.assertEqual(result.treatment,treatment)
                self.assertEqual(result.embedded_text,['最终评价'])
            self.assertEqual(raw,before)

    def test_photos_and_evidence_must_keep_source_pixels(self):
        for role in ('photo','evidence'):
            raw=subject(role=role)
            with self.subTest(role=role):
                self.assertEqual(InventorySubject.model_validate(raw).treatment,'preserve')
                raw['treatment']='cutout'
                with self.assertRaisesRegex(ValueError,'preserve'):
                    InventorySubject.model_validate(raw)

    def test_names_are_not_used_to_guess_roles_or_reject_real_images(self):
        cases=(('photo','带文字边框的医院照片'),
               ('evidence','原始森林图与文本框'),
               ('object','带发光边框的三维计时器'),
               ('object','Text-panel shaped physical sign'))
        for role,name in cases:
            raw=subject(role=role);raw['name']=name
            raw['embedded_text']=['图内原始文字']
            with self.subTest(role=role,name=name):
                result=InventorySubject.model_validate(raw)
                self.assertEqual((result.role,result.name),(role,name))
                self.assertEqual(result.embedded_text,['图内原始文字'])

    def test_objects_can_still_use_either_preparation_method(self):
        for treatment in ('cutout','preserve'):
            raw=subject(role='object');raw['treatment']=treatment
            with self.subTest(treatment=treatment):
                self.assertEqual(InventorySubject.model_validate(raw).treatment,treatment)

    def test_plain_panel_text_and_frame_survive_as_separate_code_inventory(self):
        raw=dict(background='extract',background_color='#102030',
            foreground_summary=dict(has_nontext_subjects=False,subject_count=0,
                description='仅有文字与简单圆角底板',
                text_only_reason='没有独立物件、人物、照片或证据图'),
            subjects=[],texts=[dict(text='最终评价',source_box=[.4,.7,.6,.8])],
            decorations=[dict(kind='frame',description='完整闭合的圆角发光框，需四边及底边完整',
                              source_box=[.37,.67,.63,.84])])
        before=copy.deepcopy(raw)
        inventory=Inventory.model_validate(raw)
        design=inventory.to_design({'canvas':[1600,900],'duration':8},{'core':'core.png'})
        self.assertEqual(design.subjects,[])
        self.assertEqual([label.text for label in design.labels],['最终评价'])
        self.assertEqual(inventory.decorations[0].kind,'frame')
        self.assertEqual(inventory.decorations[0].source_box,(.37,.67,.63,.84))
        self.assertEqual(raw,before)

    def test_schema_advertises_semantic_role_without_changing_legacy_default(self):
        role=InventorySubject.model_json_schema()['properties']['role']
        self.assertEqual(set(role['enum']),{'object','person','photo','evidence','text_panel'})
        self.assertEqual(role['default'],'object')
        raw=subject(role='invented_kind')
        with self.assertRaises(ValueError):InventorySubject.model_validate(raw)


if __name__=='__main__':unittest.main()
