import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch
import requests
import json

from backend.app.runninghub_workflow_video import WorkflowConfig, RunningHubWorkflowVideoProvider, aiwood_workflow_preset
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
    def test_aiwood_preset_submits_owner_graph_nodes(self):
        provider = RunningHubWorkflowVideoProvider('secret-test', workflow=aiwood_workflow_preset(), session=Mock())
        provider.session.post.return_value=response({'taskId':'owner-task'})
        provider.upload_image=Mock(return_value='openapi/new-core.jpg')
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'x.jpg'; image.write_bytes(b'image')
            provider.submit(VideoGenerationRequest('new prompt',(image,),Path(folder)/'out.mp4',8,'16:9'))
        rows={(r['nodeId'],r['fieldName']):r['fieldValue'] for r in provider.session.post.call_args.kwargs['json']['nodeInfoList']}
        self.assertEqual(rows[('150','image')],'openapi/new-core.jpg')
        self.assertEqual(rows[('232','value')],'new prompt')
        self.assertEqual(rows[('132','value')],8)
        self.assertIs(rows[('180','save_output')],True)
        self.assertNotIn(('238','audio'),rows)

    def provider(self, *responses):
        session = Mock()
        session.post.side_effect = responses
        return RunningHubWorkflowVideoProvider('secret-test', workflow=config(),
                                              base_url='https://www.runninghub.ai', session=session)

    def test_filename_mapping_output_flags_and_submission_boundary(self):
        provider = self.provider(response({'code':200,'message':'success','data':{
                                     'filename':'openapi/image.jpg', 'download_url':'https://cdn/image.jpg'}}),
                                 response({'taskId':'123','status':'RUNNING'}))
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'image.jpg'
            image.write_bytes(b'image')
            request = VideoGenerationRequest('reviewed prompt',(image,),Path(folder)/'out.mp4',6,'16:9')
            boundary = Mock()
            self.assertEqual(provider.submit(request,before_submit=boundary)['task_id'],'123')
            boundary.assert_called_once()
        calls = provider.session.post.call_args_list
        self.assertTrue(calls[0].args[0].endswith('/openapi/v2/media/upload/binary'))
        self.assertEqual(calls[0].kwargs['headers'], {'Authorization':'Bearer secret-test'})
        self.assertEqual(calls[0].kwargs['data'], {})
        self.assertTrue(calls[1].args[0].endswith('/run/workflow/2108955980238266370'))
        rows = calls[1].kwargs['json']['nodeInfoList']
        self.assertEqual(rows[0]['fieldValue'],'openapi/image.jpg')
        self.assertEqual(rows[2]['fieldValue'],6)
        self.assertEqual([r['fieldValue'] for r in rows[-2:]],[True,True])

    def test_official_upload_filename_spellings(self):
        for field, code in [('fileName', 0), ('filename', 200), ('fileName', '0'), ('filename', '200')]:
            with self.subTest(field=field, code=code), tempfile.TemporaryDirectory() as folder:
                upload = response({'code':code, 'data':{field:'openapi/image.png'}})
                provider = self.provider(upload)
                image = Path(folder)/'x.png'; image.write_bytes(b'image')
                self.assertEqual(provider.upload_image(image), 'openapi/image.png')
                upload.close.assert_called_once()
                self.assertTrue(provider.session.post.call_args.kwargs['files']['file'][1].closed)

    def test_explicit_legacy_endpoint_uses_documented_multipart_auth(self):
        provider = RunningHubWorkflowVideoProvider('secret-test', workflow=config(),
                    session=Mock(), upload_path='/task/openapi/upload')
        provider.session.post.return_value = response({'code':0, 'data':{'fileName':'api/image.png'}})
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'x.png'; image.write_bytes(b'image')
            self.assertEqual(provider.upload_image(image), 'api/image.png')
        self.assertEqual(provider.session.post.call_args.kwargs['data'],
                         {'apiKey':'secret-test', 'fileType':'input'})

    def test_http_200_business_rejection_is_reported_and_never_submits(self):
        upload = response({'code':1014, 'msg':'APIKEY_USER_NOT_FOUND secret-test '
                           'https://cdn/image?token=private token=another-secret',
                           'data':{'filename':'openapi/image.jpg'}})
        provider = self.provider(upload)
        boundary = Mock()
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'x.jpg'; image.write_bytes(b'image')
            request = VideoGenerationRequest('prompt',(image,),Path(folder)/'out.mp4',6,'16:9')
            with self.assertRaises(DynamicVideoTaskFailed) as raised:
                provider.submit(request, before_submit=boundary)
        message = str(raised.exception)
        self.assertIn('1014', message)
        self.assertIn('APIKEY_USER_NOT_FOUND', message)
        self.assertIn('尚未提交视频任务', message)
        for private in ('secret-test','another-secret','https://cdn','token=private'):
            self.assertNotIn(private, message)
        self.assertNotIn('未返回', message)
        boundary.assert_not_called()
        self.assertEqual(provider.session.post.call_count, 1)
        upload.close.assert_called_once()

    def test_upload_rejections_and_invalid_responses_never_cross_submission_boundary(self):
        cases = [(200, []), (200, None), (200, {'code':0,'errorCode':1014}),
                 (200, {'code':0,'status':'FAILED','data':{'filename':'openapi/x.jpg'}}),
                 (403, {'code':0,'data':{'filename':'openapi/x.jpg'}}),
                 (302, {'code':0,'data':{'filename':'openapi/x.jpg'}})]
        for status, body in cases:
            with self.subTest(status=status, body=body), tempfile.TemporaryDirectory() as folder:
                provider = self.provider(response(body,status))
                image = Path(folder)/'x.jpg'; image.write_bytes(b'image')
                boundary = Mock()
                request = VideoGenerationRequest('prompt',(image,),Path(folder)/'out.mp4',6,'16:9')
                with self.assertRaisesRegex(DynamicVideoTaskFailed,'尚未提交视频任务'):
                    provider.submit(request,before_submit=boundary)
                boundary.assert_not_called()
                self.assertEqual(provider.session.post.call_count,1)

    def test_invalid_json_upload_is_definite_pre_submission_failure(self):
        upload = response(None)
        upload.json.side_effect = ValueError('not JSON')
        provider = self.provider(upload)
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'x.jpg'; image.write_bytes(b'image')
            with self.assertRaisesRegex(DynamicVideoTaskFailed,'HTTP 200.*不是 JSON'):
                provider.upload_image(image)
        upload.close.assert_called_once()

    def test_pre_submission_upload_failure_does_not_retry_or_mark_paid(self):
        provider = self.provider(response({'code':401,'message':'denied'}))
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'x.jpg'; image.write_bytes(b'image')
            request = VideoGenerationRequest('prompt',(image,),Path(folder)/'out.mp4',6,'16:9')
            state = Path(folder)/'state.json'
            for _ in range(2):
                with self.assertRaises(DynamicVideoTaskFailed):
                    provider.run(request,state)
            saved = json.loads(state.read_text(encoding='utf-8'))
            self.assertFalse(saved['paid_submission_started'])
            self.assertFalse(saved['submit_response_lost'])
            self.assertTrue(saved['terminal'])
            self.assertNotIn('task_id',saved)
        self.assertEqual(provider.session.post.call_count,1)

    def test_explicit_upload_retry_submits_only_after_upload_success(self):
        provider = self.provider(requests.Timeout('upload interrupted'),
                                 response({'code':200,'data':{'filename':'openapi/x.jpg'}}),
                                 response({'taskId':'retry-task','status':'RUNNING'}))
        boundary = Mock()
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'x.jpg'; image.write_bytes(b'image')
            request = VideoGenerationRequest('prompt',(image,),Path(folder)/'out.mp4',6,'16:9')
            with self.assertRaises(DynamicVideoTaskFailed):
                provider.submit(request,before_submit=boundary)
            boundary.assert_not_called()
            self.assertEqual(provider.session.post.call_count,1)
            self.assertEqual(provider.submit(request,before_submit=boundary)['task_id'],'retry-task')
        boundary.assert_called_once()
        self.assertEqual(sum('/run/workflow/' in call.args[0]
                             for call in provider.session.post.call_args_list),1)

    def test_no_url_or_data_uri_fallback_on_upload_failure(self):
        provider = self.provider(response({'code':0,'data':{'download_url':'https://cdn/x.jpg'}}))
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'x.jpg'; image.write_bytes(b'image')
            with self.assertRaises(DynamicVideoTaskFailed): provider.upload_image(image)
        self.assertEqual(provider.session.post.call_count,1)

    def test_filename_field_must_be_relative_and_unambiguous(self):
        invalid = ['https://cdn/x.jpg','data:image/png;base64,AAAA','//cdn/x.jpg',
                   '../x.jpg','openapi/../x.jpg','C:\\private\\x.jpg','x.jpg?token=secret',
                   123, ['openapi/x.jpg'], 'openapi/x\n.jpg']
        for field in ('fileName','filename'):
            for value in invalid:
                with self.subTest(field=field,value=value), tempfile.TemporaryDirectory() as folder:
                    provider = self.provider(response({'code':200,'data':{field:value}}))
                    image = Path(folder)/'x.jpg'; image.write_bytes(b'image')
                    with self.assertRaisesRegex(DynamicVideoTaskFailed,'有效的工作流文件名'):
                        provider.upload_image(image)
                    self.assertEqual(provider.session.post.call_count,1)
        provider = self.provider(response({'code':200,'data':{
            'fileName':'openapi/one.jpg', 'filename':'openapi/two.jpg'}}))
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder)/'x.jpg'; image.write_bytes(b'image')
            with self.assertRaises(DynamicVideoTaskFailed): provider.upload_image(image)

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
