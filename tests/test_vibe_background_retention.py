"""Selective background retention contracts; offline and project-independent."""
import copy
import unittest

from backend.app import vibe_code_workflow as workflow
from backend.app.vibe_layered import BaseRetainedSubject, Design, _inventory_notes
from backend.app.vibe_layered_inventory import Inventory


def retained(name='透明演示器皿', box=None):
    return dict(name=name, source_box=box or [.1,.2,.4,.8],
                reason='半透明边缘依赖原背景，保留原位以避免不自然的矩形接缝。',
                content_kind='illustration')


def inventory(*, detached=False):
    subjects=[]
    if detached:
        subjects=[dict(name='独立示意图标',source='core',source_crop=[.6,.3,.8,.6],
                       treatment='cutout',embedded_text=[])]
    return dict(background='extract',background_color='#153047',
        background_notes='保留装饰光带与原位半透明物件，不保留文字和文字底板。',
        foreground_summary=dict(has_nontext_subjects=True,subject_count=len(subjects),
            background_retained_count=1,
            description='透明演示器皿留在底图；需要单独动画的图标另行取材。'),
        subjects=subjects,background_retained=[retained()],
        texts=[dict(text='测试标题',source_box=[.55,.08,.9,.2])],decorations=[])


def base_check(identity='base_0', **changes):
    row=dict(base_id=identity,preserved=True,compositing_ok=True,
             evidence='对照原图、实际底图及完整预览，器皿轮廓、色彩与位置保留，未发现额外叠放或遮挡。')
    row.update(changes)
    return row


def review_body(checks=None):
    return dict(passed=True,summary='已逐项核对原图、底图和完整预览',
        issues=[],embedded_text_verified=True,recovered_text_verified=True,
        subject_checks=[],layout_checks=[],background_checks=checks or [])


class BackgroundRetentionInventoryTests(unittest.TestCase):
    def test_only_background_retained_subject_is_not_misclassified_as_text_only(self):
        raw=inventory();before=copy.deepcopy(raw)
        result=Inventory.model_validate(raw)
        self.assertEqual(result.subjects,[])
        self.assertEqual(len(result.background_retained),1)
        self.assertTrue(result.foreground_summary.has_nontext_subjects)
        self.assertIsNone(result.foreground_summary.text_only_reason)
        self.assertEqual(raw,before)

    def test_mixed_subject_counts_stay_separate_and_both_are_accounted_for(self):
        raw=inventory(detached=True)
        result=Inventory.model_validate(raw)
        self.assertEqual(result.foreground_summary.subject_count,1)
        self.assertEqual(result.foreground_summary.background_retained_count,1)
        self.assertEqual(len(result.subjects),1)
        self.assertEqual(len(result.background_retained),1)

    def test_legacy_empty_retention_remains_compatible(self):
        raw=inventory(detached=True)
        raw.pop('background_retained')
        raw['foreground_summary'].pop('background_retained_count')
        result=Inventory.model_validate(raw)
        self.assertEqual(result.background_retained,[])
        self.assertEqual(result.foreground_summary.background_retained_count,0)

    def test_inconsistent_counts_or_text_only_claim_cannot_hide_retained_subject(self):
        bad=[]
        raw=inventory();raw['foreground_summary']['background_retained_count']=0;bad.append(raw)
        raw=inventory();raw['foreground_summary']['subject_count']=1;bad.append(raw)
        raw=inventory();raw['foreground_summary']['has_nontext_subjects']=False;bad.append(raw)
        raw=inventory();raw['foreground_summary']['text_only_reason']='只看文字，不计算底图里的主体';bad.append(raw)
        for raw in bad:
            with self.subTest(summary=raw['foreground_summary']),self.assertRaises(ValueError):
                Inventory.model_validate(raw)

    def test_only_non_evidentiary_illustrations_and_decorative_objects_can_use_model_edited_base(self):
        for kind in ('illustration','decorative_object'):
            raw=retained();raw['content_kind']=kind
            with self.subTest(kind=kind):
                self.assertEqual(BaseRetainedSubject.model_validate(raw).content_kind,kind)
        for kind in ('photo','evidence','pathology','data_chart','person','unknown'):
            raw=retained();raw['content_kind']=kind
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                BaseRetainedSubject.model_validate(raw)

    def test_retained_geometry_and_required_explanation_are_validated(self):
        for box in ([.5,.1,.2,.7],[.1,.4,.8,.4],[-.1,0,.5,.5],[0,0,1.1,1]):
            with self.subTest(box=box),self.assertRaises(ValueError):
                BaseRetainedSubject.model_validate(retained(box=box))
        for field in ('name','reason'):
            raw=retained();raw[field]=' '
            with self.subTest(field=field),self.assertRaises(ValueError):
                BaseRetainedSubject.model_validate(raw)
        raw=retained();raw['source']='arbitrary_reference'
        with self.assertRaises(ValueError):BaseRetainedSubject.model_validate(raw)

    def test_retention_requires_extraction_not_solid_or_unverified_reference_background(self):
        for background in ('solid','core','ref_background'):
            raw=inventory();raw['background']=background
            with self.subTest(background=background),self.assertRaises(ValueError):
                Inventory.model_validate(raw)
            direct=dict(background=background,background_color='#153047',
                        subjects=[],labels=[],background_retained=[retained()])
            with self.subTest(design_background=background),self.assertRaises(ValueError):
                Design.model_validate(direct)

    def test_core_subject_cannot_be_retained_and_extracted_twice_by_name_or_region(self):
        raw=inventory(detached=True)
        raw['subjects'][0]['source_crop']=[.105,.205,.395,.795]
        with self.assertRaises(ValueError):Inventory.model_validate(raw)
        raw=inventory(detached=True)
        raw['subjects'][0]['name']=raw['background_retained'][0]['name']
        with self.assertRaises(ValueError):Inventory.model_validate(raw)

    def test_numeric_overlap_in_reference_coordinates_is_not_core_overlap(self):
        raw=inventory(detached=True)
        raw['subjects'][0].update(source='ref_photo',source_crop=[.1,.2,.4,.8],
                                  role='photo',treatment='preserve')
        result=Inventory.model_validate(raw)
        self.assertEqual(result.subjects[0].source,'ref_photo')

    def test_adapter_and_notes_keep_explicit_base_identity_without_adding_detached_asset(self):
        raw=inventory(detached=True);before=copy.deepcopy(raw)
        result=Inventory.model_validate(raw)
        design=result.to_design({'canvas':[1600,900],'duration':8},{'core':'core.png'})
        self.assertEqual([row.model_dump() for row in design.background_retained],
                         [row.model_dump() for row in result.background_retained])
        self.assertEqual(len(design.subjects),1)
        notes=_inventory_notes(result)
        self.assertEqual([row['id'] for row in notes['background_retained']],['base_0'])
        self.assertEqual(notes['background_retained'][0]['name'],retained()['name'])
        self.assertEqual(raw,before)


