import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch
import requests
import json

from backend.app.runninghub_workflow_video import WorkflowConfig, RunningHubWorkflowVideoProvider
from module6_dynamic_video import VideoGenerationRequest, DynamicVideoTaskFailed, DynamicVideoTaskUnknown


def config():
    return dict(workflow_id='2108955980238266370', image_nodes=[{'node_id':'51','field':'image'}],
                prompt_node={'node_id':'263','field':'text'}, duration_node={'node_id':'259','field':'value'},
                output_nodes=['214','264'], preferred_output='214')


def response(body, status=200):
    result = Mock(status_code=status, ok=status < 400)
    result.json.return_value = body
    return result


class WorkflowTests(unittest.TestCase):
    def provider(self, *responses):
        session = Mock()
        session.post.side_effect = responses
        return RunningHubWorkflowVideoProvider('secret-test', workflow=config(),
                                              base_url='https://www.runninghub.ai', session=session)

    def test_filename_mapping_output_flags_and_submission_boundary(self):
        provider = self.provider(response({'code':0,'data':{'fileName':'openapi/image.jpg'}}),
                                 response({'taskId':'123','status':'RUNNING'}))
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'image.jpg'
            image.write_bytes(b'image')
            request = VideoGenerationRequest('reviewed prompt',(image,),Path(folder)/'out.mp4',6,'16:9')
            boundary = Mock()
            self.assertEqual(provider.submit(request,before_submit=boundary)['task_id'],'123')
            boundary.assert_called_once()
        calls = provider.session.post.call_args_list
        self.assertTrue(calls[1].args[0].endswith('/run/workflow/2108955980238266370'))
        rows = calls[1].kwargs['json']['nodeInfoList']
        self.assertEqual(rows[0]['fieldValue'],'openapi/image.jpg')
        self.assertEqual(rows[2]['fieldValue'],6)
        self.assertEqual([r['fieldValue'] for r in rows[-2:]],[True,True])

    def test_no_url_or_data_uri_fallback_on_upload_failure(self):
        provider = self.provider(response({'code':0,'data':{'download_url':'https://cdn/x.jpg'}}))
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'x.jpg'; image.write_bytes(b'image')
            with self.assertRaises(DynamicVideoTaskFailed): provider.upload_image(image)
        self.assertEqual(provider.session.post.call_count,1)

    def test_select_final_output_not_preview(self):
        provider = self.provider(response({'status':'SUCCESS','results':[
            {'nodeId':'264','outputType':'mp4','url':'https://cdn/preview.mp4'},
            {'nodeId':'214','outputType':'mp4','url':'https://cdn/final.mp4'}]}))
        self.assertEqual(provider.query('123')['result_url'],'https://cdn/final.mp4')

    def test_missing_final_output_keeps_identity(self):
        provider = self.provider(response({'status':'SUCCESS','results':[]}))
        with self.assertRaisesRegex(DynamicVideoTaskUnknown,'save_output'): provider.query('123')

    def test_mapping_conflicts_and_incomplete_config(self):
        invalid = config(); invalid['overrides']=[{'node_id':'214','field':'save_output','value':False}]
        with self.assertRaises(ValueError): WorkflowConfig.model_validate(invalid)
        with self.assertRaises(ValueError): WorkflowConfig().require_ready()

    def test_workflow_identity_includes_mapping(self):
        first = self.provider()
        other = config(); other['duration_node']['node_id']='260'
        second = RunningHubWorkflowVideoProvider('secret-test',workflow=other)
        self.assertNotEqual(first.identity['workflow_fingerprint'],second.identity['workflow_fingerprint'])

    def test_lost_submission_response_never_resubmits(self):
        provider = self.provider(requests.Timeout('lost response'))
        provider.upload_image = Mock(return_value='openapi/x.jpg')
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'x.jpg'; image.write_bytes(b'image')
            request=VideoGenerationRequest('prompt',(image,),Path(folder)/'out.mp4',6,'16:9')
            state=Path(folder)/'state.json'
            for _ in range(2):
                with self.assertRaises(DynamicVideoTaskUnknown): provider.run(request,state)
            self.assertEqual(provider.session.post.call_count,1)
            self.assertTrue(json.loads(state.read_text())['paid_submission_started'])

    def test_saved_workflow_config_roundtrip_and_public_key_privacy(self):
        from backend.app import video_model_config as settings
        values={'VIDEO_PROTOCOL':'runninghub_workflow','VIDEO_API_BASE_URL':'https://www.runninghub.ai',
                'VIDEO_API_KEY':'secret-test','VIDEO_RH_WORKFLOW':json.dumps(config())}
        with patch.object(settings,'_values',return_value=values):
            loaded=settings.load_config()
            self.assertEqual(loaded['workflow']['preferred_output'],'214')
            self.assertTrue(loaded['submit_path'].endswith('/2108955980238266370'))
            self.assertNotIn('secret-test',json.dumps(settings._public(loaded)))

    def test_explicit_rejection_and_task_id_precedence(self):
        for body, expected in [({'errorCode':1014,'errorMessage':'denied'}, DynamicVideoTaskFailed),
                               ({'status':'RUNNING'}, DynamicVideoTaskUnknown)]:
            provider = self.provider(response(body))
            with tempfile.TemporaryDirectory() as folder:
                image=Path(folder)/'x.jpg'; image.write_bytes(b'image')
                provider.upload_image=Mock(return_value='openapi/x.jpg')
                request=VideoGenerationRequest('prompt',(image,),Path(folder)/'out.mp4',6,'16:9')
                with self.assertRaises(expected): provider.submit(request)
        provider=self.provider(response({'taskId':'accepted'},500))
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'x.jpg'; image.write_bytes(b'image')
            provider.upload_image=Mock(return_value='openapi/x.jpg')
            request=VideoGenerationRequest('prompt',(image,),Path(folder)/'out.mp4',6,'16:9')
            self.assertEqual(provider.submit(request)['task_id'],'accepted')
