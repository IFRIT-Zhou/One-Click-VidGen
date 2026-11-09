"""Dedicated Vibe settings: no actual provider calls or production secrets."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app import vibe_config as api
from backend.app import config as env


class VibeConfigTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        root=Path(t.name);self.settings=root/'vibe.json';self.env=root/'.env'
        for p in [patch.object(api,'SETTINGS_PATH',self.settings),patch.object(api,'ENV_PATH',self.env),
                  patch.object(env,'ENV_PATH',self.env),patch.object(env,'ENV_EXAMPLE_PATH',root/'absent'),
                  patch.object(api,'require_user',side_effect=lambda r:{'id':r.headers.get('user','1')}),patch.dict('os.environ',{},clear=False)]:
            p.start();self.addCleanup(p.stop)
        app=FastAPI();app.include_router(api.router);self.client=TestClient(app);self.addCleanup(self.client.close)

    def save(self,user='1',**kw):
        return self.client.put('/api/vibe-model',json={'api_key':'test-secret','max_image_requests':4,**kw},headers={'user':user})

    def test_defaults_secret_storage_and_account_isolation(self):
        initial=self.client.get('/api/vibe-model').json()
        self.assertEqual(initial['model'],'deepseek-flash');self.assertFalse(initial['configured'])
        self.assertEqual(initial['effective_max_image_requests'],0)
        self.assertFalse(initial['image_generation_enabled'])
        self.assertEqual(initial['generation_policy'],'source_style_code_only')
        saved=self.save();self.assertEqual(saved.status_code,200,saved.text)
        self.assertNotIn('test-secret',saved.text);self.assertNotIn('test-secret',self.settings.read_text())
        self.assertEqual(api.get_config(1)['api_key'],'test-secret')
        self.assertFalse(self.client.get('/api/vibe-model',headers={'user':'2'}).json()['configured'])
        self.assertEqual(self.save(user='2',api_key='second').status_code,200)
        self.assertEqual(api.get_config(1)['api_key'],'test-secret');self.assertEqual(api.get_config(2)['api_key'],'second')

    def test_retain_clear_key_and_prevent_cross_provider_leak(self):
        self.save()
        self.assertEqual(self.save(api_key='',max_image_requests=8).status_code,200)
        self.assertEqual(api.get_config(1)['api_key'],'test-secret')
        r=self.save(api_key=None,base_url='https://new.example.test/v1')
        self.assertEqual(r.status_code,422)
        self.assertEqual(api.get_config(1)['base_url'],'https://api.deepseek.com')
        self.save(api_key=None,clear_key=True)
        self.assertEqual(api.get_config(1)['api_key'],'')

    def test_invalid_settings_and_no_pool_fallback(self):
        for body in [{'max_image_requests':0},{'max_image_requests':13},{'max_image_requests':'4'},
                     {'base_url':'https://user:pass@example.test'},{'base_url':'http://example.test'},
                     {'model':'  '},{'api_key':'bad\nkey'},{'language_route':'cloud'}]:
            self.assertEqual(self.save(**body).status_code,422,str(body))
        self.env.write_text('LANGUAGE_PROVIDER=custom\nCUSTOM_API_KEY=not-vibe\nOCV_USE_CLOUD_IMAGE_POOL=1\n')
        self.assertEqual(api.get_config(1)['api_key'],'')

    def test_paid_vision_test_requires_confirmation_and_never_transmits_project(self):
        self.save()
        response=Mock(status_code=200);response.json.return_value={'choices':[{'message':{'content':'红色'}}],'usage':{'total_tokens':12}}
        with patch.object(api.requests,'post',return_value=response) as post:
            self.assertEqual(self.client.post('/api/vibe-model/test',json={'confirm_cloud':False}).status_code,422)
            post.assert_not_called()
            result=self.client.post('/api/vibe-model/test',json={'confirm_cloud':True})
            self.assertEqual(result.status_code,200,result.text)
            self.assertTrue(result.json()['vision']);self.assertNotIn('test-secret',result.text)
            kwargs=post.call_args.kwargs
            self.assertFalse(kwargs['allow_redirects'])
            self.assertEqual(kwargs['json']['model'],'deepseek-flash')
            self.assertEqual(kwargs['json']['messages'][0]['content'][0]['text'],'只回答这张图片的主要颜色，不要解释。')
            self.assertTrue(kwargs['json']['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))

    def test_provider_error_never_echoes_secret_and_no_retry(self):
        self.save()
        response=Mock(status_code=401,text='test-secret')
        with patch.object(api.requests,'post',return_value=response) as post:
            result=self.client.post('/api/vibe-model/test',json={'confirm_cloud':True})
            self.assertEqual(result.status_code,502);self.assertNotIn('test-secret',result.text)
            self.assertEqual(post.call_count,1)

    def test_provider_key_is_destination_bound_even_when_metadata_write_fails(self):
        self.save()
        with patch.object(Path,'replace',side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                api.save_config(1,api.Settings(base_url='https://other.example.test',api_key='other-secret'))
        self.assertEqual(api.get_config(1)['base_url'],'https://api.deepseek.com')
        self.assertEqual(api.get_config(1)['api_key'],'test-secret')
        self.assertEqual(self.save(base_url='https://other.example.test',api_key='other-secret').status_code,200)
        self.assertEqual(api.get_config(1)['api_key'],'other-secret')

    def test_corrupted_config_fails_explicitly_not_fallback(self):
        self.settings.write_text(json.dumps({'users':{'1':{'max_image_requests':99}}}))
        result=self.client.get('/api/vibe-model')
        self.assertEqual(result.status_code,409)
        self.assertIn('重新保存',result.text)


if __name__=='__main__':unittest.main()
