"""Background reuse is local, independently durable, and input/route bound."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

from backend.app import vibe_cloud as api, vibe_layered as layered


class LayeredBackgroundCacheTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project=Path(temporary.name).resolve()/'project'
        self.project.mkdir()
        self.core=self.project/'core.png'
        Image.new('RGB',(80,45),'#193B50').save(self.core)
        self.sources={'core':self.core}
        self.context={'canvas':[80,45]}
        self.route={'key':'a'*64,'label':'个人 API'}
        self.config={'layered_owner_id':1,'layered_image_route':self.route,
                     'layered_image_config':{'model':'offline-model','api_key':'not-written'}}
        self.event=threading.Event()
        self.design=layered.Design(background='extract',background_color='#193B50',
                                   subjects=[],labels=[])
        self.counter=0
        self.current=self.job()
        self.mats=api._job(self.project,self.current).parent/'materials'
        self.mats.mkdir()
        generate=patch.object(api,'generate_asset',side_effect=self.generate)
        self.generated=generate.start();self.addCleanup(generate.stop)
        resume=patch.object(layered,'resume_asset',side_effect=self.resume)
        self.resumed=resume.start();self.addCleanup(resume.stop)

    def job(self,**changes):
        self.counter+=1
        identity=f'{self.counter:032x}'
        folder=api._root(self.project)/identity
        folder.mkdir()
        task=dict(task_id=identity,shot_id='shot1',template_mode='layered',
                  status='running',stage='materials',cloud_input_stamp='b'*64,
                  image_route=copy.deepcopy(self.route),
                  usage={'image_requests':0,'image_task_ids':[]})
        task.update(changes)
        api._write(folder/'task.json',task)
        return identity

    def generated_file(self,output,name):
        target=output/(name+'.png')
        Image.new('RGB',(80,45),'#365768').save(target)
        return {'path':target,'studio_task_id':'c'*32}

    def generate(self,user,prompt,refs,output,name,cancelled,**kwargs):
        self.assertEqual(user,1)
        self.assertFalse(cancelled())
        self.assertEqual(kwargs['image_route'],self.route)
        kwargs['on_submitted']('c'*32)
        return self.generated_file(output,name)

    def resume(self,user,identity,output,name,cancelled):
        self.assertEqual(identity,'c'*32)
        self.assertFalse(cancelled())
        return self.generated_file(output,name)

    def cache(self,*,manifest=True,status='failed',background='extract',**task_changes):
        identity=self.job(status=status,stage='poster_review',
            usage={'image_requests':1,'image_task_ids':['c'*32]},**task_changes)
        folder=api._job(self.project,identity).parent
        mats=folder/'materials';mats.mkdir()
        source=self.generated_file(mats,'background')['path']
        api._write(folder/'layout.json',{'background':background})
        if manifest:
            layered._save_background_ready(self.project,identity,source,self.sources,
                self.route,image_task_id='c'*32,recipe=layered._background_recipe(self.design))
        return identity,source

    def background(self,budget=1,resume=False,config=None):
        return layered._background(self.project,self.current,self.design,self.context,
            self.sources,self.mats,config or self.config,self.event,resume,budget=budget)

    def ready(self,identity=None):
        return api.previews._read_json(api._job(self.project,identity or self.current).parent/
                                      'background_ready.json',64*1024)

    def test_background_ready_persists_before_later_workflow_success(self):
        source=self.background()
        self.assertEqual(self.generated.call_count,1)
        task=api._read_job(self.project,self.current)
        self.assertEqual(task['status'],'running')
        self.assertEqual(task['usage']['image_requests'],1)
        ready=self.ready()
        self.assertEqual(ready['kind'],'extracted')
        self.assertEqual(ready['sha256'],api.previews._stream_hash(source,1024*1024))
        self.assertEqual(ready['image_route_key'],self.route['key'])
        self.assertNotIn('not-written',json.dumps(ready))

    def test_failed_job_background_reuses_without_new_request_even_with_zero_budget(self):
        identity,source=self.cache(status='failed')
        result=self.background(budget=0)
        self.assertEqual(result.read_bytes(),source.read_bytes())
        self.assertNotEqual(result,source)
        self.generated.assert_not_called();self.resumed.assert_not_called()
        self.assertEqual(api._read_job(self.project,self.current)['usage']['image_requests'],0)
        self.assertEqual(api._read_job(self.project,self.current)['background_reused_from'],identity)
        self.assertEqual(self.ready()['origin_task_id'],identity)

    def test_reused_background_remains_a_durable_cache_without_claiming_new_charge(self):
        origin,_=self.cache()
        self.background(budget=0)
        reused=self.current
        api._update(api._job(self.project,reused),status='failed',stage='motion')
        # Remove the original material; the independent copied artifact survives.
        (api._job(self.project,origin).parent/'materials'/'background.png').unlink()
        self.current=self.job()
        self.mats=api._job(self.project,self.current).parent/'materials';self.mats.mkdir()
        self.background(budget=0)
        self.assertEqual(self.ready()['origin_task_id'],origin)
        self.assertEqual(api._read_job(self.project,self.current)['background_reused_from'],reused)
        self.generated.assert_not_called()

    def test_legacy_extraction_file_cannot_establish_new_deletion_recipe(self):
        self.cache(manifest=False,status='failed')
        with self.assertRaisesRegex(ValueError,'删除/保留规则一致'):
            self.background(budget=0)
        self.generated.assert_not_called()

    def test_legacy_solid_background_never_masquerades_as_extracted_background(self):
        self.cache(manifest=False,status='completed',background='solid')
        self.background()
        self.assertEqual(self.generated.call_count,1)

    def test_legacy_reference_background_never_masquerades_as_extraction(self):
        self.cache(manifest=False,status='completed',background='ref_01')
        self.background()
        self.assertEqual(self.generated.call_count,1)

    def test_legacy_file_requires_paid_extraction_provenance(self):
        identity,_=self.cache(manifest=False)
        api._update(api._job(self.project,identity),usage={'image_requests':0,'image_task_ids':[]})
        self.background()
        self.assertEqual(self.generated.call_count,1)

    def test_cache_does_not_cross_shot_input_route_or_core_hash(self):
        for field,value in [('shot_id','shot2'),('cloud_input_stamp','d'*64),
                            ('image_route',{'key':'e'*64})]:
            with self.subTest(field=field):
                identity,_=self.cache()
                api._update(api._job(self.project,identity),**{field:value})
        self.cache()
        Image.new('RGB',(80,45),'#ABCDEF').save(self.core)
        self.background()
        self.assertEqual(self.generated.call_count,1)

    def test_hash_mismatch_does_not_fall_back_to_legacy_file(self):
        _,source=self.cache()
        Image.new('RGB',(80,45),'#ABCDEF').save(source)
        self.background()
        self.assertEqual(self.generated.call_count,1)

    def test_manifest_cannot_point_outside_its_own_task(self):
        identity,_=self.cache()
        ready=self.ready(identity)
        ready['source_file']=self.core.relative_to(self.project).as_posix()
        ready['sha256']=api.previews._stream_hash(self.core,1024*1024)
        api._write(api._job(self.project,identity).parent/'background_ready.json',ready)
        self.background()
        self.assertEqual(self.generated.call_count,1)

    def test_corrupt_missing_and_malformed_caches_do_not_abort_fresh_request(self):
        identity,source=self.cache()
        source.write_bytes(b'not-an-image')
        identity,_=self.cache()
        (api._job(self.project,identity).parent/'background_ready.json').write_text('{bad',encoding='utf8')
        identity,source=self.cache()
        source.unlink()
        identity=self.job(image_route=[])
        (api._job(self.project,identity).parent/'layout.json').write_text('null',encoding='utf8')
        identity=self.job()
        api._job(self.project,identity).write_text('{bad',encoding='utf8')
        self.background()
        self.assertEqual(self.generated.call_count,1)

    def test_zero_budget_without_cache_fails_before_provider_request(self):
        with self.assertRaisesRegex(ValueError,'请允许一次'):
            self.background(budget=0)
        self.generated.assert_not_called();self.resumed.assert_not_called()

    def test_missing_route_cannot_reuse_an_old_route(self):
        self.cache()
        api._update(api._job(self.project,self.current),image_route=None)
        with self.assertRaisesRegex(ValueError,'请允许一次'):
            self.background(budget=0,config={'layered_owner_id':1})
        self.generated.assert_not_called()

    def test_mismatched_frozen_route_fails_before_provider_request(self):
        config={**self.config,'layered_image_route':{'key':'f'*64}}
        with self.assertRaisesRegex(ValueError,'渠道与任务记录不一致'):
            self.background(config=config)
        self.generated.assert_not_called();self.resumed.assert_not_called()

    def test_explicit_resume_only_fetches_original_and_persists_ready(self):
        api._update(api._job(self.project,self.current),
                    usage={'image_requests':1,'image_task_ids':['c'*32]})
        self.background(resume=True,config={'layered_owner_id':1})
        self.generated.assert_not_called();self.assertEqual(self.resumed.call_count,1)
        self.assertEqual(self.ready()['image_task_id'],'c'*32)
        self.assertEqual(self.ready()['image_route_key'],self.route['key'])

    def test_cancelled_task_never_copies_or_submits(self):
        self.cache()
        self.event.set()
        with self.assertRaises(InterruptedError):self.background()
        self.generated.assert_not_called();self.resumed.assert_not_called()
        self.assertFalse((self.mats/'background.png').exists())


if __name__=='__main__':unittest.main()
