"""Deletion-first base edits are explicit, reusable only by exact recipe.

All images and requests here are local fixtures; no providers are contacted.
"""
import copy
import json
import unittest
from unittest.mock import patch

from backend.app import vibe_cloud as api, vibe_layered as layered
import test_vibe_layered_cache as cache_tests


class BackgroundRecipeTests(unittest.TestCase):
    setUp=cache_tests.LayeredBackgroundCacheTests.setUp
    job=cache_tests.LayeredBackgroundCacheTests.job
    generated_file=cache_tests.LayeredBackgroundCacheTests.generated_file
    generate=cache_tests.LayeredBackgroundCacheTests.generate
    resume=cache_tests.LayeredBackgroundCacheTests.resume
    cache=cache_tests.LayeredBackgroundCacheTests.cache
    background=cache_tests.LayeredBackgroundCacheTests.background
    ready=cache_tests.LayeredBackgroundCacheTests.ready

    def retained_design(self):
        value=self.design.model_dump()
        value['background_retained']=[{
            'name':'半透明仪器模型及其柔和倒影',
            'source_box':[.12,.17,.46,.92],
            'reason':'透射与反光依赖原背景，不需要独立移动，留在底图可避免矩形接缝',
            'content_kind':'illustration',
        }]
        return layered.Design.model_validate(value)

    def test_prompt_explicitly_removes_text_and_plates_but_preserves_named_base_material(self):
        design=self.retained_design()
        before=design.model_dump()
        recipe=layered._background_recipe(design)
        self.assertTrue(layered._valid_background_recipe(recipe))
        self.assertIn('执行定点删除',recipe['prompt'])
        self.assertIn('不要重新生成',recipe['prompt'])
        self.assertIn('文本框/卡片底板',recipe['prompt'])
        self.assertIn('半透明仪器模型',recipe['prompt'])
        self.assertIn('透明/半透明透射',recipe['prompt'])
        self.assertIn('不要复制第二份',recipe['prompt'])
        self.assertIn('[0.12,0.17,0.46,0.92]',recipe['prompt'])
        self.assertEqual(design.model_dump(),before)

    def test_recipe_binds_prompt_notes_and_retained_regions_not_project_specific_names(self):
        design=self.retained_design()
        first=layered._background_recipe(design)
        self.assertEqual(first,layered._background_recipe(design))
        notes=design.model_copy(update={'background_notes':'保留原图的铜色纹理与斜向光带'})
        self.assertNotEqual(first['sha256'],layered._background_recipe(notes)['sha256'])
        moved=design.model_dump()
        moved['background_retained'][0]['source_box']=[.11,.17,.46,.92]
        self.assertNotEqual(first['sha256'],layered._background_recipe(layered.Design.model_validate(moved))['sha256'])
        self.assertNotEqual(first['sha256'],layered._background_recipe(self.design)['sha256'])

    def test_reference_crops_are_not_advertised_as_core_erase_coordinates(self):
        value=self.design.model_dump()
        value['subjects']=[dict(source='ref_portrait',crop=[.1,.2,.7,.8],box=[.1,.2,.7,.8],
                                treatment='cutout',at=0)]
        recipe=layered._background_recipe(layered.Design.model_validate(value))
        self.assertIn('crop不是海报坐标',recipe['prompt'])
        self.assertNotIn('"core_region":[0.1,0.2,0.7,0.8]',recipe['prompt'])

    def test_reference_subject_names_make_removal_list_specific_and_part_of_recipe(self):
        value=self.design.model_dump()
        value['subjects']=[dict(source='ref_object',crop=[0,0,1,1],box=[0,0,1,1],
                                treatment='cutout',at=0)]
        design=layered.Design.model_validate(value)
        notes={'subjects':[{'id':'subject_0','name':'玻璃台上的金属仪器'}]}
        recipe=layered._background_recipe(design,notes)
        self.assertIn('玻璃台上的金属仪器',recipe['prompt'])
        self.assertNotEqual(recipe['sha256'],layered._background_recipe(design)['sha256'])

    def test_new_request_records_recipe_before_submission_and_ready_binds_same_hash(self):
        self.design=self.retained_design()
        expected=layered._background_recipe(self.design)
        original_generate=self.generated.side_effect
        def inspect_request(*args,**kwargs):
            receipt=api.previews._read_json(self.mats.parent/'background_request.json',128*1024)
            self.assertEqual(receipt['recipe'],expected)
            self.assertIsNone(receipt['image_task_id'])
            self.assertEqual(args[1],expected['prompt'])
            return original_generate(*args,**kwargs)
        self.generated.side_effect=inspect_request
        self.background()
        receipt=api.previews._read_json(self.mats.parent/'background_request.json',128*1024)
        self.assertEqual(receipt['image_task_id'],'c'*32)
        self.assertEqual(self.ready()['version'],2)
        self.assertEqual(self.ready()['recipe_sha256'],expected['sha256'])
        self.assertEqual(self.ready()['recipe'],expected)
        self.assertEqual(api._read_job(self.project,self.current)['usage']['image_requests'],1)

    def test_changed_background_notes_do_not_reuse_old_cache_without_authorization(self):
        self.cache()
        self.design=self.design.model_copy(update={'background_notes':'保留右侧既有斜向光带'})
        with self.assertRaisesRegex(ValueError,'删除/保留规则一致'):
            self.background(budget=0)
        self.generated.assert_not_called()
        self.resumed.assert_not_called()

    def test_changed_retention_list_does_not_reuse_subject_erased_base(self):
        self.cache()
        self.design=self.retained_design()
        with self.assertRaisesRegex(ValueError,'请允许一次'):
            self.background(budget=0)
        self.generated.assert_not_called()

    def test_reworded_retention_reason_reuses_identical_edit_and_does_not_change_inventory(self):
        self.design=self.retained_design()
        identity,source=self.cache()
        old_recipe=layered._background_recipe(self.design)
        revised=self.design.model_dump()
        revised['background_retained'][0]['reason']='材质透明且光线与周围环境融合，保持静止在底图中效果更自然'
        self.design=layered.Design.model_validate(revised)
        new_recipe=layered._background_recipe(self.design)
        self.assertEqual(new_recipe,old_recipe)
        self.assertNotIn('reason',new_recipe['retained'][0])
        self.assertNotIn(revised['background_retained'][0]['reason'],new_recipe['prompt'])
        self.assertEqual(self.design.model_dump(),revised)
        self.assertEqual(self.background(budget=0).read_bytes(),source.read_bytes())
        self.assertEqual(self.ready()['origin_task_id'],identity)
        self.generated.assert_not_called()

    def test_matching_retained_base_reuses_without_new_image_request(self):
        self.design=self.retained_design()
        identity,source=self.cache()
        result=self.background(budget=0)
        self.assertEqual(result.read_bytes(),source.read_bytes())
        self.assertEqual(self.ready()['origin_task_id'],identity)
        self.generated.assert_not_called()
        self.assertEqual(api._read_job(self.project,self.current)['usage']['image_requests'],0)

    def test_v1_receipt_cannot_masquerade_as_new_background_recipe(self):
        identity,source=self.cache(manifest=False)
        layered._save_background_ready(self.project,identity,source,self.sources,self.route,image_task_id='c'*32)
        self.assertEqual(self.ready(identity)['version'],1)
        with self.assertRaisesRegex(ValueError,'请允许一次'):
            self.background(budget=0)
        self.generated.assert_not_called()

    def test_tampered_recipe_is_not_reused_even_if_ready_hash_claims_current_recipe(self):
        identity,_=self.cache()
        ready=self.ready(identity)
        ready['recipe']['prompt']='改成别的背景'
        api._write(api._job(self.project,identity).parent/'background_ready.json',ready)
        with self.assertRaisesRegex(ValueError,'请允许一次'):
            self.background(budget=0)
        self.generated.assert_not_called()

    def test_resume_rejects_changed_retained_subjects_before_fetching_or_submitting(self):
        self.background()
        original=copy.deepcopy(self.ready()['recipe'])
        self.generated.reset_mock()
        self.design=self.retained_design()
        self.mats=self.mats.parent/'materials-recovery'
        self.mats.mkdir()
        with self.assertRaisesRegex(ValueError,'保留主体与保存的分层方案不一致'):
            self.background(resume=True)
        self.generated.assert_not_called()
        self.resumed.assert_not_called()
        self.assertEqual(self.ready()['recipe'],original)

    def test_legacy_resume_rejects_new_retention_without_original_receipt(self):
        api._update(api._job(self.project,self.current),usage={'image_requests':1,'image_task_ids':['c'*32]})
        self.design=self.retained_design()
        with self.assertRaisesRegex(ValueError,'旧背景请求没有保留主体记录'):
            self.background(resume=True)
        self.generated.assert_not_called()
        self.resumed.assert_not_called()

    def test_legacy_resume_without_retained_subjects_remains_usable_but_not_upgraded(self):
        api._update(api._job(self.project,self.current),usage={'image_requests':1,'image_task_ids':['c'*32]})
        self.background(resume=True)
        self.generated.assert_not_called()
        self.resumed.assert_called_once()
        self.assertEqual(self.ready()['version'],1)
        self.assertNotIn('recipe',self.ready())
        self.assertNotIn('recipe_sha256',self.ready())

    def test_resume_accepts_reworded_reason_and_keeps_original_image_recipe(self):
        self.design=self.retained_design()
        self.background()
        original=copy.deepcopy(self.ready()['recipe'])
        self.generated.reset_mock()
        revised=self.design.model_dump()
        revised['background_retained'][0]['reason']='保留在底图中可使透明材质继续融入原有光照与环境'
        self.design=layered.Design.model_validate(revised)
        self.mats=self.mats.parent/'materials-recovery'
        self.mats.mkdir()
        self.background(resume=True)
        self.generated.assert_not_called()
        self.resumed.assert_called_once()
        self.assertEqual(self.ready()['recipe'],original)

    def test_resume_does_not_recompute_current_prompt_or_relabel_old_result_after_prompt_upgrade(self):
        self.design=self.retained_design()
        self.background()
        original=copy.deepcopy(self.ready()['recipe'])
        self.generated.reset_mock()
        self.mats=self.mats.parent/'materials-recovery'
        self.mats.mkdir()
        with patch.object(layered,'_background_recipe',side_effect=AssertionError('must use submitted receipt')):
            self.background(resume=True)
        self.generated.assert_not_called()
        self.resumed.assert_called_once()
        self.assertEqual(self.ready()['recipe'],original)

    def test_corrupt_resume_receipt_does_not_resubmit_or_relabel_paid_output(self):
        self.background()
        self.generated.reset_mock()
        receipt=self.mats.parent/'background_request.json'
        value=json.loads(receipt.read_text(encoding='utf-8'))
        value['image_task_id']='d'*32
        api._write(receipt,value)
        self.mats=self.mats.parent/'materials-recovery'
        self.mats.mkdir()
        with self.assertRaisesRegex(ValueError,'原背景请求配方或素材记录不一致'):
            self.background(resume=True)
        self.generated.assert_not_called()
        self.resumed.assert_not_called()


if __name__=='__main__':unittest.main()
