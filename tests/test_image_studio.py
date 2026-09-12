import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import requests
from backend.app import image_studio as studio


class ImageStudioTests(unittest.TestCase):
    def run_case(self, side_effect=None, results=None):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            record=dict(id='abc123',prompt='只提交我的原话',references=[],ratio='2:1',resolution='2k',status='running')
            session=MagicMock()
            session.__enter__.return_value=session
            from PIL import Image
            buffer=io.BytesIO()
            Image.new('RGB',(32,16)).save(buffer,format='PNG')
            download=MagicMock()
            download.content=buffer.getvalue()
            if side_effect:
                session.request.side_effect=side_effect
            else:
                responses=[]
                for result in results:
                    response=MagicMock()
                    response.json.return_value=result
                    responses.append(response)
                if results and str(results[-1].get('status','')).upper() == 'SUCCESS':
                    responses.append(download)
                session.request.side_effect=responses
            studio.ACTIVE.add(1)
            with patch.object(studio.requests,'Session',return_value=session):
                studio.execute(1,path,record,dict(endpoint='https://example.com/generate',query_url='https://example.com/query',api_key='secret',refresh_token='refresh',cloud_base_url='https://example.com',cloud_pool='1'))
            self.assertNotIn(1,studio.ACTIVE)
            stored=json.loads((path/'record.json').read_text(encoding='utf-8'))
            self.assertNotIn('secret',json.dumps(stored))
            return session,stored

    def test_submit_timeout_never_resubmits(self):
        session,record=self.run_case(side_effect=requests.Timeout('timeout'))
        self.assertEqual(session.request.call_count,1)
        self.assertEqual(record['status'],'failed')
        self.assertIn('尚未确认',record['message'])

    def test_terminal_failure_preserves_error_and_original_prompt(self):
        session,record=self.run_case(results=[{'taskId':'remote1'},{'status':'FAILED','errorCode':1501,'errorMessage':'blocked'}])
        self.assertEqual(session.request.call_count,2)
        payload=session.request.call_args_list[0].kwargs['json']
        self.assertEqual(payload['prompt'],'只提交我的原话')
        self.assertEqual(payload['clientJobId'],'image-studio-abc123')
        self.assertEqual(record['remote_task_id'],'remote1')
        self.assertIn('FAILED',record['message'])

    def test_paths_are_user_scoped_and_reject_traversal(self):
        self.assertNotEqual(studio.folder(1,'abc'),studio.folder(2,'abc'))
        with self.assertRaises(Exception):
            studio.folder(1,'../abc')

    def test_success_records_actual_dimensions(self):
        session,record=self.run_case(results=[{'taskId':'remote1'},{'status':'SUCCESS','results':[{'url':'https://example.com/image.png'}]}])
        self.assertEqual(record['status'],'completed')
        self.assertEqual((record['width'],record['height']),(32,16))
        self.assertEqual(session.request.call_count,3)

    def test_pool_runtime_keeps_refresh_token(self):
        client=MagicMock()
        client.image_pool_runtime.return_value={
            'base_url':'https://pool.example/api/v1',
            'access_token':'access-token',
            'refresh_token':'refresh-token',
        }
        with patch('backend.app.cloud_client.cloud_client_for',return_value=client):
            config, returned=studio.config_for(1,studio.ImageRequest(prompt='test',provider='pool'))
        self.assertIs(returned,client)
        self.assertEqual(config['refresh_token'],'refresh-token')
        self.assertEqual(config['endpoint'],'https://pool.example/api/v1/image-pool/generate')

    def test_pool_query_401_refreshes_and_keeps_original_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            record=dict(id='retry-safe',prompt='仅提交一次',references=[],ratio='2:1',resolution='2k',status='running')
            session=MagicMock()
            session.__enter__.return_value=session
            submit=MagicMock(status_code=200); submit.json.return_value={'taskId':'remote1'}
            expired=MagicMock(status_code=401); expired.close=MagicMock()
            query=MagicMock(status_code=200); query.json.return_value={'status':'SUCCESS','results':[{'url':'https://example.com/image.png'}]}
            from PIL import Image
            buffer=io.BytesIO(); Image.new('RGB',(32,16)).save(buffer,format='PNG')
            download=MagicMock(status_code=200); download.content=buffer.getvalue()
            session.request.side_effect=[submit,expired,query,download]
            refreshed=MagicMock(status_code=200); refreshed.json.return_value={'access_token':'new-access','refresh_token':'new-refresh','expires_in':900}
            client=MagicMock()
            studio.ACTIVE.add(1)
            config=dict(endpoint='https://example.com/generate',query_url='https://example.com/query',api_key='old-access',refresh_token='old-refresh',cloud_base_url='https://example.com',cloud_pool='1')
            with patch.object(studio.requests,'Session',return_value=session), patch('module4_video_render.requests.post',return_value=refreshed):
                studio.execute(1,path,record,config,client)
            self.assertEqual(record['status'],'completed',record.get('message'))
            self.assertEqual(session.request.call_count,4)
            self.assertEqual(session.request.call_args_list[0].kwargs['json']['clientJobId'],'image-studio-retry-safe')
            self.assertEqual(sum(1 for call in session.request.call_args_list if call.kwargs.get('json',{}).get('prompt') == '仅提交一次'),1)
            self.assertEqual(config['api_key'],'new-access')
            client.adopt_image_pool_runtime.assert_called_once()
