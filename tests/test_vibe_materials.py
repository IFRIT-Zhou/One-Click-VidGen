"""Offline material boundaries and one-shot studio queue integration."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from backend.app import vibe_materials as materials


class VibeMaterialsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.project = self.root/'video'/'7'/('a'*32)
        self.project.mkdir(parents=True)
        self.out = self.project/'assets'/'vibe'/'test'
        self.source = self.project/'core.png'
        image = Image.new('RGBA', (100, 80), (31, 71, 131, 255))
        ImageDraw.Draw(image).rectangle((0, 0, 49, 79), fill=(160, 10, 20, 255))
        image.save(self.source)
        self.record = {'references':[{'id':'ref_03','file':'ref3.png'}, {'id':'ref_04','file':'ref4.png'}, {'id':'ref_99','file':'unbound-missing.png'}],
                       'scene_assets':[{'id':'scene_a','image':'scene.png','image_status':'completed'}]}
        for name in ['ref3.png', 'ref4.png', 'scene.png']:
            image.save(self.project/name)
        self.shot = {'id':'shot_1','image':'core.png','reference_ids':['ref_03','ref_04'],'scene_reference_id':'scene_a'}
        for item in [patch.object(materials.video,'ROOT',self.root/'video'),
                     patch.object(materials.studio,'ROOT',self.root/'studio'),
                     patch.object(materials.studio,'ACTIVE',set()), patch.object(materials.studio,'QUEUED',[]),
                     patch('backend.app.video_scene_references.enabled', return_value=True)]:
            item.start();self.addCleanup(item.stop)

    def call(self, **kw):
        from backend.app.vibe_image_route import fingerprint
        config=dict(endpoint='https://example.invalid/generate',api_key='test',model='personal-image',resolution='4k',ratio='16:9')
        route=dict(key=fingerprint(config),provider='custom',method='running',size='2560x1440',ratio='16:9',resolution='4k',label='个人 API')
        args = dict(user_id=7,prompt='制作一张抽象流程插画',reference_paths=[self.source],
                    output_dir=self.out,name='piece_01',timeout=.01,image_config=config,image_route=route)
        args.update(kw)
        return materials.generate_asset(**args)

    def test_only_bound_assets_keep_stable_ids_and_do_not_mutate_record(self):
        before = copy.deepcopy((self.record,self.shot))
        result = materials.collect_assets(self.project,self.shot,self.record)
        self.assertEqual(set(result), {'core','ref_03','ref_04','scene:scene_a'})
        self.assertEqual(result['ref_03'],self.project/'ref3.png')
        self.assertEqual(before,(self.record,self.shot))

    def test_explicit_redraw_selection_supersedes_planning_references(self):
        self.shot['redraw_selection']={'reference_ids':['ref_04'],'use_scene_reference':False}
        self.assertEqual(set(materials.collect_assets(self.project,self.shot,self.record)),{'core','ref_04'})

    def test_disabled_scenes_are_not_implicitly_uploaded(self):
        self.shot['scene_reference_disabled']=True
        self.assertEqual(set(materials.collect_assets(self.project,self.shot,self.record)),{'core','ref_03','ref_04'})

    def test_unknown_and_outside_references_reject(self):
        self.shot['reference_ids']=['ref_missing']
        with self.assertRaises(materials.MaterialError):
            materials.collect_assets(self.project,self.shot,self.record)
        self.shot['reference_ids']=['ref_03']
        outside=self.root/'outside.png';Image.new('RGB',(20,20)).save(outside)
        self.record['references'][0]['file']=str(outside)
        with self.assertRaises(materials.MaterialError):
            materials.collect_assets(self.project,self.shot,self.record)

    def reference(self, kind, identity, file, number=1):
        return {'kind':kind, 'id':identity, 'file':file, 'local_image_number':number,
                'sha256':hashlib.sha256((self.project/file).read_bytes()).hexdigest()}

    def snapshot(self, references):
        value = {'version':1, 'image_sha256':hashlib.sha256(self.source.read_bytes()).hexdigest(),
                 'references':references}
        self.shot['image_reference_snapshot'] = value
        return value

    def test_generation_snapshot_overrides_later_selection_and_keeps_actual_order(self):
        Image.new('RGB',(20,20),'green').save(self.project/'previous.png')
        Image.new('RGB',(20,20),'white').save(self.project/'old-core.png')
        self.snapshot([
            self.reference('current_image','shot_1','old-core.png',1),
            self.reference('reference','ref_04','ref4.png',2),
            self.reference('scene_reference','scene_a','scene.png',3),
            self.reference('previous_shot','previous_1','previous.png',4)])
        self.shot.update(redraw_selection={'reference_ids':['ref_99'],'use_scene_reference':False},
                         image_task={'status':'failed','action':'redraw'},scene_reference_disabled=True)
        before=copy.deepcopy((self.shot,self.record))
        result=materials.collect_assets(self.project,self.shot,self.record)
        self.assertEqual(list(result),['core','source_image:shot_1','ref_04','scene:scene_a','previous:previous_1'])
        self.assertEqual(before,(self.shot,self.record))

    def test_empty_generation_snapshot_does_not_send_current_bound_materials(self):
        self.snapshot([])
        self.assertEqual(list(materials.collect_assets(self.project,self.shot,self.record)),['core'])

    def test_generation_snapshot_rejects_changed_core_and_missing_bound_reference(self):
        snapshot=self.snapshot([self.reference('reference','ref_03','ref3.png')])
        original=snapshot['image_sha256']
        snapshot['image_sha256']='0'*64
        with self.assertRaisesRegex(materials.MaterialError,'当前分镜图与生成参考快照不一致'):
            materials.collect_assets(self.project,self.shot,self.record)
        snapshot['image_sha256']=original
        # ref4 has the old bytes, but is not the declared source's history.
        Image.new('RGB',(20,20),'red').save(self.project/'ref3.png')
        with self.assertRaisesRegex(materials.MaterialError,'参考 ref_03 已丢失或内容改变'):
            materials.collect_assets(self.project,self.shot,self.record)

    def test_current_image_input_is_restored_by_hash_from_own_history(self):
        Image.new('RGB',(20,20),'purple').save(self.project/'old-core.png')
        source=self.reference('current_image','shot_1','old-core.png')
        source['file']='core.png'  # Legacy redraw input was overwritten on success.
        self.shot['image_history']=[{'image':'old-core.png'}]
        self.snapshot([source])
        result=materials.collect_assets(self.project,self.shot,self.record)
        self.assertEqual(result['source_image:shot_1'],self.project/'old-core.png')
        self.assertNotEqual(result['source_image:shot_1'],result['core'])

    def test_previous_shot_snapshot_restores_only_the_bound_previous_version(self):
        Image.new('RGB',(20,20),'purple').save(self.project/'previous-old.png')
        source=self.reference('previous_shot','previous_1','previous-old.png')
        source['file']='previous.png'
        Image.new('RGB',(20,20),'yellow').save(self.project/'previous.png')
        self.record['shots']=[{'id':'previous_1','image':'previous.png',
                               'image_history':[{'image':'previous-old.png'}]},self.shot]
        self.snapshot([source])
        result=materials.collect_assets(self.project,self.shot,self.record)
        self.assertEqual(result['previous:previous_1'],self.project/'previous-old.png')

    def test_scene_snapshot_restores_prior_pixels_even_if_scene_was_disabled(self):
        source=self.reference('scene_reference','scene_a','scene.png')
        (self.project/'scene-old.png').write_bytes((self.project/'scene.png').read_bytes())
        Image.new('RGB',(20,20),'yellow').save(self.project/'scene.png')
        self.record['scene_assets'][0].update(disabled=True,bridge_image_history=[{'image':'scene-old.png'}])
        self.shot['scene_reference_disabled']=True
        self.snapshot([source])
        result=materials.collect_assets(self.project,self.shot,self.record)
        self.assertEqual(result['scene:scene_a'],self.project/'scene-old.png')

    def test_snapshot_order_path_and_identity_validation_happen_before_upload(self):
        reference=self.reference('reference','ref_03','ref3.png')
        for change in ({'local_image_number':2},{'local_image_number':True},
                       {'kind':'library'},{'id':'../secret'},{'file':'../outside.png'}):
            with self.subTest(change=change):
                self.snapshot([{**reference,**change}])
                with self.assertRaises(materials.MaterialError):
                    materials.collect_assets(self.project,self.shot,self.record)

    def test_actual_duplicate_file_is_sent_only_once_using_first_identity(self):
        self.snapshot([self.reference('reference','ref_03','ref3.png',1),
                       self.reference('scene_reference','scene_a','ref3.png',2)])
        self.assertEqual(list(materials.collect_assets(self.project,self.shot,self.record)),['core','ref_03'])

    def test_derived_aliases_preserve_short_ids_and_fit_valid_long_ids(self):
        for kind,prefix in (('current_image','source_image:'),('previous_shot','previous:'),
                            ('scene_reference','scene:'),('reference','')):
            with self.subTest(kind=kind):
                boundary='a'*(80-len(prefix))
                self.assertEqual(materials._reference_key(kind,boundary),prefix+boundary)
                identity='a'*80
                alias=materials._reference_key(kind,identity)
                self.assertEqual(alias,materials._reference_key(kind,identity))
                self.assertLessEqual(len(alias),80)
                self.assertRegex(alias,r'^[A-Za-z0-9_:-]{1,80}$')
                self.assertTrue(alias.startswith(prefix))
                if prefix:
                    self.assertTrue(alias.endswith('_'+hashlib.sha256(identity.encode('ascii')).hexdigest()[:24]))

    def test_long_ids_with_the_same_truncated_prefix_have_distinct_stable_aliases(self):
        left,right='same_prefix_'+'a'*67+'1','same_prefix_'+'a'*67+'2'
        self.assertEqual(len(left),80)
        for kind in ('current_image','previous_shot','scene_reference'):
            with self.subTest(kind=kind):
                self.assertNotEqual(materials._reference_key(kind,left),materials._reference_key(kind,right))

    def test_long_snapshot_identity_is_not_modified_when_collecting_derived_alias(self):
        identity='s'*80
        self.shot['id']=identity
        Image.new('RGB',(20,20),'purple').save(self.project/'old-core.png')
        snapshot=self.snapshot([self.reference('current_image',identity,'old-core.png')])
        before=copy.deepcopy((snapshot,self.shot,self.record))
        result=materials.collect_assets(self.project,self.shot,self.record)
        alias=materials._reference_key('current_image',identity)
        self.assertEqual(list(result),['core',alias])
        self.assertEqual(result[alias],self.project/'old-core.png')
        self.assertEqual(before,(snapshot,self.shot,self.record))
        self.assertEqual(snapshot['references'][0]['id'],identity)

    def bridge_result(self, request_id='request_0001', references=None):
        folder=self.project/'assets'/'bridge_images';folder.mkdir(parents=True,exist_ok=True)
        (folder/(request_id+'.jpg')).write_bytes(self.source.read_bytes())
        entry={'shot_id':self.shot['id'],'request_id':request_id,'status':'completed',
               'references':references if references is not None else [self.reference('reference','ref_04','ref4.png')]}
        self.record.setdefault('codex_bridge',{}).setdefault('image_requests',{})[request_id]=entry
        return entry

    def test_bridge_restores_current_image_inputs_despite_later_failed_request(self):
        self.bridge_result()
        self.shot.update(redraw_selection={'reference_ids':['ref_99']},
                         image_task={'request_id':'request_0002','status':'failed'})
        self.record['codex_bridge']['image_requests']['request_0002']={
            'shot_id':self.shot['id'],'status':'failed','references':[{'id':'ref_99'}]}
        before=copy.deepcopy(self.record)
        result=materials.collect_assets(self.project,self.shot,self.record)
        self.assertEqual(list(result),['core','ref_04'])
        self.assertEqual(before,self.record)

    def test_bridge_completed_current_request_with_exact_image_version_survives_output_cleanup(self):
        self.bridge_result()
        (self.project/'assets'/'bridge_images'/'request_0001.jpg').unlink()
        self.shot.update(image_version=hashlib.sha256(self.source.read_bytes()).hexdigest(),
                         image_task={'status':'completed','request_id':'request_0001'})
        self.assertEqual(list(materials.collect_assets(self.project,self.shot,self.record)),['core','ref_04'])

    def test_newer_bridge_result_supersedes_stale_pre_bridge_generation_snapshot(self):
        self.snapshot([self.reference('reference','ref_03','ref3.png')])
        Image.new('RGB',(30,30),'yellow').save(self.source)
        self.bridge_result()
        self.assertEqual(list(materials.collect_assets(self.project,self.shot,self.record)),['core','ref_04'])

    def test_bridge_unrelated_completed_result_does_not_supply_its_inputs(self):
        self.bridge_result()
        Image.new('RGB',(20,20),'black').save(self.project/'assets'/'bridge_images'/'request_0001.jpg')
        self.record['codex_bridge']['image_requests']['request_0001']['references']=[{'id':'ref_99'}]
        self.assertEqual(list(materials.collect_assets(self.project,self.shot,self.record)),
                         ['core','ref_03','ref_04','scene:scene_a'])

    def test_legacy_failed_or_pending_redraw_selection_is_not_misrepresented(self):
        self.shot['redraw_selection']={'reference_ids':['ref_04']}
        for status in ('queued','running','failed','cancelled'):
            with self.subTest(status=status):
                self.shot['image_task']={'status':status,'action':'redraw'}
                with self.assertRaisesRegex(materials.MaterialError,'不能把新选择当作原图参考'):
                    materials.collect_assets(self.project,self.shot,self.record)

    def test_legacy_uploaded_core_does_not_inherit_stale_generated_references(self):
        self.shot.update(image_origin='upload',redraw_selection={'reference_ids':['ref_99']})
        self.assertEqual(list(materials.collect_assets(self.project,self.shot,self.record)),['core'])

    def test_legacy_previous_continuity_uses_applied_prompt_not_unsaved_prompt(self):
        Image.new('RGB',(20,20),'pink').save(self.project/'previous.png')
        self.record['shots']=[{'id':'previous_1','image':'previous.png','image_status':'completed'},self.shot]
        self.shot.update(image_applied_prompt='延续上一镜的器官结构，展示新的阶段',image_prompt='仅文字，不承接')
        result=materials.collect_assets(self.project,self.shot,self.record)
        self.assertEqual(list(result),['core','ref_03','ref_04','scene:scene_a','previous:previous_1'])
        self.shot.update(image_applied_prompt='独立主体',image_prompt='延续上一镜的器官结构')
        self.assertNotIn('previous:previous_1',materials.collect_assets(self.project,self.shot,self.record))

    def test_legacy_redraw_current_image_uses_archived_input_before_selected_references(self):
        Image.new('RGB',(20,20),'white').save(self.project/'old-core.png')
        self.shot.update(redraw_selection={'reference_ids':['ref_04'],'use_current_image':True,'use_scene_reference':False},
                         image_history=[{'image':'old-core.png'}],image_task={'status':'completed','action':'redraw'})
        self.assertEqual(list(materials.collect_assets(self.project,self.shot,self.record)),
                         ['core','source_image:shot_1','ref_04'])

    def test_legacy_scene_generation_hash_preserves_actual_disabled_old_scene(self):
        version=hashlib.sha256((self.project/'scene.png').read_bytes()).hexdigest()
        (self.project/'scene-old.png').write_bytes((self.project/'scene.png').read_bytes())
        Image.new('RGB',(20,20),'orange').save(self.project/'scene.png')
        self.record['scene_assets'][0].update(disabled=True,bridge_image_history=[{'image':'scene-old.png'}])
        self.shot.update(scene_reference_used_version=version,scene_reference_disabled=True)
        result=materials.collect_assets(self.project,self.shot,self.record)
        self.assertEqual(result['scene:scene_a'],self.project/'scene-old.png')

    def test_crop_preserves_source_pixels_at_native_size(self):
        result=materials.crop_asset({'core':self.source},'core',[.25,.25,.75,.75],self.out,'crop')
        with Image.open(self.source) as source, Image.open(result) as output:
            self.assertEqual(output.size,(50,40))
            self.assertEqual(output.tobytes(),source.crop((25,20,75,60)).tobytes())
        for box in ([0,0,2,1],[0,0,float('nan'),1],[True,0,1,1],[.5,0,.5,1]):
            with self.subTest(box=box),self.assertRaises(materials.MaterialError):
                materials.crop_asset({'core':self.source},'core',box,self.out,'bad')
        for name in ('../leak','bad.png','x/y'):
            with self.assertRaises(materials.MaterialError):
                materials.crop_asset({'core':self.source},'core',[0,0,1,1],self.out,name)
        with self.assertRaises(materials.MaterialError):
            materials.crop_asset({'core':self.source},'core',[0,0,1,1],self.out,'crop')

    def complete(self,user,path,record,config,client):
        Image.new('RGBA',(100,80),(12,32,64,255)).save(path/'result.png')
        record.update(status='completed')
        materials.studio.save(path,record)

    def test_uses_explicit_project_route_once_and_retains_durable_studio_record(self):
        ids=[]
        with patch.object(materials.studio,'config_for',return_value=({},None)) as configure, \
             patch.object(materials.studio,'enqueue',side_effect=self.complete) as enqueue:
            result=self.call(on_submitted=ids.append)
        self.assertEqual(enqueue.call_count,1)
        configure.assert_not_called()
        record,config=enqueue.call_args.args[2:4]
        self.assertEqual((record['provider'],record['resolution'],config['model']),('custom','4k','personal-image'))
        self.assertNotIn('api_key',record)
        self.assertEqual(ids,[result['studio_task_id']])
        stored=materials.studio.folder(7,ids[0])
        self.assertTrue((stored/'record.json').is_file())
        self.assertEqual(result['path'].read_bytes(),(stored/'result.png').read_bytes())
        self.assertTrue(json.loads((stored/'record.json').read_text(encoding='utf-8'))['vibe_material']['requires_review'])

    def test_matte_uses_model_mask_but_never_model_rgb(self):
        def complete_mask(user,path,record,config,client):
            proposed=Image.new('RGB',(1024,1024),'black')
            ImageDraw.Draw(proposed).rectangle((0,0,511,1023),fill='white')
            result=materials.layers.process_proposal(path,record,proposed)
            result.save(path/'result.png')
            record['status']='completed';materials.studio.save(path,record)
        with patch.object(materials.studio,'config_for',return_value=({},None)), \
             patch.object(materials.studio,'enqueue',side_effect=complete_mask):
            result=self.call(mode='matte',protected_evidence=True)
        with Image.open(result['path']) as image, Image.open(self.source) as original:
            self.assertEqual(image.getpixel((20,40)),original.getpixel((20,40)))
            self.assertEqual(image.getpixel((80,40))[:3],original.getpixel((80,40))[:3])
            self.assertEqual(image.getpixel((80,40))[3],0)

    def test_matte_wrapper_has_final_opaque_mask_contract_after_selection_text(self):
        selection = '选中左侧肝脏，保留原始自然色与材质细节，输出透明背景PNG'
        with patch.object(materials.studio,'config_for',return_value=({},None)) as configure, \
             patch.object(materials.studio,'enqueue',side_effect=self.complete) as enqueue:
            self.call(mode='matte', prompt=selection)
        prompt = enqueue.call_args.args[2]['prompt']
        self.assertIn(json.dumps(selection, ensure_ascii=False), prompt)
        self.assertIn('只是目标的位置、包含和排除范围说明，不是输出格式指令', prompt)
        self.assertGreater(prompt.index('最终输出要求'), prompt.index(selection))
        for instruction in ('不透明黑白蒙版', '纯白(255,255,255)', '纯黑(0,0,0)',
                            'alpha 必须全部为255', '禁止彩色图像、透明背景或彩色 RGBA 抠图',
                            '原始 RGB 与最终透明度由 OCV 程序'):
            self.assertIn(instruction, prompt)

    def test_coloured_matte_still_rejects_and_surfaces_the_specific_reason(self):
        source_before = self.source.read_bytes()
        def complete_colour(user,path,record,config,client):
            try:
                materials.layers.process_proposal(path,record,Image.new('RGBA',(1024,1024),(200,80,20,100)))
            except ValueError as error:
                record.update(status='failed',message=str(error))
                materials.studio.save(path,record)
        with patch.object(materials.studio,'config_for',return_value=({},None)), \
             patch.object(materials.studio,'enqueue',side_effect=complete_colour) as enqueue:
            with self.assertRaisesRegex(materials.MaterialError,'模型未返回可用的黑白蒙版') as failure:
                self.call(mode='matte',prompt='选择左侧主体')
        self.assertEqual(enqueue.call_count,1)
        self.assertIn(failure.exception.studio_task_id,str(failure.exception))
        self.assertEqual(source_before,self.source.read_bytes())
        self.assertFalse((self.out/'piece_01.png').exists())

    def test_studio_error_details_are_actionable_redacted_and_bounded(self):
        detail = ('蒙版格式错误；api_key=test-secret-value；Bearer bearer-secret-value '
                  'https://example.invalid/private?token=url-secret-value '
                  'G:\\private\\customer.png\nTraceback (most recent call last):\n  File "secret.py"')
        sanitized = materials._studio_failure_detail({'message': detail})
        self.assertIn('蒙版格式错误',sanitized)
        for secret in ('test-secret-value', 'bearer-secret-value', 'url-secret-value',
                       'example.invalid', 'customer.png', 'Traceback', 'secret.py'):
            self.assertNotIn(secret,sanitized)
        self.assertLessEqual(len(materials._studio_failure_detail({'message':'x'*10000})),500)
        self.assertEqual(materials._studio_failure_detail({'message':{'error':'not plain text'}}),'')

    def test_protected_evidence_unknown_mode_and_cross_user_block_before_config(self):
        foreign=self.root/'video'/'8'/'core.png';foreign.parent.mkdir(parents=True);Image.new('RGB',(20,20)).save(foreign)
        with patch.object(materials.studio,'config_for') as config:
            for kw in ({'protected_evidence':True},{'mode':'unknown'},{'mode':'matte','reference_paths':[]},
                       {'reference_paths':[foreign]},{'output_dir':self.root/'escape'},{'name':'../bad'}):
                with self.subTest(kw=kw),self.assertRaises(materials.MaterialError):self.call(**kw)
            config.assert_not_called()

    def test_failure_does_not_retry(self):
        def failure(user,path,record,config,client):
            record.update(status='failed');materials.studio.save(path,record)
        with patch.object(materials.studio,'config_for',return_value=({},None)), \
             patch.object(materials.studio,'enqueue',side_effect=failure) as enqueue:
            with self.assertRaises(materials.MaterialError) as result:self.call()
            self.assertEqual(enqueue.call_count,1)
            self.assertTrue(result.exception.studio_task_id)

    def test_cancel_before_submission_does_not_configure_or_enqueue(self):
        with patch.object(materials.studio,'config_for') as config:
            with self.assertRaises(materials.MaterialWaitStopped):self.call(cancel_check=lambda:True)
            config.assert_not_called()

    def test_cancel_wait_leaves_running_job_inspectable(self):
        calls=iter([False,False,True])
        def running(user,path,record,config,client):
            record.update(status='running');materials.studio.save(path,record)
        with patch.object(materials.studio,'config_for',return_value=({},None)), \
             patch.object(materials.studio,'enqueue',side_effect=running):
            with self.assertRaises(materials.MaterialWaitStopped) as err:self.call(cancel_check=lambda:next(calls))
        file=materials.studio.folder(7,err.exception.studio_task_id)/'record.json'
        self.assertEqual(json.loads(file.read_text(encoding='utf-8'))['status'],'running')

    def test_timeout_removes_only_not_dispatched_queue_entry(self):
        def queued(user,path,record,config,client):
            record.update(status='queued');materials.studio.save(path,record)
            materials.studio.ACTIVE.add((user,record['id']))
            materials.studio.QUEUED.append((user,path,record,config,client,'lane',1))
        with patch.object(materials.studio,'config_for',return_value=({},None)), \
             patch.object(materials.studio,'enqueue',side_effect=queued):
            with self.assertRaises(materials.MaterialError) as err:self.call()
        file=materials.studio.folder(7,err.exception.studio_task_id)/'record.json'
        self.assertEqual(json.loads(file.read_text(encoding='utf-8'))['status'],'cancelled')
        self.assertFalse(materials.studio.QUEUED)
        self.assertFalse(materials.studio.ACTIVE)


if __name__=='__main__': unittest.main()
