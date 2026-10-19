import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from backend.app import cloud_video as c
from backend.app.cloud_client import CloudApiError

CAPS={'enabled':True,'workflow_revision':'h3-fast-linux-v1','model':'minimax-h3','resolutions':[{'width':640,'height':384},{'width':384,'height':640}], 'duration_seconds':{'min':2,'max':10}}
CAPS_SAFE={**CAPS,'resolutions':CAPS['resolutions']+[
    {'width':1280,'height':720},{'width':720,'height':1280},
    {'width':1664,'height':936},{'width':936,'height':1664}],
    'default_resolutions':{'landscape':{'width':1664,'height':936},'portrait':{'width':936,'height':1664}}}

class CloudVideoTests(unittest.TestCase):
    def test_expired_quote_renews_only_after_verified_miss_same_identity(self):
        client=Mock()
        client.create_video_job.side_effect=[CloudApiError('Quote expired; request a new quote',status_code=409),{'job_id':'new'}]
        client.video_job.side_effect=CloudApiError('missing',status_code=404)
        spec=dict(prompt='tree',reference_image_asset_id='asset',width=640,height=384,duration_seconds=2,seed=3)
        saved={'quote_id':'old','client_job_id':'stable','spec':spec}
        client.video_quote.return_value={'quote_id':'newquote','credits':'0','test_mode':True,'spec':spec}
        persist=Mock()
        self.assertEqual(c.create_or_renew(client,saved,persist),{'job_id':'new'})
        self.assertEqual([call.args for call in client.create_video_job.call_args_list],[('old','stable'),('newquote','stable')])
        persist.assert_called_once();self.assertEqual(saved['spec'],spec)

    def test_expired_quote_lookup_uncertain_never_renews(self):
        client=Mock();client.create_video_job.side_effect=CloudApiError('Quote expired; request a new quote',status_code=409)
        client.video_job.side_effect=CloudApiError('offline',status_code=503)
        with self.assertRaises(CloudApiError):c.create_or_renew(client,{'quote_id':'old','client_job_id':'stable'},Mock())
        client.video_quote.assert_not_called()

    def test_expired_quote_existing_job_returned_without_renewal(self):
        client=Mock();client.create_video_job.side_effect=CloudApiError('expired',status_code=410,code='QUOTE_EXPIRED')
        client.video_job.return_value={'job_id':'existing'}
        self.assertEqual(c.create_or_renew(client,{'quote_id':'old','client_job_id':'stable'},Mock()),{'job_id':'existing'})
        client.video_quote.assert_not_called()

    def test_unrelated_conflict_never_renews(self):
        client=Mock();client.create_video_job.side_effect=CloudApiError('identity conflict',status_code=409)
        with self.assertRaises(CloudApiError):c.create_or_renew(client,{'quote_id':'old','client_job_id':'stable'},Mock())
        client.video_job.assert_not_called();client.video_quote.assert_not_called()

    def test_capabilities_respect_orientation_and_no_downgrade(self):
        unsafe = {**CAPS, 'resolutions': [{'width': 1664, 'height': 937}]}
        with self.assertRaisesRegex(ValueError, '总像素上限'):
            c.dimensions_for(unsafe, {'resolution': 'custom', 'width': 1664, 'height': 937}, '16:9')
        with self.assertRaises(ValueError):
            c.dimensions_for(unsafe, {}, '16:9')
        self.assertEqual(c.dimensions_for(CAPS,{},'9:16')[1],(384,640))
        with self.assertRaises(ValueError):c.dimensions_for(CAPS,{'resolution':'720p'},'16:9')
        with self.assertRaises(ValueError):c.dimensions_for({**CAPS,'enabled':False},{},'16:9')

    def test_explicit_defaults_and_legacy_capabilities_select_largest_size(self):
        for ratio,expected in [('16:9',(1664,936)),('9:16',(936,1664))]:
            with self.subTest(ratio=ratio):
                self.assertEqual(c.dimensions_for(CAPS_SAFE,{},ratio)[1],expected)
                legacy={key:value for key,value in CAPS_SAFE.items() if key!='default_resolutions'}
                self.assertEqual(c.dimensions_for(legacy,{},ratio)[1],expected)
        # An explicit server default wins even when larger sizes are available.
        caps={**CAPS_SAFE,'default_resolutions':{'landscape':{'width':1280,'height':720}}}
        self.assertEqual(c.dimensions_for(caps,{},'16:9')[1],(1280,720))
        with self.assertRaisesRegex(ValueError,'默认尺寸'):
            c.dimensions_for(caps,{},'9:16')

    def test_explicit_saved_size_survives_new_default_and_unsupported_size_rejected(self):
        self.assertEqual(c.dimensions_for(CAPS_SAFE,{'resolution':'custom','width':640,'height':384},'16:9')[1],(640,384))
        self.assertEqual(c.dimensions_for(CAPS_SAFE,{'resolution':'720p'},'9:16')[1],(720,1280))
        for caps,options in [(CAPS,{'resolution':'custom','width':1664,'height':936}),
                             (CAPS_SAFE,{'resolution':'1080p'}),
                             ({**CAPS,'default_resolutions':CAPS_SAFE['default_resolutions']},{})]:
            with self.subTest(options=options),self.assertRaises(ValueError):
                c.dimensions_for(caps,options,'16:9')

    def test_duration_must_cover_timeline_and_fit_cloud_limits(self):
        for timeline,requested in [(1.2,2),(3,3),(9.9,10)]:
            c.validate_duration({'duration':timeline,'generation_duration':requested},CAPS)
        for timeline,requested in [(10.1,10),(11,11),(5,4),(0,4),(float('nan'),4),(float('inf'),4),(2,2.5),(2,'4')]:
            with self.subTest(timeline=timeline,requested=requested),self.assertRaises(ValueError):
                c.validate_duration({'duration':timeline,'generation_duration':requested},CAPS)
        with self.assertRaises(ValueError):
            c.validate_duration({'duration':8,'generation_duration':8},{'duration_seconds':{'min':2,'max':6}})
        with self.assertRaises(ValueError):
            c.validate_duration({'duration':16,'generation_duration':16},{'duration_seconds':{'min':2,'max':30}})

    def test_unknown_task_keeps_frozen_identity_and_dimensions_when_defaults_change(self):
        from backend.app import video_generation as vg
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        path=Path(temp.name)
        frozen={'backend':'cloud_h3','client_job_id':'stable-client','quote_id':'old-quote','width':640,'height':384,'workflow_revision':'h3-fast-linux-v1'}
        record={'id':'project','revision':1,'status':'video_review','logs':[],
                'settings':{'ratio':'16:9'},'creation_parameters':{'video_generation_backend':'cloud_h3'},
                'shots':[{'id':'s1','kind':'video','duration':12,'generation_duration':12,'video_prompt':'tree',
                          'image':'core.jpg','image_status':'completed','video_status':'unknown','video_terminal':False,'video_request':copy.deepcopy(frozen)}]}
        client=self.client();client.video_capabilities.return_value=CAPS_SAFE
        try:
            with patch.object(c,'cloud_client_for',return_value=client),patch.object(c.studio,'directory',return_value=path),patch.object(c.studio,'read',return_value=record),patch.object(c.studio,'save'),patch.object(vg,'_inputs'),patch.object(c.threading,'Thread') as thread:
                c.generate_cloud('project',vg.GenerateClips(revision=1,shot_ids=['s1']),1)
                resolved=thread.call_args.kwargs['args'][3]
                self.assertEqual(resolved['s1'][2],(640,384))
                self.assertEqual(record['shots'][0]['video_request'],frozen)
                client.video_quote.assert_not_called();client.create_video_job.assert_not_called()
        finally:
            c.studio.ACTIVE.discard(str(path));c.studio.CANCEL_EVENTS.pop(str(path),None)

    def test_oversized_timeline_fails_preflight_before_queuing_or_uploading(self):
        from backend.app import video_generation as vg
        from fastapi import HTTPException
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        path=Path(temp.name)
        record={'id':'project','revision':1,'status':'video_review','logs':[],
                'settings':{'ratio':'16:9'},'creation_parameters':{'video_generation_backend':'cloud_h3'},
                'shots':[{'id':'s1','kind':'video','duration':12,'generation_duration':10,'video_prompt':'tree',
                          'image':'core.jpg','image_status':'completed','video_status':'pending'}]}
        before=copy.deepcopy(record)
        client=self.client();client.video_capabilities.return_value=CAPS_SAFE
        with patch.object(c,'cloud_client_for',return_value=client),patch.object(c.studio,'directory',return_value=path),patch.object(c.studio,'read',return_value=record),patch.object(c.studio,'save') as save,patch.object(vg,'_inputs'),patch.object(c.threading,'Thread') as thread:
            with self.assertRaises(HTTPException) as error:
                c.generate_cloud('project',vg.GenerateClips(revision=1,shot_ids=['s1']),1)
            self.assertEqual(error.exception.status_code,409)
            self.assertIn('拆分分镜',error.exception.detail)
            self.assertEqual(record,before)
            thread.assert_not_called();save.assert_not_called()
            client.video_asset.assert_not_called();client.video_quote.assert_not_called();client.create_video_job.assert_not_called()

    def run_worker(self, client, event=None, before=None, probe=None):
        from backend.app import video_generation as vg
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        path=Path(temp.name)
        shot={'id':'s1','kind':'video','video_backend':'cloud_h3','video_status':'unknown','video_terminal':False,'video_task_id':'','video':'assets/videos/clip.mp4','video_request':{'backend':'cloud_h3','client_job_id':'stable-client','quote_id':'quote-original','width':640,'height':384}}
        if before:before(shot)
        record={'revision':1,'shots':[shot],'logs':[]}
        def read(_):return copy.deepcopy(record)
        def save(_,value):record.clear();record.update(copy.deepcopy(value))
        with patch.object(c,'cloud_client_for',return_value=client),patch.object(c.studio,'read',side_effect=read),patch.object(c.studio,'save',side_effect=save),patch.object(c.time,'sleep'),patch('backend.app.pipeline.probe_media_duration',side_effect=probe or (lambda _:2)):
            c._worker(path,1,['s1'],{'s1':({},CAPS,(640,384))},event or threading.Event())
        return record

    def client(self):
        client=Mock();client.config.max_wait_seconds=10
        return client

    def test_lost_submit_resume_finds_existing_without_reposting(self):
        client=self.client();client.video_job.return_value={'job_id':'server-1','status':'failed','error':'fixture'}
        record=self.run_worker(client)
        client.create_video_job.assert_not_called();client.video_job.assert_called_once_with('stable-client',by_client=True)
        self.assertTrue(record['shots'][0]['video_terminal'])

    def test_lookup_outage_preserves_identity_and_never_posts(self):
        client=self.client();client.video_job.side_effect=CloudApiError('offline',status_code=503)
        record=self.run_worker(client)
        client.create_video_job.assert_not_called()
        self.assertEqual(record['shots'][0]['video_request']['client_job_id'],'stable-client')
        self.assertFalse(record['shots'][0]['video_terminal'])
        self.assertTrue(record['shots'][0]['video_resume_available'])
        self.assertIn('请继续查询原任务',record['shots'][0]['video_progress_message'])

    def test_restart_clears_stale_running_progress_without_submitting(self):
        from backend.app import video_generation as vg
        shot={'id':'s1','kind':'video','video_backend':'cloud_h3','video_status':'running',
              'video_task_id':'old-job','video_progress_message':'running'}
        record={'revision':1,'shots':[shot],'logs':[]}
        vg.recover_interrupted(Path('.'),record)
        self.assertEqual(shot['video_status'],'unknown')
        self.assertIn('尚未重新核对云端状态',shot['video_progress_message'])
        self.assertEqual(shot['video_task_id'],'old-job')
        self.assertTrue(shot['video_resume_available'])

    def test_resume_old_failed_job_exposes_failure_without_generation_or_download(self):
        client=self.client()
        client.video_job.return_value={'job_id':'old-job','status':'failed',
            'error':{'code':'EXECUTION_OUTCOME_UNKNOWN','message':'节点重启，执行结果无法确认'}}
        record=self.run_worker(client,before=lambda shot:shot.update(video_task_id='old-job',video_progress_message='running'))
        shot=record['shots'][0]
        self.assertEqual(shot['video_status'],'failed')
        self.assertEqual(shot['video_error'],'EXECUTION_OUTCOME_UNKNOWN：节点重启，执行结果无法确认')
        self.assertTrue(shot['video_terminal']);self.assertFalse(shot['video_resume_available'])
        client.video_job.assert_called_once_with('old-job')
        client.create_video_job.assert_not_called();client.download_to.assert_not_called()

    def test_invalid_download_is_removed_and_original_task_can_be_queried_again(self):
        for result in [0,float('nan'),RuntimeError('FFprobe 无法读取媒体时长')]:
            with self.subTest(result=result):
                client=self.client();client.video_job.return_value={'job_id':'old-job','status':'succeeded'}
                downloaded=[]
                def download(url,target,**kwargs):
                    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'invalid-video');downloaded.append(target)
                def probe(target):
                    if isinstance(result,Exception):raise result
                    return result
                client.download_to.side_effect=download
                record=self.run_worker(client,probe=probe)
                self.assertFalse(downloaded[0].exists())
                self.assertEqual(record['shots'][0]['video_status'],'unknown')
                self.assertEqual(record['shots'][0]['video_task_id'],'old-job')
                self.assertTrue(record['shots'][0]['video_resume_available'])
                client.create_video_job.assert_not_called()

    def test_missing_lookup_resubmits_same_quote_and_same_key(self):
        client=self.client();client.video_job.side_effect=CloudApiError('missing',status_code=404)
        client.create_video_job.return_value={'job_id':'server-2','status':'failed'}
        self.run_worker(client)
        client.create_video_job.assert_called_once_with('quote-original','stable-client')
        client.video_quote.assert_not_called()

    def test_cancel_waits_for_server_terminal(self):
        client=self.client();event=threading.Event()
        def lookup(*args,**kwargs):event.set();return {'job_id':'server-1','status':'running'}
        client.video_job.side_effect=lookup
        client.cancel_video_job.return_value={'job_id':'server-1','status':'cancelled'}
        record=self.run_worker(client,event)
        client.cancel_video_job.assert_called_once_with('server-1')
        self.assertEqual(record['shots'][0]['video_status'],'stopped')
        self.assertTrue(record['shots'][0]['video_terminal'])

    def test_completed_result_downloads_without_new_generation(self):
        client=self.client();client.video_job.return_value={'job_id':'server-1','status':'succeeded'}
        def download(url, target, **kwargs):
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'fixture-video')
        client.download_to.side_effect=download
        record=self.run_worker(client)
        self.assertEqual(record['shots'][0]['video_status'],'completed')
        client.create_video_job.assert_not_called()
        self.assertEqual(client.download_to.call_args.args[0],'/cloud/video/jobs/server-1/result')

    def test_cancelled_queued_shot_never_submits(self):
        client=self.client()
        self.run_worker(client,before=lambda shot:shot.update(video_queue_cancelled=True))
        client.video_job.assert_not_called();client.create_video_job.assert_not_called()

    def test_new_clip_freezes_uploads_quotes_submits_and_downloads(self):
        from backend.app import video_generation as vg
        from PIL import Image
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        path=Path(temp.name);Image.new('RGB',(32,32),'green').save(path/'core.jpg')
        record={'id':'project','revision':1,'status':'video_generation_ready','logs':[], 'references':[],
            'settings':{'ratio':'16:9'}, 'creation_parameters':{'video_generation_backend':'cloud_h3','language_route':'cloud','use_cloud_image_pool':False,'comfyui_h3_prompt_agent':True},
            'shots':[{'id':'s1','kind':'video','duration':1.2,'generation_duration':4,'video_prompt':'green tree',
                'image':'core.jpg','image_status':'completed','video_status':'pending'}]}
        client=self.client();client.video_capabilities.return_value=CAPS_SAFE
        client.video_job.side_effect=CloudApiError('missing',status_code=404)
        client.video_asset.return_value={'asset_id':'asset-fixture'}
        def quote(spec):return {'quote_id':'quote-frozen','credits':'0','test_mode':True,'spec':dict(spec,num_frames=107)}
        client.video_quote.side_effect=quote
        def submit(quote_id,client_id):
            saved=record['shots'][0]['video_request']
            self.assertEqual(saved['client_job_id'],client_id)
            self.assertEqual(saved['quote_id'],quote_id)
            self.assertGreaterEqual(saved['seed'],0);self.assertLess(saved['seed'],2**32)
            return {'job_id':'server-fixture','status':'succeeded'}
        client.create_video_job.side_effect=submit
        def download(url,target,**kwargs):target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'clip')
        client.download_to.side_effect=download
        def read(_):return copy.deepcopy(record)
        def save(_,value):record.clear();record.update(copy.deepcopy(value))
        from backend.app import language_routing, gemini_client
        client.image_pool_runtime.return_value={'base_url':'https://pool.example','access_token':'test-token'}
        def convert(*args,**kwargs):
            self.assertEqual(gemini_client.language_base_url(),'https://pool.example/model-pool/v1')
            return 'converted prompt','fixture-hash'
        with patch.object(language_routing,'cloud_client_for',return_value=client),patch.object(vg,'convert_for_h3',side_effect=convert) as converter,patch.object(c,'cloud_client_for',return_value=client),patch.object(c.studio,'read',side_effect=read),patch.object(c.studio,'save',side_effect=save),patch.object(c.studio,'directory',return_value=path),patch.object(vg,'_inputs',return_value=(path/'core.jpg',)),patch('backend.app.presenter_mode.audio_policy',return_value=(False,False)),patch.object(c.threading,'Thread') as thread,patch('backend.app.pipeline.probe_media_duration',return_value=4):
            c.generate_cloud('project',vg.GenerateClips(revision=1,shot_ids=['s1']),1)
            args=thread.call_args.kwargs['args'];c._worker(*args)
        self.assertEqual(record['shots'][0]['video_status'],'completed')
        converter.assert_called_once()
        self.assertEqual(client.video_quote.call_args.args[0]['duration_seconds'],4)
        self.assertEqual((client.video_quote.call_args.args[0]['width'],client.video_quote.call_args.args[0]['height']),(1664,936))
        self.assertEqual(record['shots'][0]['duration'],1.2)
        self.assertEqual(client.create_video_job.call_count,1)

    def test_nonzero_quote_does_not_submit(self):
        client=self.client();client.video_job.side_effect=CloudApiError('missing',status_code=404)
        client.video_asset.return_value={'asset_id':'asset'}
        client.video_quote.return_value={'quote_id':'paid','credits':'1','test_mode':True}
        def before(shot):
            saved=shot['video_request'];saved.pop('quote_id');saved.update(reference_files=['core.jpg'],prompt='test',duration=2,seed=7)
        record=self.run_worker(client,before=before)
        client.create_video_job.assert_not_called()
        self.assertIn('不再免费',record['error'])

if __name__=='__main__':unittest.main()
