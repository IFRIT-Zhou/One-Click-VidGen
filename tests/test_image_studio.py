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
            session.get.return_value.content=buffer.getvalue()
            if side_effect:
                session.post.side_effect=side_effect
            else:
                responses=[]
                for result in results:
                    response=MagicMock()
                    response.json.return_value=result
                    responses.append(response)
                session.post.side_effect=responses
            studio.ACTIVE.add(1)
            with patch.object(studio.requests,'Session',return_value=session):
                studio.execute(1,path,record,dict(endpoint='https://example.com/generate',query_url='https://example.com/query',api_key='secret',cloud_pool='1'))
            self.assertNotIn(1,studio.ACTIVE)
            stored=json.loads((path/'record.json').read_text(encoding='utf-8'))
            self.assertNotIn('secret',json.dumps(stored))
            return session,stored

    def test_submit_timeout_never_resubmits(self):
        session,record=self.run_case(side_effect=requests.Timeout('timeout'))
        self.assertEqual(session.post.call_count,1)
        self.assertEqual(record['status'],'failed')
        self.assertIn('尚未确认',record['message'])

    def test_terminal_failure_preserves_error_and_original_prompt(self):
        session,record=self.run_case(results=[{'taskId':'remote1'},{'status':'FAILED','errorCode':1501,'errorMessage':'blocked'}])
        self.assertEqual(session.post.call_count,2)
        payload=session.post.call_args_list[0].kwargs['json']
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
        self.assertEqual(session.post.call_count,2)
