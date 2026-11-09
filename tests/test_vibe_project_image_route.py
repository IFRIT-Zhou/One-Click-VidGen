"""Offline tests: Vibe images use the saved project route, not an implicit pool."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from backend.app import vibe_image_route as route, image_studio


class ProjectRouteTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.project=Path(temp.name)

    def save(self, parameters):
        (self.project/'record.json').write_text(json.dumps(dict(
            creation_parameters=parameters,settings={'ratio':'9:16'})),encoding='utf-8')

    def test_personal_snapshot_keeps_protocol_model_dimensions_and_no_cloud(self):
        self.save(dict(use_cloud_image_pool=False,image_profile_id='mine',
            image_profile_snapshot={'id':'mine','name':'我的接口','resolution':'4k'}))
        config=dict(protocol='openai_sync',endpoint='https://personal.invalid/generate',
            reference_endpoint='https://personal.invalid/edit',api_key='SECRET',model='my-image',
            size='1024x1536',resolution='4k',quality='high',request_parameters={'style':'natural'})
        with patch('backend.app.image_profiles.profile_provider_configs',return_value=[config]) as profiles, \
             patch('backend.app.cloud_client.cloud_client_for') as cloud:
            actual,summary=route.resolve(self.project,1)
        cloud.assert_not_called();profiles.assert_called_once()
        for key in config:self.assertEqual(actual[key],config[key])
        self.assertEqual(actual['ratio'],'9:16')
        self.assertEqual(summary['profile_id'],'mine')
        self.assertEqual(summary['provider'],'custom')
        self.assertIn('my-image',summary['label'])
        self.assertNotIn('SECRET',json.dumps(summary))
        self.assertNotIn('https://',json.dumps(summary))

    def test_both_pool_channels_follow_saved_project_options(self):
        client=Mock();client.image_pool_runtime.return_value=dict(
            base_url='https://pool.invalid',access_token='TOKEN')
        for method in ('running','ican'):
            with self.subTest(method=method):
                self.save(dict(use_cloud_image_pool=True,method=method,size='1152x2048',image_resolution='4k'))
                with patch('backend.app.cloud_client.cloud_client_for',return_value=client), \
                     patch('module4_video_render._provider_configs') as personal:
                    config,summary=route.resolve(self.project,1)
                personal.assert_not_called()
                self.assertEqual(config['method'],method)
                self.assertEqual(summary['provider'],'pool')
                self.assertEqual(summary['ratio'],'9:16')
                if method=='ican':self.assertEqual(config['size'],'1152x2048')
                else:self.assertEqual(config['resolution'],'4k')

    def test_personal_legacy_uses_saved_resolution_and_rejects_global_pool(self):
        self.save(dict(use_cloud_image_pool=False,image_resolution='4k'))
        base=dict(endpoint='https://personal.invalid/generate',api_key='SECRET',resolution='1k')
        with patch('module4_video_render._provider_configs',return_value=[base]):
            config,summary=route.resolve(self.project,1)
        self.assertEqual(config['resolution'],'4k')
        self.assertEqual(summary['resolution'],'4k')
        with patch('module4_video_render._provider_configs',return_value=[{**base,'cloud_pool':'1'}]):
            with self.assertRaisesRegex(ValueError,'冲突'):route.resolve(self.project,1)

    def test_broken_personal_profile_does_not_fall_back(self):
        self.save(dict(image_profile_snapshot={'id':'missing'}))
        with patch('backend.app.image_profiles.profile_provider_configs',side_effect=ValueError('missing')), \
             patch('backend.app.cloud_client.cloud_client_for') as cloud:
            with self.assertRaisesRegex(ValueError,'missing'):route.resolve(self.project,1)
        cloud.assert_not_called()

    def test_fingerprint_ignores_credentials_but_pins_model_endpoint_and_size(self):
        base=dict(api_key='one',endpoint='https://a.invalid',resolution='2k',model='image')
        self.assertEqual(route.fingerprint(base),route.fingerprint({**base,'api_key':'two','refresh_token':'new'}))
        for key,value in [('model','other'),('resolution','4k'),('endpoint','https://b.invalid')]:
            self.assertNotEqual(route.fingerprint(base),route.fingerprint({**base,key:value}))

    def test_sync_studio_uses_single_image_request_not_async_task_protocol(self):
        import base64
        config=dict(protocol='openai_sync',endpoint='https://personal.invalid/generate',
            reference_endpoint='https://personal.invalid/edit',api_key='SECRET',model='sync-image',
            size='1024x1536',quality='high')
        buffer=io.BytesIO();Image.new('RGB',(16,32),'blue').save(buffer,format='PNG')
        Image.new('RGB',(16,32)).save(self.project/'ref_1.png')
        record=dict(id='abc',prompt='保留背景',references=['ref_1.png'],ratio='9:16',resolution='4k')
        session=Mock();session.__enter__=Mock(return_value=session);session.__exit__=Mock(return_value=False)
        response=Mock(ok=True,status_code=200);response.json.return_value={'data':[{'b64_json':base64.b64encode(buffer.getvalue()).decode()}]}
        session.post.return_value=response
        with patch.object(image_studio.requests,'Session',return_value=session):
            image_studio.execute(1,self.project,record,config)
        self.assertEqual(record['status'],'completed',record)
        session.post.assert_called_once();session.request.assert_not_called()
        self.assertEqual(session.post.call_args.args[0],config['reference_endpoint'])
        self.assertEqual(session.post.call_args.kwargs['data']['model'],'sync-image')
        self.assertEqual(session.post.call_args.kwargs['data']['size'],'1024x1536')
        self.assertNotIn('remote_task_id',record)
        self.assertTrue((self.project/'result.png').is_file())

    def test_recovery_rejects_changed_endpoint_before_enqueue(self):
        config=dict(endpoint='https://old.invalid',api_key='old',ratio='16:9',resolution='2k')
        record=dict(id='abc',prompt='test',provider='custom',method='running',size='2560x1440',
            ratio='16:9',resolution='2k',references=[],remote_task_id='original',status='awaiting_result',
            image_route={'key':route.fingerprint(config)})
        with patch.object(image_studio,'ROOT',self.project), \
             patch.object(image_studio,'config_for',return_value=({**config,'endpoint':'https://new.invalid'},None)), \
             patch.object(image_studio,'enqueue') as submit:
            folder=image_studio.folder(1,'abc');folder.mkdir(parents=True);image_studio.save(folder,record)
            with self.assertRaises(image_studio.HTTPException) as failure:
                image_studio.resume_existing(1,'abc',query_only=True)
        self.assertEqual(failure.exception.status_code,409)
        submit.assert_not_called()

    def test_async_personal_profile_auto_size_does_not_break_resume(self):
        config=dict(protocol='async_task',endpoint='https://old.invalid',api_key='key',
                    size='auto',ratio='16:9',resolution='2k')
        record=dict(id='abc',prompt='test',provider='custom',method='running',size='auto',profile_id='mine',
            ratio='16:9',resolution='2k',references=[],remote_task_id='original',status='awaiting_result',
            image_route={'key':route.fingerprint(config)})
        with patch.object(image_studio,'ROOT',self.project), \
             patch.object(image_studio,'config_for',return_value=(config,None)), \
             patch.object(image_studio,'enqueue') as submit:
            folder=image_studio.folder(1,'abc');folder.mkdir(parents=True);image_studio.save(folder,record)
            result=image_studio.resume_existing(1,'abc',query_only=True)
        submit.assert_called_once()
        self.assertEqual(result['remote_task_id'],'original')


if __name__=='__main__':unittest.main()
