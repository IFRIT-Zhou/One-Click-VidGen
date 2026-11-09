"""Offline actual-asset assembly and geometry-frozen grouped animation."""
import copy
import json
from pathlib import Path
import random
import subprocess
import tempfile
import unittest

from PIL import Image

from backend.app import vibe_layered_assembly as assembly


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.subject = self.root/'subject.png'
        Image.new('RGBA', (200, 160), (45, 67, 89, 255)).save(self.subject)
        self.background = self.root/'background.png'
        Image.new('RGB', (640, 360), '#13283C').save(self.background)
        self.assets = {'background':self.background, 'subject_0':self.subject}
        self.context = {'canvas':[640, 360], 'duration':12}
        self.raw = dict(background_color='#13283C', subjects=[dict(asset_id='subject_0', crop=[0,0,1,.75],
            box=[.05,.2,.35,.6], group='subject')], labels=[
            dict(box=[.1,.02,.9,.15], text='研究结果', color='#FFFFFF', font_size=36, group='title'),
            dict(box=[.4,.3,.9,.6], text='', plate=dict(color='#254863', gradient_to='#315978', stroke='#4DB6E8'), group='result'),
            dict(box=[.42,.33,.88,.57], text='49 对患者', color='#FFFFFF', font_size=42, group='result')],
            decorations=[dict(type='polyline', box=[.36,.35,.42,.5], points=[[0,.5],[.8,.5],[.6,.2],[.8,.5],[.6,.8]],
                stroke='#4DB6E8', group='result', motion='draw')])
        self.inventory = dict(subjects=[{'embedded_text':[]}], labels=[{'text':'研究结果'},{'text':'49'},{'text':'对患者'}])

    def compile(self):
        design = assembly.Assembly.model_validate(self.raw)
        prepared = assembly.materialize_subjects(design, self.assets, self.root/'placed')
        return design, prepared, assembly.compile_assembly(design, self.context, prepared)

    def schedule(self):
        return assembly.MotionSchedule(groups=[dict(id='title',at=0,enter=.6,motion='fade',exit=.3),
            dict(id='subject',at=2,enter=.7,motion='slide_left',exit=.3),
            dict(id='result',at=5,enter=.8,motion='draw',exit=.3)])

    def test_actual_rgba_crop_is_lossless_without_auto_trim_or_source_changes(self):
        before = self.subject.read_bytes()
        design, assets, plan = self.compile()
        with Image.open(assets['placed_subject_0']) as image:
            self.assertEqual(image.size, (200,120))
            self.assertEqual(image.getpixel((100,100)), (45,67,89,255))
        self.assertEqual(self.subject.read_bytes(),before)
        self.assertEqual(plan['elements'][1]['asset_id'],'placed_subject_0')
        self.assertTrue(assembly.validate_content(design,self.inventory))
        with self.assertRaises(FileExistsError):
            assembly.materialize_subjects(design,self.assets,self.root/'placed')

    def test_boundaries_inverted_empty_and_unbound_assets_fail_closed(self):
        for crop in ([.5,0,.5,1], [0,.9,1,.2]):
            raw=copy.deepcopy(self.raw);raw['subjects'][0]['crop']=crop
            with self.subTest(crop=crop),self.assertRaises(ValueError):
                assembly.materialize_subjects(assembly.Assembly.model_validate(raw),self.assets,self.root/'bad')
        raw=copy.deepcopy(self.raw);raw['subjects'][0]['asset_id']='subject_7'
        with self.assertRaises(ValueError):
            assembly.materialize_subjects(assembly.Assembly.model_validate(raw),self.assets,self.root/'unbound')
        Image.new('RGBA',(200,160),(0,0,0,0)).save(self.subject)
        with self.assertRaisesRegex(ValueError,'完全透明'):
            assembly.materialize_subjects(assembly.Assembly.model_validate(self.raw),self.assets,self.root/'transparent')

    def test_content_allows_split_join_but_blocks_missing_repeated_or_changed_numbers(self):
        design=assembly.Assembly.model_validate(self.raw)
        self.assertTrue(assembly.validate_content(design,self.inventory))
        for text in ('94 对患者','49 对患者患者','4.9 对患者','49 人','对患者'):
            raw=copy.deepcopy(self.raw);raw['labels'][-1]['text']=text
            with self.subTest(text=text),self.assertRaises(ValueError):
                assembly.validate_content(assembly.Assembly.model_validate(raw),self.inventory)

    def test_retained_raster_text_counts_once_and_all_subjects_must_remain(self):
        raw=copy.deepcopy(self.raw);raw['subjects'][0]['embedded_text']=['研究结果'];raw['labels']=raw['labels'][1:]
        self.assertTrue(assembly.validate_content(assembly.Assembly.model_validate(raw),self.inventory))
        raw['labels'].append(self.raw['labels'][0])
        with self.assertRaises(ValueError):assembly.validate_content(assembly.Assembly.model_validate(raw),self.inventory)
        raw=copy.deepcopy(self.raw);raw['subjects']=[]
        with self.assertRaisesRegex(ValueError,'全部主体'):
            assembly.validate_content(assembly.Assembly.model_validate(raw),self.inventory)

    def test_missing_inventory_title_can_be_recovered_with_original_image_coordinates(self):
        raw=copy.deepcopy(self.raw)
        raw['labels'].append(dict(box=[.1,.8,.9,.95],text='额外原图标题',color='#FFFFFF',font_size=32,group='footnote'))
        raw['recovered_text']=[dict(text='额外原图标题',source_box=[.1,.8,.9,.95])]
        before=copy.deepcopy(raw)
        design=assembly.Assembly.model_validate(raw)
        self.assertTrue(assembly.validate_content(design,self.inventory))
        self.assertEqual(raw,before)
        self.assertEqual(design.model_dump()['recovered_text'][0]['text'],'额外原图标题')
        prepared=assembly.materialize_subjects(design,self.assets,self.root/'recovered')
        plan=assembly.compile_assembly(design,self.context,prepared)
        self.assertEqual(sum(e.get('text')=='额外原图标题' for e in plan['elements']),1)
        self.assertNotIn('recovered_text',plan,'Audit claims are not unknown renderer properties')

    def test_recovery_can_be_displayed_as_embedded_text_or_split_labels(self):
        for method in ('embedded','split'):
            raw=copy.deepcopy(self.raw)
            raw['recovered_text']=[dict(text='原图随访 24 个月',source_box=[.1,.7,.9,.9])]
            if method=='embedded':raw['subjects'][0]['embedded_text']=['原图随访24个月']
            else:
                for text in ('原图随访','24','个月'):
                    raw['labels'].append(dict(box=[.1,.7,.9,.9],text=text,color='#FFFFFF',font_size=32,group='footnote'))
            with self.subTest(method=method):
                self.assertTrue(assembly.validate_content(assembly.Assembly.model_validate(raw),self.inventory))

    def test_recovery_rejects_missing_invented_or_duplicate_claims(self):
        for case in ('not_displayed','undeclared','existing','duplicate','missing_old','wrong_numeric'):
            raw=copy.deepcopy(self.raw)
            raw['recovered_text']=[dict(text='新增原图标题',source_box=[.1,.7,.9,.9])]
            if case!='not_displayed':
                raw['labels'].append(dict(box=[.1,.7,.9,.9],text='新增原图标题',color='#FFFFFF',font_size=32,group='footnote'))
            if case=='undeclared':raw['recovered_text']=[]
            if case=='existing':
                raw['recovered_text'][0]['text']='研究结果'
                raw['labels'][-1]['text']='研究结果'
            if case=='duplicate':raw['recovered_text']*=2
            if case=='missing_old':raw['labels'].pop(0)
            if case=='wrong_numeric':raw['labels'][2]['text']='94对患者'
            with self.subTest(case=case),self.assertRaises(ValueError):
                assembly.validate_content(assembly.Assembly.model_validate(raw),self.inventory)

    def test_recovery_requires_nonempty_text_and_valid_normalized_source_box(self):
        cases=[dict(text='',source_box=[0,0,1,1]),dict(text=' ，。 ',source_box=[0,0,1,1]),
            dict(text='标题',source_box=[.8,0,.2,1]),dict(text='标题',source_box=[0,.5,1,.5]),
            dict(text='标题',source_box=[0,0,2,1]),dict(text='标题'),
            dict(text='标题',source_box=[0,0,1,1],type='json_object')]
        for item in cases:
            raw={**copy.deepcopy(self.raw),'recovered_text':[item]}
            with self.subTest(item=item),self.assertRaises(ValueError):assembly.Assembly.model_validate(raw)

    def test_only_exact_top_level_transport_marker_is_ignored_for_both_model_responses(self):
        for schema,raw in ((assembly.Assembly,self.raw),(assembly.MotionSchedule,self.schedule().model_dump())):
            marked={**copy.deepcopy(raw),'type':'json_object'};before=copy.deepcopy(marked)
            self.assertNotIn('type',schema.model_validate(marked).model_dump())
            self.assertEqual(marked,before)
            for marker in ('json_schema','JSON_OBJECT',None,{}):
                with self.subTest(schema=schema.__name__,marker=marker),self.assertRaises(ValueError):
                    schema.model_validate({**copy.deepcopy(raw),'type':marker})
            with self.assertRaises(ValueError):schema.model_validate({**marked,'unknown':True})
        nested=copy.deepcopy(self.raw);nested['subjects'][0]['type']='json_object'
        with self.assertRaises(ValueError):assembly.Assembly.model_validate(nested)
        nested=self.schedule().model_dump();nested['groups'][0]['type']='json_object'
        with self.assertRaises(ValueError):assembly.MotionSchedule.model_validate(nested)

    def test_line_points_remain_required_and_director_explains_coordinate_contract(self):
        for kind,points in (('line',[]),('polyline',[]),('line',[[0,0],[1,1],[0,1]])):
            raw=copy.deepcopy(self.raw);raw['decorations'][0].update(type=kind,points=points)
            with self.subTest(kind=kind,points=points),self.assertRaises(ValueError):assembly.Assembly.model_validate(raw)
        self.assertIn('line恰好2点',assembly.ASSEMBLY_DIRECTOR)
        self.assertIn('不能只写box省略points',assembly.ASSEMBLY_DIRECTOR)
        self.assertIn('box 内部',assembly.Decoration.model_json_schema()['properties']['points']['description'])

    def test_added_decoration_glyphs_report_exact_labels_and_keep_content_rules(self):
        raw=copy.deepcopy(self.raw)
        for text in ('«','»'):
            raw['labels'].append(dict(box=[.1,.7,.2,.9],text=text,color='#FFFFFF',font_size=32,group='arrows'))
        design=assembly.Assembly.model_validate(raw);before=design.model_dump()
        with self.assertRaises(ValueError) as caught:assembly.validate_content(design,self.inventory)
        message=str(caught.exception)
        for token in ('新增字符','«','»','labels[3]','labels[4]','g:arrows:text_3','"group":"arrows"',
                      'polyline','保留已正确主体','不作为文字无条件豁免'):
            self.assertIn(token,message)
        self.assertEqual(design.model_dump(),before)
        raw['labels']=raw['labels'][:-2]
        raw['decorations'].append(dict(type='polyline',group='arrows',box=[.1,.7,.2,.9],points=[[1,0],[0,.5],[1,1]]))
        self.assertTrue(assembly.validate_content(assembly.Assembly.model_validate(raw),self.inventory))

    def test_missing_and_duplicated_characters_report_counts_and_relevant_source(self):
        raw=copy.deepcopy(self.raw);raw['labels'][-1]['text']='49对患患者'
        with self.assertRaises(ValueError) as caught:
            assembly.validate_content(assembly.Assembly.model_validate(raw),self.inventory)
        message=str(caught.exception)
        self.assertIn('重复字符',message)
        self.assertIn('"char":"患","expected":1,"actual":2,"delta":1',message)
        self.assertIn('labels[2]',message)
        self.assertIn('g:result:text_2',message)
        raw=copy.deepcopy(self.raw);raw['labels'][0]['text']='研究'
        with self.assertRaises(ValueError) as caught:
            assembly.validate_content(assembly.Assembly.model_validate(raw),self.inventory)
        message=str(caught.exception)
        for token in ('缺少字符','果','结','inventory.labels[0]','研究结果','不改写事实','可能缺字标签','g:title:text_0'):
            self.assertIn(token,message)

    def test_reordered_digits_report_numeric_tokens_and_changed_layer(self):
        raw=copy.deepcopy(self.raw);raw['labels'][-1]['text']='94对患者'
        with self.assertRaises(ValueError) as caught:
            assembly.validate_content(assembly.Assembly.model_validate(raw),self.inventory)
        message=str(caught.exception)
        for token in ('缺少原数值','新增或改变数值','"49":1','"94":1','labels[2]','g:result:text_2','94对患者'):
            self.assertIn(token,message)

    def test_mixed_font_numbers_units_and_multicolor_title_can_split_in_same_group(self):
        raw=copy.deepcopy(self.raw)
        raw['labels']=[
            dict(box=[.1,.02,.3,.15],text='研究',color='#FFFFFF',font_size=36,group='title'),
            dict(box=[.3,.02,.5,.15],text='结果',color='#4DB6E8',font_size=36,group='title'),
            dict(box=[.4,.3,.6,.6],text='49',color='#FFFFFF',font_size=64,group='result'),
            dict(box=[.6,.4,.9,.6],text='对患者',color='#FFFFFF',font_size=28,group='result')]
        design=assembly.Assembly.model_validate(raw)
        self.assertTrue(assembly.validate_content(design,self.inventory))
        placed=assembly.materialize_subjects(design,self.assets,self.root/'mixed-fonts')
        plan=assembly.compile_assembly(design,self.context,placed)
        self.assertEqual([e['font_size'] for e in plan['elements'] if e['type']=='text'],[36,36,64,28])
        self.assertIn('不同字号',assembly.ASSEMBLY_DIRECTOR)
        self.assertIn('同组邻接标签',assembly.ASSEMBLY_DIRECTOR)

    def test_tiny_label_height_fails_early_with_specific_geometry_without_guessing_width(self):
        raw=copy.deepcopy(self.raw)
        raw['labels'][-1].update(box=[.4,.3,.6,.31],font_size=64)
        design=assembly.Assembly.model_validate(raw)
        assets={**self.assets,'placed_subject_0':self.subject}
        with self.assertRaises(ValueError) as caught:assembly.compile_assembly(design,self.context,assets)
        for token in ('labels[2]','g:result:text_2','49 对患者','高度不足最低一行','最低字号','保留文字和其他素材/坐标'):
            self.assertIn(token,str(caught.exception))
        raw['labels'][-1].update(box=[.4,.3,.41,.6],font_size=12)
        # Width-dependent wrapping is left to browser measurement, not guessed.
        assembly.compile_assembly(assembly.Assembly.model_validate(raw),self.context,assets)

    def test_line_points_error_names_geometry_and_group_without_guessing_direction(self):
        raw=copy.deepcopy(self.raw)
        raw['decorations'][0].update(type='polyline',points=[[.5,.5],[.5,.5]])
        with self.assertRaises(ValueError) as caught:assembly.Assembly.model_validate(raw)
        for token in ('decorations.0','group=result','box=','points=','零长度','不猜测'):
            self.assertIn(token,str(caught.exception))

    def test_prepared_variant_uses_explicit_original_pixels_without_changing_subject_identity(self):
        original=self.root/'original.png';Image.new('RGBA',(200,160),(180,20,30,255)).save(original)
        assets={**self.assets,'subject_0_original':original}
        for variant,colour in (('cutout',(45,67,89,255)),('original',(180,20,30,255))):
            raw=copy.deepcopy(self.raw);raw['subjects'][0]['prepared_variant']=variant
            design=assembly.Assembly.model_validate(raw)
            with self.subTest(variant=variant):
                self.assertEqual(design.subjects[0].asset_id,'subject_0')
                self.assertEqual(assembly.resolve_subject_asset(design.subjects[0],assets),
                                 original if variant=='original' else self.subject)
                placed=assembly.materialize_subjects(design,assets,self.root/variant)
                with Image.open(placed['placed_subject_0']) as image:
                    self.assertEqual(image.size,(200,120));self.assertEqual(image.getpixel((80,80)),colour)
                self.assertTrue(assembly.validate_content(design,self.inventory))
        self.assertEqual(assembly.Assembly.model_validate(self.raw).subjects[0].prepared_variant,'cutout')

    def test_original_variant_missing_or_arbitrary_path_rejects_and_embedded_text_still_checked(self):
        raw=copy.deepcopy(self.raw);raw['subjects'][0]['prepared_variant']='original'
        design=assembly.Assembly.model_validate(raw)
        with self.assertRaisesRegex(ValueError,'subject_0_original'):
            assembly.materialize_subjects(design,self.assets,self.root/'missing-original')
        raw['subjects'][0]['prepared_variant']='../../secret.png'
        with self.assertRaises(ValueError):assembly.Assembly.model_validate(raw)
        raw['subjects'][0]['prepared_variant']='original';raw['subjects'][0]['embedded_text']=['研究结果']
        with self.assertRaisesRegex(ValueError,'重复字符'):
            assembly.validate_content(assembly.Assembly.model_validate(raw),self.inventory)

    def test_original_variant_does_not_guess_coordinate_mapping_for_different_pixel_bounds(self):
        original=self.root/'mismatched-original.png';Image.new('RGBA',(640,360),'white').save(original)
        raw=copy.deepcopy(self.raw);raw['subjects'][0]['prepared_variant']='original'
        with self.assertRaisesRegex(ValueError,'像素边界不一致'):
            assembly.resolve_subject_asset(assembly.Assembly.model_validate(raw).subjects[0],
                                           {**self.assets,'subject_0_original':original})

    def test_prepared_crop_is_the_only_new_schema_and_serialized_coordinate_key(self):
        properties=assembly.Assembly.model_json_schema()['$defs']['AssemblySubject']['properties']
        self.assertIn('prepared_crop',properties)
        self.assertNotIn('crop',properties)
        self.assertEqual(properties['prepared_crop']['default'],[0,0,1,1])
        self.assertIn('素材已从 core/参考图裁过',properties['prepared_crop']['description'])
        legacy=copy.deepcopy(self.raw);before=copy.deepcopy(legacy)
        design=assembly.Assembly.model_validate(legacy)
        self.assertEqual(design.subjects[0].prepared_crop,(0,0,1,.75))
        self.assertEqual(design.subjects[0].crop,design.subjects[0].prepared_crop)
        self.assertNotIn('crop',design.model_dump()['subjects'][0])
        self.assertEqual(design.model_dump()['subjects'][0]['prepared_crop'],(0,0,1,.75))
        self.assertEqual(legacy,before,'Legacy normalization must not mutate diagnostic JSON')
        raw=copy.deepcopy(self.raw);raw['subjects'][0]['prepared_crop']=[0,0,1,1]
        with self.assertRaisesRegex(ValueError,'不能同时提供 crop 和 prepared_crop'):
            assembly.Assembly.model_validate(raw)
        raw['subjects'][0].pop('crop')
        self.assertEqual(assembly.Assembly.model_validate(raw).subjects[0].prepared_crop,(0,0,1,1))

    def test_source_crop_copy_is_rejected_for_general_prepared_subjects_without_auto_conversion(self):
        rng=random.Random(19)
        for index in range(8):
            source=[round(rng.uniform(.05,.3),4),round(rng.uniform(.05,.3),4),
                    round(rng.uniform(.65,.95),4),round(rng.uniform(.65,.95),4)]
            inventory=copy.deepcopy(self.inventory);inventory['subjects'][0]['crop']=source
            raw=copy.deepcopy(self.raw);raw['subjects'][0].pop('crop')
            raw['subjects'][0]['prepared_crop']=source
            design=assembly.Assembly.model_validate(raw);before=design.model_dump()
            with self.subTest(index=index),self.assertRaises(ValueError) as caught:
                assembly.validate_content(design,inventory)
            message=str(caught.exception)
            for token in ('坐标空间混用','subjects[0]','asset_id=subject_0','inventory.subjects[0].crop',
                          'prepared_crop=[0,0,1,1]','不自动换算'):
                self.assertIn(token,message)
            self.assertEqual(design.model_dump(),before)
            # Default whole-prepared-asset placement is valid for any source location.
            raw['subjects'][0].pop('prepared_crop')
            self.assertTrue(assembly.validate_content(assembly.Assembly.model_validate(raw),inventory))

    def test_copy_detector_is_exact_not_a_broad_nearby_coordinate_heuristic(self):
        inventory=copy.deepcopy(self.inventory);inventory['subjects'][0]['crop']=[.15,.22,.78,.83]
        raw=copy.deepcopy(self.raw);raw['subjects'][0].pop('crop')
        raw['subjects'][0]['prepared_crop']=[.15,.22,.78,.8300001]
        with self.assertRaisesRegex(ValueError,'坐标空间混用'):
            assembly.validate_content(assembly.Assembly.model_validate(raw),inventory)
        raw['subjects'][0]['prepared_crop']=[.15,.22,.78,.84]
        self.assertTrue(assembly.validate_content(assembly.Assembly.model_validate(raw),inventory))
        inventory['subjects'][0]['crop']=[0,0,1,1]
        raw['subjects'][0]['prepared_crop']=[0,0,1,1]
        self.assertTrue(assembly.validate_content(assembly.Assembly.model_validate(raw),inventory))

    def test_new_prepared_crop_materializes_only_actual_asset_relative_pixels(self):
        raw=copy.deepcopy(self.raw);raw['subjects'][0].pop('crop')
        raw['subjects'][0]['prepared_crop']=[0,.1,1,.9]
        design=assembly.Assembly.model_validate(raw)
        prepared=assembly.materialize_subjects(design,self.assets,self.root/'prepared-space')
        with Image.open(prepared['placed_subject_0']) as im:
            self.assertEqual(im.size,(200,128))
            self.assertEqual(im.getpixel((100,60)),(45,67,89,255))
        for token in ('prepared_crop默认整张[0,0,1,1]','不裁是首选','严禁复制初次inventory',
                      '醒目数字保持大字号','单位/说明保持较小字号'):
            self.assertIn(token,assembly.ASSEMBLY_DIRECTOR)

    def test_assembly_supports_renderer_maximum_200_font_without_relaxing_legacy_design(self):
        from backend.app.vibe_layered import Label
        raw=copy.deepcopy(self.raw)
        raw['labels'][-1].update(text='49',font_size=200,box=[.1,.2,.8,.85])
        raw['labels'].append(dict(text='对患者',font_size=42,color='#FFFFFF',group='result',box=[.6,.75,.9,.95]))
        design=assembly.Assembly.model_validate(raw)
        self.assertTrue(assembly.validate_content(design,self.inventory))
        prepared=assembly.materialize_subjects(design,self.assets,self.root/'large-number')
        plan=assembly.compile_assembly(design,self.context,prepared)
        self.assertEqual(next(e['font_size'] for e in plan['elements'] if e.get('text')=='49'),200)
        self.assertEqual(assembly.AssemblyLabel.model_json_schema()['properties']['font_size']['anyOf'][0]['maximum'],200)
        self.assertEqual(Label.model_json_schema()['properties']['font_size']['anyOf'][0]['maximum'],140)
        raw['labels'][2]['font_size']=200.01
        with self.assertRaises(ValueError):assembly.Assembly.model_validate(raw)
        root=Path(__file__).resolve().parents[1];node=root/'runtime/node/node.exe'
        if not node.is_file():self.skipTest('bundled Node unavailable')
        script="import {validateLayeredInput} from './lib/layered-contract.mjs';let s='';for await(const b of process.stdin)s+=b;validateLayeredInput(JSON.parse(s));console.log('ok')"
        result=subprocess.run([str(node),'--input-type=module','-e',script],cwd=root/'tools/vibe_remotion',
            input=json.dumps(dict(plan=plan,assets={k:str(v) for k,v in prepared.items()},duration=12,fps=30)),
            text=True,capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_static_assembly_does_not_require_animation_or_emit_unknown_fields(self):
        _, _, plan = self.compile()
        before=copy.deepcopy(plan)
        still=assembly.static_plan(plan)
        self.assertEqual(plan,before)
        self.assertTrue(all(e['at']==0 and e['enter']==0 and e['exit']==0 and e['motion']=='none' for e in still['elements']))
        self.assertEqual(set(assembly.plan_groups(plan)),{'subject','title','result'})
        self.assertTrue(all('group' not in e and 'crop' not in e for e in plan['elements']))
        self.assertFalse(any(e['type']=='text' and not e['text'].strip() for e in plan['elements']))

    def test_animation_preserves_geometry_text_assets_and_background(self):
        _, _, plan=self.compile()
        before=copy.deepcopy(plan)
        animated=assembly.apply_motion(plan,self.schedule(),self.context)
        self.assertEqual(plan,before)
        self.assertEqual(animated['elements'][0],plan['elements'][0])
        timing={'at','end','enter','exit','motion'}
        for old,new in zip(plan['elements'],animated['elements']):
            self.assertEqual({k:v for k,v in old.items() if k not in timing},{k:v for k,v in new.items() if k not in timing})
        result_nodes=[e for e in animated['elements'] if e['id'].startswith('g:result:')]
        self.assertTrue(all(e['at']==5 and e['enter']==.8 for e in result_nodes))
        self.assertEqual(next(e for e in result_nodes if e['type']=='polyline')['motion'],'draw')
        self.assertTrue(all(e['motion']=='fade' for e in result_nodes if e['type']!='polyline'))

    def test_motion_must_cover_groups_and_stagger_long_shots(self):
        _, _, plan=self.compile()
        for change in ('missing','extra','simultaneous','too_late'):
            rows=self.schedule().model_dump()['groups']
            if change=='missing':rows.pop()
            if change=='extra':rows.append(dict(id='invented',at=3,motion='fade'))
            if change=='simultaneous':
                for row in rows:row['at']=0
            if change=='too_late':rows[-1]['at']=11
            with self.subTest(change=change),self.assertRaises(ValueError):
                assembly.apply_motion(plan,assembly.MotionSchedule(groups=rows),self.context)

    def test_group_schedule_never_accepts_changed_layout_or_model_code(self):
        raw=self.schedule().model_dump();raw['groups'][0]['box']=[0,0,1,1]
        with self.assertRaises(ValueError):assembly.MotionSchedule.model_validate(raw)
        raw=copy.deepcopy(self.raw);raw['labels'][0]['group']='../secret'
        with self.assertRaises(ValueError):assembly.Assembly.model_validate(raw)
        raw=copy.deepcopy(self.raw);raw['script']='alert(1)'
        with self.assertRaises(ValueError):assembly.Assembly.model_validate(raw)

    def test_compiled_plans_pass_real_remotion_whitelist(self):
        _, assets, plan=self.compile()
        root=Path(__file__).resolve().parents[1]
        node=root/'runtime/node/node.exe'
        if not node.is_file():self.skipTest('bundled Node unavailable')
        script="import {validateLayeredInput} from './lib/layered-contract.mjs';let s='';for await(const b of process.stdin)s+=b;validateLayeredInput(JSON.parse(s));console.log('ok')"
        for candidate in (plan,assembly.static_plan(plan),assembly.apply_motion(plan,self.schedule(),self.context)):
            result=subprocess.run([str(node),'--input-type=module','-e',script],cwd=root/'tools/vibe_remotion',
                input=json.dumps(dict(plan=candidate,assets={k:str(v) for k,v in assets.items()},duration=12,fps=30)),
                text=True,capture_output=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main()
