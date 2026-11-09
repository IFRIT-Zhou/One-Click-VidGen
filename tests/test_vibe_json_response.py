"""Distinguish completed malformed answers from unsafe-to-retry responses."""
import json
import unittest
from unittest.mock import MagicMock, patch

from backend.app import vibe_cloud_client as client


class ModelJsonResponseTests(unittest.TestCase):
    config = {'base_url': 'https://example.invalid/v1', 'api_key': 'offline-test-key',
              'model': 'deepseek-flash'}

    @staticmethod
    def stream(text, finish='stop', *, done=True, reasoning=''):
        response=MagicMock()
        response.status_code=200
        response.__enter__.return_value=response
        choice={'delta':{'content':text,'reasoning_content':reasoning}}
        if finish is not None:choice['finish_reason']=finish
        response.iter_lines.return_value=[b'data: '+json.dumps({'choices':[choice]}).encode()]
        if done:response.iter_lines.return_value.append(b'data: [DONE]')
        return response

    def invoke(self, response):
        with patch.object(client.requests,'post',return_value=response) as post:
            try:
                return client.chat_json(self.config,'offline JSON instruction',{}, {},lambda:False)
            finally:
                self.assertEqual(post.call_count,1,'The client must never retry by itself')

    def test_completed_malformed_answer_is_distinct_and_raw_text_is_private(self):
        raw=' {"subjects": [PRIVATE-FINAL-ANSWER] } '
        with self.assertRaises(client.ModelResponseFormatError) as caught:
            self.invoke(self.stream(raw,reasoning='PRIVATE-REASONING'))
        error=caught.exception
        self.assertIsInstance(error,client.CloudCallError)
        self.assertEqual(error.response_text,raw)
        self.assertEqual(error.usage['error_code'],'model_json_invalid')
        self.assertEqual(error.usage['finish_reason'],'stop')
        self.assertTrue(error.usage['stream_done'])
        self.assertEqual(error.usage['json_error_kind'],'syntax')
        self.assertGreaterEqual(error.usage['json_error_line'],1)
        self.assertGreaterEqual(error.usage['json_error_column'],1)
        public=str(error)+json.dumps(error.usage)
        for secret in ('PRIVATE-FINAL-ANSWER','PRIVATE-REASONING','offline-test-key'):
            self.assertNotIn(secret,public)
        self.assertNotIn('PRIVATE-REASONING',error.response_text)

    def test_complete_non_object_is_format_failure_not_silently_unwrapped(self):
        for raw in ('[]','[{"passed":true}]','"text"','null','42'):
            with self.subTest(raw=raw),self.assertRaises(client.ModelResponseFormatError) as caught:
                self.invoke(self.stream(raw))
            self.assertEqual(caught.exception.response_text,raw)
            self.assertEqual(caught.exception.usage['json_error_kind'],'top_level_not_object')

    def test_missing_finish_is_not_repairable_even_with_done_marker(self):
        for done in (True,False):
            with self.subTest(done=done),self.assertRaises(client.CloudCallError) as caught:
                self.invoke(self.stream('{"subjects":',finish=None,done=done))
            error=caught.exception
            self.assertNotIsInstance(error,client.ModelResponseFormatError)
            self.assertEqual(error.usage['error_code'],'model_response_incomplete')
            self.assertIsNone(error.usage['finish_reason'])
            self.assertEqual(error.usage['stream_done'],done)

    def test_explicit_stop_without_done_still_records_known_completion(self):
        with self.assertRaises(client.ModelResponseFormatError) as caught:
            self.invoke(self.stream('{"subjects":',done=False))
        self.assertEqual(caught.exception.usage['finish_reason'],'stop')
        self.assertFalse(caught.exception.usage['stream_done'])

    def test_length_truncation_never_uses_format_repair_even_for_valid_json(self):
        for raw in ('{"passed":true}','{"subjects":'):
            with self.subTest(raw=raw),self.assertRaises(client.CloudCallError) as caught:
                self.invoke(self.stream(raw,finish='length'))
            self.assertNotIsInstance(caught.exception,client.ModelResponseFormatError)
            self.assertEqual(caught.exception.usage['error_code'],'model_output_truncated')
            self.assertEqual(caught.exception.usage['finish_reason'],'length')

    def test_completed_empty_answers_have_explicit_diagnostics_without_repair(self):
        for raw in ('', ' \n\t ', ' ' * 105, '\u3000\u00a0\r\n'):
            with self.subTest(raw=repr(raw)),self.assertRaises(client.CloudCallError) as caught:
                self.invoke(self.stream(raw,reasoning='PRIVATE-REASONING'))
            error=caught.exception
            self.assertNotIsInstance(error,client.ModelResponseFormatError)
            self.assertFalse(hasattr(error,'response_text'))
            self.assertEqual(error.usage['error_code'],'model_empty_completed')
            self.assertEqual(error.usage['answer_chars'],len(raw))
            self.assertEqual(error.usage['non_whitespace_chars'],0)
            self.assertTrue(error.usage['empty_after_strip'])
            self.assertEqual(error.usage['finish_reason'],'stop')
            self.assertTrue(error.usage['stream_done'])
            self.assertIn('上游已结束响应但正文为空或只有空白',str(error))
            self.assertIn('未自动重试、未切换渠道',str(error))
            public=str(error)+json.dumps(error.usage)
            for secret in ('PRIVATE-REASONING','offline-test-key'):
                self.assertNotIn(secret,public)

    def test_empty_answer_without_confirmed_completion_stays_incomplete(self):
        for finish,done in (('stop',False),(None,True),(None,False),('unknown',True)):
            with self.subTest(finish=finish,done=done),self.assertRaises(client.CloudCallError) as caught:
                self.invoke(self.stream(' \n\t ',finish=finish,done=done))
            self.assertNotIsInstance(caught.exception,client.ModelResponseFormatError)
            self.assertEqual(caught.exception.usage['error_code'],'model_response_incomplete')
            self.assertEqual(caught.exception.usage['non_whitespace_chars'],0)

    def test_filtered_answers_never_use_format_repair_or_empty_completed(self):
        for raw,finish in (('{bad','content_filter'),(' \n\t ','content_filter'),
                           ('{bad','tool_calls'),('{bad','insufficient_system_resource')):
            with self.subTest(raw=raw,finish=finish),self.assertRaises(client.CloudCallError) as caught:
                self.invoke(self.stream(raw,finish=finish))
            self.assertNotIsInstance(caught.exception,client.ModelResponseFormatError)
            self.assertEqual(caught.exception.usage['error_code'],'model_response_incomplete')

    def test_empty_length_truncation_keeps_existing_classification(self):
        with self.assertRaises(client.CloudCallError) as caught:
            self.invoke(self.stream(' \n\t ',finish='length'))
        self.assertNotIsInstance(caught.exception,client.ModelResponseFormatError)
        self.assertEqual(caught.exception.usage['error_code'],'model_output_truncated')

    def test_non_whitespace_count_excludes_internal_spaces_without_exposing_text(self):
        raw=' {"passed": true} \n'
        result,usage=self.invoke(self.stream(raw))
        self.assertEqual(result,{'passed':True})
        self.assertEqual(usage['answer_chars'],len(raw))
        self.assertEqual(usage['non_whitespace_chars'],len('{"passed":true}'))
        self.assertFalse(usage['empty_after_strip'])
        self.assertNotIn(raw,json.dumps(usage))

    def test_unknown_finish_reason_is_sanitized_and_not_repairable(self):
        for reason in ('private-server-reason', {'private-server-reason':'secret'}):
            with self.subTest(reason=reason),self.assertRaises(client.CloudCallError) as caught:
                self.invoke(self.stream('{bad',finish=reason))
            self.assertNotIsInstance(caught.exception,client.ModelResponseFormatError)
            self.assertEqual(caught.exception.usage['finish_reason'],'unknown')
            self.assertNotIn('private-server-reason',str(caught.exception)+json.dumps(caught.exception.usage))

    def test_format_repair_body_is_bounded_without_silent_truncation(self):
        maximum=client.MAX_FORMAT_REPAIR_CHARS
        raw='x'*maximum
        with self.assertRaises(client.ModelResponseFormatError) as caught:
            self.invoke(self.stream(raw))
        self.assertEqual(caught.exception.response_text,raw)
        with self.assertRaises(client.CloudCallError) as caught:
            self.invoke(self.stream(raw+'x'))
        self.assertNotIsInstance(caught.exception,client.ModelResponseFormatError)
        self.assertEqual(caught.exception.usage['error_code'],'model_json_repair_too_large')
        self.assertFalse(hasattr(caught.exception,'response_text'))

    def test_valid_object_and_existing_fence_compatibility_are_unchanged(self):
        for raw in ('{"passed":true}','```json\n{"passed":true}\n```'):
            for finish in ('stop',None):
                with self.subTest(raw=raw,finish=finish):
                    result,usage=self.invoke(self.stream(raw,finish=finish))
                    self.assertEqual(result,{'passed':True})
                    self.assertEqual(usage['finish_reason'],finish)
                    self.assertTrue(usage['stream_done'])

    def test_single_broken_fence_uses_format_repair_without_parser_crash(self):
        with self.assertRaises(client.ModelResponseFormatError) as caught:
            self.invoke(self.stream('```'))
        self.assertEqual(caught.exception.usage['json_error_kind'],'invalid_markdown_fence')
        self.assertEqual(caught.exception.response_text,'```')

    def test_transport_failure_after_stop_does_not_become_format_failure(self):
        response=self.stream('{bad')
        first=response.iter_lines.return_value[0]
        def lines(**kwargs):
            yield first
            raise client.requests.ConnectionError(client.urllib3_errors.ReadTimeoutError(
                None,'/private','private transport text'))
        response.iter_lines.side_effect=lines
        with self.assertRaises(client.CloudCallError) as caught:
            self.invoke(response)
        error=caught.exception
        self.assertNotIsInstance(error,client.ModelResponseFormatError)
        self.assertEqual(error.usage['error_code'],'read_timeout')
        self.assertEqual(error.usage['finish_reason'],'stop')
        self.assertFalse(error.usage['stream_done'])
        self.assertNotIn('private',str(error)+json.dumps(error.usage))


if __name__=='__main__':unittest.main()