class BackgroundRetentionReviewTests(unittest.TestCase):
    def setUp(self):
        self.inventory=Inventory.model_validate(inventory())
        self.measured={'assets':[{'id':'background'}],'texts':[]}

    def check(self, checks, **changes):
        raw=review_body(checks);raw.update(changes)
        return workflow.CodeReview.model_validate(raw)

    def test_each_base_identity_is_reviewed_exactly_once(self):
        review=self.check([base_check()])
        workflow.validate_review_coverage(review,self.measured,self.inventory)
        for checks in ([],[base_check(),base_check()],
                       [base_check('base_1')],[base_check(),base_check('base_1')]):
            with self.subTest(ids=[row['base_id'] for row in checks]),self.assertRaises(ValueError):
                workflow.validate_review_coverage(self.check(checks),self.measured,self.inventory)

    def test_retained_checks_do_not_replace_detached_subject_or_text_checks(self):
        measured={'assets':[{'id':'background'},{'id':'subject_0'}],
                  'texts':[{'id':'headline'}]}
        review=self.check([base_check()])
        with self.assertRaises(ValueError):workflow.validate_review_coverage(review,measured,self.inventory)
        complete=self.check([base_check()],
            subject_checks=[dict(asset_id='subject_0',complete=True,evidence='实际主体在完整画面中轮廓齐全，没有被裁切。')],
            layout_checks=[dict(text_id='headline',faithful=True,evidence='标题文字与原图一致，比例、色彩与留白协调。')])
        workflow.validate_review_coverage(complete,measured,self.inventory)

    def test_no_retained_inventory_requires_no_base_checks_including_legacy_call_signature(self):
        legacy=workflow.CodeReview.model_validate({key:value for key,value in review_body().items()
                                                   if key!='background_checks'})
        workflow.validate_review_coverage(legacy,self.measured)
        with self.assertRaises(ValueError):
            workflow.validate_review_coverage(self.check([base_check()]),self.measured)

    def test_preservation_and_compositing_are_independent_and_cannot_be_false_when_passed(self):
        for field in ('preserved','compositing_ok'):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):self.check([base_check(**{field:False})])
                failed=self.check([base_check(**{field:False})],passed=False,
                                  issues=['base_0 在实际对照中存在可见问题，需要按该问题处理。'])
                workflow.validate_review_coverage(failed,self.measured,self.inventory)
                self.assertFalse(getattr(failed.background_checks[0],field))
                other='compositing_ok' if field=='preserved' else 'preserved'
                self.assertTrue(getattr(failed.background_checks[0],other))

    def test_base_review_booleans_are_strict_and_visual_evidence_is_required(self):
        for field in ('preserved','compositing_ok'):
            for value in ('true',1,None):
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                    self.check([base_check(**{field:value})])
        for value in ('','好'):
            with self.subTest(evidence=value),self.assertRaises(ValueError):
                self.check([base_check(evidence=value)])


if __name__=='__main__':unittest.main()
