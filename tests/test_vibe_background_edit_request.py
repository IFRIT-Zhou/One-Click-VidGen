"""Targeted edits separate deletion from retention; no providers are called."""
import copy
import unittest

from backend.app import vibe_layered as layered


class BackgroundEditRequestTests(unittest.TestCase):
    def setUp(self):
        self.design=layered.Design(background='extract',background_color='#102030',
            background_notes='保留斜向体积光、背景渐变和远处建筑轮廓',subjects=[],
            labels=[dict(text='观察结果',box=[.1,.1,.8,.2],font_size=32,color='#FFFFFF',at=0)],
            background_retained=[dict(name='透明仪器',source_box=[.7,.4,.9,.8],
                reason='半透明主体与环境反射融合，不需要独立移动',content_kind='illustration')])
        self.request=dict(prompt='完整移除条形图的彩色填充和残留边缘，并按周围背景自然补全',
            remove_regions=[dict(description='待代码重建的水平数据条',region=[.1,.3,.6,.4])],
            preserve_details=['保留画面上方的原有体积光'])

    def recipe(self,request=None,advanced=False):
        return layered._background_recipe(self.design,advanced_subject_separation=advanced,
                                           edit_request=request)

    def test_unspecified_edit_request_preserves_legacy_contract(self):
        for advanced in (True,False):
            with self.subTest(advanced=advanced):
                old=layered._background_recipe(self.design,advanced_subject_separation=advanced)
                self.assertEqual(old,self.recipe(advanced=advanced))
                self.assertTrue(layered._valid_background_recipe(old))
                self.assertIn('其他需要保留的背景细节：'+self.design.background_notes,old['prompt'])

    def test_targeted_deletion_has_own_section_separate_from_preservation(self):
        before=self.design.model_dump()
        request_before=copy.deepcopy(self.request)
        for advanced in (True,False):
            with self.subTest(advanced=advanced):
                recipe=self.recipe(self.request,advanced)
                prompt=recipe['prompt']
                self.assertTrue(layered._valid_background_recipe(recipe))
                self.assertNotIn('其他需要保留的背景细节：',prompt)
                edit=prompt.split('【本次编辑要求】\n',1)[1].split('【原背景保留细节】\n',1)[0]
                self.assertIn(self.request['prompt'],edit)
                self.assertIn('"region":[0.1,0.3,0.6,0.4]',edit)
                retained=prompt.split('【原背景保留细节】\n',1)[1].split('【保护与修补边界】',1)[0]
                self.assertIn(self.design.background_notes,retained)
                self.assertIn(self.request['preserve_details'][0],retained)
                self.assertNotIn(self.request['prompt'],retained)
                self.assertIn('不是整块矩形硬擦蒙版',prompt)
                self.assertIn('先修订内容清单或编辑要求',prompt)
                self.assertIn('不得生成式重画',prompt)
                self.assertIn('渐变、体积光、纹理',prompt)
                self.assertIn('实际结果仍须对照',prompt)
        self.assertEqual(self.design.model_dump(),before)
        self.assertEqual(self.request,request_before)

    def test_every_effective_edit_field_is_hash_bound(self):
        original=self.recipe(self.request)
        self.assertNotEqual(original['sha256'],self.recipe()['sha256'])
        for field in ('prompt','region','description','preserve_details'):
            with self.subTest(field=field):
                changed=copy.deepcopy(self.request)
                if field=='prompt': changed['prompt']='只移除指定图标'
                elif field=='region': changed['remove_regions'][0]['region']=[.1,.3,.7,.4]
                elif field=='description': changed['remove_regions'][0]['description']='标注箭头'
                else: changed['preserve_details']=['保留银色反光']
                recipe=self.recipe(changed)
                self.assertNotEqual(original['sha256'],recipe['sha256'])
                self.assertTrue(layered._valid_background_recipe(recipe))
        tampered={**original,'prompt':original['prompt']+'改成纯白背景'}
        self.assertFalse(layered._valid_background_recipe(tampered))

    def test_prompt_only_and_empty_explicit_request_have_separate_sections(self):
        for request in ({},{'prompt':'移除右侧旧标注线'}):
            with self.subTest(request=request):
                recipe=self.recipe(request)
                self.assertTrue(layered._valid_background_recipe(recipe))
                self.assertIn('【本次编辑要求】',recipe['prompt'])
                self.assertIn('【原背景保留细节】',recipe['prompt'])

    def test_rejects_invalid_regions_before_submission(self):
        for region in ([.7,.2,.1,.4],[.1,.2,.1,.4],[0,0,1,2],[True,0,1,1],
                       ['0',0,1,1],[0,0,float('nan'),1],[0,0,float('inf'),1],[0,1]):
            with self.subTest(region=region):
                request=copy.deepcopy(self.request)
                request['remove_regions'][0]['region']=region
                with self.assertRaises(ValueError):self.recipe(request)

    def test_rejects_unknown_or_unbounded_instruction_fields(self):
        values=[{'source_path':'arbitrary-file'}, {'prompt':None}, {'prompt':'x'*2001},
                {'remove_regions':'all'}, {'preserve_details':'all'},
                {'remove_regions':[dict(region=[0,0,1,1])]},
                {'remove_regions':[dict(region=[0,0,1,1],description='  ')]},
                {'preserve_details':['  ']}, {'preserve_details':['x'*601]},
                {'remove_regions':self.request['remove_regions']*25},
                {'preserve_details':['x']*25}]
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):self.recipe(value)


if __name__=='__main__':unittest.main()
