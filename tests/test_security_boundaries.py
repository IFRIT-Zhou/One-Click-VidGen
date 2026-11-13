import tempfile
import unittest
import ast
import re
from types import SimpleNamespace
from urllib.parse import urlparse
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app import auth, local_access, image_profiles


class SecurityBoundariesTests(unittest.TestCase):
    def test_language_destination_change_requires_new_key(self):
        from fastapi import HTTPException
        source=Path(__file__).resolve().parents[1]/'backend/app/main.py'
        node=next(n for n in ast.parse(source.read_text(encoding='utf-8-sig')).body if isinstance(n,ast.FunctionDef) and n.name=='save_api_key_settings')
        node.decorator_list=[]
        fields={n.attr:None for n in ast.walk(node) if isinstance(n,ast.Attribute) and isinstance(n.value,ast.Name) and n.value.id=='payload'}
        fields.update(language_provider='custom',language_api_base_url='https://new.example',image_api_keys=[])
        save=__import__('unittest.mock',fromlist=['Mock']).Mock()
        scope={'ApiKeySettingsRequest':object,'Request':object,'Any':object,'HTTPException':HTTPException,'require_user':lambda r:None,'urlparse':urlparse,'re':re,'PROJECT_ROOT':Path('.'),
               '_parse_env_lines':lambda p:{'CUSTOM_LLM_API_BASE':'https://old.example','CUSTOM_LLM_API_KEY':'fake'},
               '_runninghub_api_keys':lambda v:[], '_unique_api_keys':lambda v:[x for x in v if x],
               'LANGUAGE_PROVIDER_OPTIONS':{'custom':{'base_env':'CUSTOM_LLM_API_BASE','key_env':'CUSTOM_LLM_API_KEY'}},'save_project_env_values':save}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),scope)
        with self.assertRaisesRegex(HTTPException,'重新填写'):
            scope['save_api_key_settings'](SimpleNamespace(**fields),None)
        save.assert_not_called()

    def test_session_secret_generated_and_saved_without_using_fixed_default(self):
        with patch.object(auth, 'SESSION_SECRET', ''), patch('backend.app.config._parse_env_lines', return_value={}), patch('backend.app.config.save_project_env_values') as save:
            secret = auth.session_secret()
            self.assertGreaterEqual(len(secret), 48)
            self.assertEqual(auth.session_secret(), secret)
            save.assert_called_once_with({'SESSION_SECRET': secret})

    def test_existing_secret_preserved(self):
        with patch.object(auth, 'SESSION_SECRET', 'existing-private-value'), patch('backend.app.config.save_project_env_values') as save:
            self.assertEqual(auth.session_secret(), 'existing-private-value')
            save.assert_not_called()

    def test_local_origin_peer_and_media_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'clip.mp4').write_bytes(b'0123456789')
            (root/'record.json').write_text('{"private":true}')
            app=FastAPI()
            app.middleware('http')(local_access.local_request_boundary)
            app.mount('/workspace', local_access.WorkspaceMediaFiles(directory=directory))
            with patch.object(local_access, 'local_auth_enabled', return_value=True), patch.object(local_access, 'require_user', return_value={'id':1}):
                client=TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1',1234))
                self.assertEqual(client.get('/workspace/clip.mp4').status_code,200)
                response=client.get('/workspace/clip.mp4',headers={'Range':'bytes=2-5'})
                self.assertEqual(response.status_code,206)
                self.assertEqual(response.content,b'2345')
                self.assertEqual(client.get('/workspace/record.json').status_code,403)
                self.assertEqual(client.get('/workspace/clip.mp4',headers={'Origin':'https://evil.example'}).status_code,403)
                self.assertEqual(client.get('/workspace/clip.mp4',headers={'Host':'evil.example'}).status_code,403)
                remote=TestClient(app,base_url='http://127.0.0.1',client=('192.168.1.4',1234))
                self.assertEqual(remote.get('/workspace/clip.mp4').status_code,403)

    def test_image_snapshot_cannot_send_new_key_to_old_host(self):
        profile={'base_url':'https://new.example','model_id':'model','api_keys':['fake']}
        with patch.object(image_profiles,'profile_by_id',return_value=profile):
            with self.assertRaisesRegex(ValueError,'地址已改变'):
                image_profiles.profile_provider_configs({'id':'test','base_url':'https://old.example'})

    def test_image_endpoint_cannot_exfiltrate_key(self):
        payload=image_profiles.ImageProfileRequest(name='test',base_url='https://good.example',model_id='model',text_endpoint='https://evil.example/generate')
        with patch.object(image_profiles,'require_user'), patch.object(image_profiles,'_write_documents') as write:
            from fastapi import HTTPException
            with self.assertRaises(HTTPException):
                image_profiles.save_profile(payload,None)
            write.assert_not_called()
