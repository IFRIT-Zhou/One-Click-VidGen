"""Core style is extracted once and cannot drift during narrative repairs."""
import copy
import unittest

from pydantic import ValidationError

from backend.app.vibe_motion_plan import MotionPlan, compile_motion_plan
from backend.app.vibe_source_style import SourceStyle, bind_source_style


class SourceStyleTests(unittest.TestCase):
    def profile(self):
        return dict(source='core', style_notes='深蓝背景，青色结构线，白色文字，科技感',
            palette=dict(background='#091923',ink='#FFFFFF',muted='#B9CBD3',surface='#173543',
                         line='#49768A',accent='#67D5CA'), background=dict(kind='technical_grid'))

    def raw(self):
        return dict(source_style=self.profile(), scene=dict(title='研究信息',theme='paper',
            palette=dict(background='#FFFFFF',ink='#000000'),background=dict(kind='paper'),
            stages=[dict(id='one',layout='findings',headline='信息',items=[dict(id='a',title='结果')])]))

    def test_core_profile_overrides_scene_colours_and_background_before_validation(self):
        raw=self.raw();before=copy.deepcopy(raw)
        bound,profile=bind_source_style(raw)
        plan=MotionPlan.model_validate(bound['scene'])
        result=compile_motion_plan(plan,{},5,set())
        self.assertEqual(result['palette'],self.profile()['palette'])
        self.assertEqual(result['background'],{'kind':'technical_grid'})
        self.assertEqual(result['accent'],self.profile()['palette']['accent'])
        self.assertEqual(raw,before)
        self.assertEqual(profile.source,'core')

    def test_frozen_first_profile_survives_changed_or_missing_repair_style(self):
        _,profile=bind_source_style(self.raw())
        for mode in ('changed','omitted'):
            repair=self.raw()
            if mode=='changed': repair['source_style']['palette']['background']='#FFFFFF'
            else: repair.pop('source_style')
            before=copy.deepcopy(repair)
            bound,again=bind_source_style(repair,profile)
            with self.subTest(mode=mode):
                self.assertEqual(again.model_dump(),profile.model_dump())
                self.assertEqual(bound['scene']['palette'],profile.palette.model_dump())
                self.assertEqual(repair,before)

    def test_new_design_cannot_omit_source_profile_or_silently_use_default(self):
        with self.assertRaisesRegex(ValueError,'不能默默改成默认白底'):
            bind_source_style({'scene':self.raw()['scene']})

    def test_source_profile_requires_all_six_colours_core_and_known_background(self):
        for alteration in ('missing-colour','not-core','empty-notes','css','background','extra'):
            profile=self.profile()
            if alteration=='missing-colour': profile['palette'].pop('line')
            if alteration=='not-core': profile['source']='ref_03'
            if alteration=='empty-notes': profile['style_notes']=' '
            if alteration=='css': profile['palette']['background']='url(private.png)'
            if alteration=='background': profile['background']['kind']='remote_texture'
            if alteration=='extra': profile['background']['url']='https://example.invalid'
            with self.subTest(alteration=alteration),self.assertRaises(ValidationError):
                SourceStyle.model_validate(profile)

    def test_palette_contrast_still_checked_without_replacing_source_background(self):
        profile=self.profile();profile['palette']['ink']=profile['palette']['background']
        with self.assertRaisesRegex(ValidationError,'正文/背景对比度不足'):
            SourceStyle.model_validate(profile)

    def test_body_and_muted_text_must_also_contrast_with_card_surface(self):
        for name, surface in (('正文','#FFFFFF'),('辅助文字','#666666')):
            profile=self.profile();profile['palette']['surface']=surface
            if name=='辅助文字':profile['palette']['muted']='#999999'
            with self.subTest(name=name),self.assertRaisesRegex(ValidationError,name+'/卡片底色对比度不足'):
                SourceStyle.model_validate(profile)

    def test_legacy_motion_plan_may_omit_background(self):
        raw=self.raw()['scene'];raw.pop('background');raw.pop('palette')
        self.assertIsNone(MotionPlan.model_validate(raw).background)


if __name__=='__main__': unittest.main()
