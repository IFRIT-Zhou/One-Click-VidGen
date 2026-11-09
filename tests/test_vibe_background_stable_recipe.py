"""Stable base editing keeps complex objects; only offline fixtures are used."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from backend.app import vibe_cloud as api, vibe_layered as layered
import test_vibe_layered_cache as cache_tests


class StableBackgroundRecipeTests(unittest.TestCase):
    setUp=cache_tests.LayeredBackgroundCacheTests.setUp
    job=cache_tests.LayeredBackgroundCacheTests.job
    generated_file=cache_tests.LayeredBackgroundCacheTests.generated_file
    generate=cache_tests.LayeredBackgroundCacheTests.generate
    resume=cache_tests.LayeredBackgroundCacheTests.resume
    cache=cache_tests.LayeredBackgroundCacheTests.cache
    ready=cache_tests.LayeredBackgroundCacheTests.ready

    def stable(self,design=None,notes=None):
        return layered._background_recipe(design or self.design,notes,advanced_subject_separation=False)

    def test_default_and_explicit_advanced_keep_legacy_recipe_contract(self):
        default=layered._background_recipe(self.design)
        advanced=layered._background_recipe(self.design,advanced_subject_separation=True)
        self.assertEqual(default,advanced)
        self.assertEqual(default['version'],'ocv-background-removal-v2')
        self.assertNotIn('advanced_subject_separation',default)
        body={key:default[key] for key in ('version','prompt','retained')}
        digest=hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.assertEqual(default['sha256'],digest)
        self.assertTrue(layered._valid_background_recipe(default))

    def test_stable_prompt_removes_named_layers_not_unlisted_people_or_complex_objects(self):
        value=self.design.model_dump()
        value['background_retained']=[dict(name='半透明容器与软管',source_box=[.12,.2,.45,.8],
            reason='透明材质与环境融合，不要求独立移动而保持原位',content_kind='illustration')]
        value['subjects']=[dict(source='ref_person',crop=[.1,.2,.6,.9],box=[0,0,.4,1],treatment='cutout',at=0)]
        value['labels']=[dict(text='临床观察',box=[.5,.1,.8,.2],color='#FFFFFF',font_size=32,at=0)]
        design=layered.Design.model_validate(value)
        notes={'subjects':[dict(id='subject_0',name='讲解员',role='person')],
               'decorations':[dict(kind='line',description='从标题引向示意区的标注线',source_box=[.5,.2,.8,.3])]}
        before=design.model_dump()
        recipe=self.stable(design,notes)
        prompt=recipe['prompt']
        for expected in ('原图编辑','不能用重新生成背景替代原图编辑','文本框/卡片底板','标注引线',
                         '清单外人物不得擅自移除','器官、器械、输液袋','不能误删血管、导管',
                         '透明/半透明透射','分子/网络图案','讲解员','半透明容器与软管'):
            self.assertIn(expected,prompt)
        self.assertIn('"role":"person"',prompt)
        self.assertIn('crop不是海报坐标',prompt)
        self.assertNotIn('"core_region":[0.1,0.2,0.6,0.9]',prompt)
        self.assertEqual(design.model_dump(),before)

    def test_real_photos_and_evidence_have_original_pixel_replacement_instructions(self):
        value=self.design.model_dump()
        value['subjects']=[dict(source='ref_photo',crop=[0,0,1,1],box=[0,0,.4,1],treatment='preserve',at=0),
                           dict(source='ref_data',crop=[0,0,1,1],box=[.4,0,1,1],treatment='preserve',at=0,
                                embedded_text=['原始测量值'])]
        notes={'subjects':[dict(id='subject_0',name='机构照片',role='photo'),
                           dict(id='subject_1',name='原始数据图',role='evidence')]}
        recipe=self.stable(layered.Design.model_validate(value),notes)
        self.assertIn('由后续原图层保真还原',recipe['prompt'])
        self.assertIn('不生成重画、不改变证据内容或数据',recipe['prompt'])
        self.assertIn('"role":"evidence"',recipe['prompt'])
        self.assertIn('"embedded_text":["原始测量值"]',recipe['prompt'])

    def test_strategy_prompt_and_decoration_changes_are_hash_bound(self):
        old=layered._background_recipe(self.design)
        stable=self.stable()
        self.assertNotEqual(old['sha256'],stable['sha256'])
        self.assertIs(stable['advanced_subject_separation'],False)
        self.assertTrue(layered._valid_background_recipe(stable))
        self.assertFalse(layered._valid_background_recipe({**old,'advanced_subject_separation':False}))
        self.assertFalse(layered._valid_background_recipe({**stable,'version':[]}))
        changed=self.stable(notes={'decorations':[dict(kind='frame',description='标题的细边框',source_box=[.1,.1,.8,.3])]})
        self.assertNotEqual(stable['sha256'],changed['sha256'])
        for field,value in [('advanced_subject_separation',True),('prompt','不同编辑目标'),('version',old['version'])]:
            with self.subTest(field=field):
                tampered=copy.deepcopy(stable)
                tampered[field]=value
                self.assertFalse(layered._valid_background_recipe(tampered))
        with self.assertRaisesRegex(ValueError,'布尔值'):
            layered._background_recipe(self.design,advanced_subject_separation='false')

    def test_stable_cache_rejects_previous_subject_erased_base_without_generation(self):
        self.cache()
        result=layered._cached_background(self.project,api._read_job(self.project,self.current),
            self.sources,self.route,self.event,recipe=self.stable())
        self.assertIsNone(result)
        self.generated.assert_not_called()
        self.resumed.assert_not_called()

    def test_stable_cache_reuses_only_exact_stable_recipe(self):
        identity,source=self.cache()
        recipe=self.stable()
        layered._save_background_ready(self.project,identity,source,self.sources,self.route,
            image_task_id='c'*32,recipe=recipe)
        current=api._read_job(self.project,self.current)
        match=layered._cached_background(self.project,current,self.sources,self.route,self.event,recipe=recipe)
        self.assertEqual(match[0],source)
        self.assertEqual(match[2],identity)
        old=layered._background_recipe(self.design)
        self.assertIsNone(layered._cached_background(self.project,current,self.sources,self.route,self.event,recipe=old))
        self.generated.assert_not_called()
        self.resumed.assert_not_called()

    def test_resume_uses_saved_stable_recipe_without_reinterpreting_default_policy(self):
        recipe=self.stable()
        request=dict(recipe=recipe,image_task_id='c'*32,image_route_key=self.route['key'],
                     core_sha256=api.previews._stream_hash(self.core,48*1024**2))
        api._write(self.mats.parent/'background_request.json',request)
        api._update(api._job(self.project,self.current),usage={'image_requests':1,'image_task_ids':['c'*32]})
        with patch.object(layered,'_background_recipe',side_effect=AssertionError('must use saved recipe')):
            layered._background(self.project,self.current,self.design,self.context,self.sources,
                self.mats,self.config,self.event,True)
        self.generated.assert_not_called()
        self.resumed.assert_called_once()
        self.assertEqual(self.ready()['recipe'],recipe)


if __name__=='__main__':
    unittest.main()
