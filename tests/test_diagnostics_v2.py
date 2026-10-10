import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app import diagnostics as d


class DiagnosticsV2Tests(unittest.TestCase):
    def test_crash_text_redacts_bearer_and_json_credentials(self):
        for value, secret in [('Authorization: Bearer abc.def.secret', 'abc.def.secret'),
                              ('{"api_key":"private-value"}', 'private-value')]:
            self.assertNotIn(secret, d.redact_text(value))

    def test_full_package_has_version_and_collection_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'launcher').mkdir()
            (root / 'launcher/update-channel.json').write_text('{"release_id":"test"}', encoding='utf-8')
            job = SimpleNamespace(id='job', user_id=1, request={}, logs=['failure'], artifacts={})
            with patch.object(d, 'PROJECT_ROOT', root), patch.object(d, 'RUNTIME_LOGS_DIR', root / 'logs'), \
                    patch.object(d, 'DIAGNOSTICS_DIR', root / 'reports'), \
                    patch.object(d, '_windows_environment', return_value={'available': False, 'reason': 'timeout'}), \
                    patch.object(d, '_command_version', return_value='test'), \
                    patch.object(d, '_package_versions', return_value={}):
                package = d.create_diagnostic_package(job)
            with zipfile.ZipFile(package) as archive:
                environment = json.loads(archive.read('运行环境.json'))
                self.assertEqual(environment['diagnostic_schema_version'], 2)
                self.assertEqual(environment['release']['release_id'], 'test')
                self.assertEqual(environment['windows_system']['reason'], 'timeout')
                self.assertTrue(json.loads(archive.read('导出清单.json'))['related_reports'])

    def test_related_owner_only_and_private_content_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dynamic = root / 'workspace/video_studio/1/child'
            dynamic.mkdir(parents=True)
            record = dict(id='child', source_project={'id': 'job'}, status='exporting',
                          error='interrupted', script='PRIVATE SCRIPT', image_prompt='PRIVATE PROMPT',
                          api_key='PRIVATE KEY', shots=[dict(id='s', video_status='completed',
                          reference_image_ids=['图1'], reference_materials=[dict(label='图1',
                          description='讲解员，每张分镜出现')], video='clip.mp4')])
            (dynamic / 'record.json').write_text(json.dumps(record), encoding='utf-8')
            (dynamic / 'clip.mp4').write_bytes(b'private media')
            connection = root / 'workspace/comfyui/1'
            connection.mkdir(parents=True)
            (connection / 'connection.json').write_text(json.dumps({
                'base_url': 'http://user:URL_SECRET@127.0.0.1:8188/?token=QUERY_SECRET'}), encoding='utf-8')
            other = root / 'workspace/video_studio/2/other'
            other.mkdir(parents=True)
            (other / 'record.json').write_text(json.dumps(dict(record, id='other')), encoding='utf-8')
            job = SimpleNamespace(id='job', user_id=1, request={'_step_output_dir': 'project'}, logs=[])
            with patch.object(d, 'PROJECT_ROOT', root), zipfile.ZipFile(root / 'report.zip', 'w') as archive:
                manifest = d._write_related_reports(archive, job)
            with zipfile.ZipFile(root / 'report.zip') as archive:
                text = '\n'.join(archive.read(name).decode('utf-8') for name in archive.namelist())
                for secret in ['PRIVATE SCRIPT', 'PRIVATE PROMPT', 'PRIVATE KEY', 'private media', '讲解员', 'URL_SECRET', 'QUERY_SECRET']:
                    self.assertNotIn(secret, text)
                self.assertNotIn('dynamic_projects/other/record_summary.json', archive.namelist())
                summary = json.loads(archive.read('dynamic_projects/child/record_summary.json'))
                self.assertEqual(summary['status'], 'exporting')
                self.assertEqual(json.loads(archive.read('comfyui_connection_summary.json'))['port'], 8188)
                self.assertTrue(summary['shots'][0]['video']['exists'])
                self.assertTrue(summary['shots'][0]['reference_materials'][0]['purpose_flags']['required_every_shot'])
            self.assertTrue(any(item['status'] == 'missing' for item in manifest))

    def test_external_and_escaping_assets_are_not_read_or_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = d._structure({'video': 'https://host/clip?token=secret',
                                    'audio': '../outside.wav'}, Path(directory))
            self.assertEqual(summary['video'], {'external': True})
            self.assertFalse(summary['audio']['inside_project'])
            self.assertIsNone(summary['audio']['exists'])

    def test_unreadable_or_oversized_json_does_not_abort_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'workspace/jobs/job/artifacts'
            path.mkdir(parents=True)
            (path / 'poster_mapping.json').write_text('{broken', encoding='utf-8')
            (path / 'visual_prompt_plan.json').write_text(' ' * (4 * 1024 * 1024 + 1), encoding='utf-8')
            job = SimpleNamespace(id='job', user_id=1, request={})
            with patch.object(d, 'PROJECT_ROOT', root), zipfile.ZipFile(root / 'report.zip', 'w') as archive:
                manifest = d._write_related_reports(archive, job)
            self.assertTrue(any(item['status'] == 'unavailable' for item in manifest))

    def test_runtime_tail_is_bounded_and_reports_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'backend.log'
            path.write_bytes(b'a' * (300 * 1024))
            with patch.object(d, 'RUNTIME_LOGS_DIR', root), patch.object(d, '_recent_runtime_logs', return_value=[path]), \
                    zipfile.ZipFile(root / 'report.zip', 'w') as archive:
                manifest = d._write_runtime_logs(archive)
            self.assertEqual(manifest[0]['bytes_exported'], 256 * 1024)
            self.assertTrue(manifest[0]['truncated'])
