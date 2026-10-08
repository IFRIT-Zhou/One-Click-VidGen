import json
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app import main, tts_editor


def wav(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\0\0' * 16000)


class ClusterRevoiceStepCompatibilityTests(unittest.TestCase):
    def advance(self, parameters):
        job = SimpleNamespace(id='test', user_id=1, status='waiting_confirmation',
                              message='', request={**parameters, 'step_mode': True,
                              '_step_workflow_version': 2, '_step_mode_stage': 'visual_setup'})
        with patch.object(main, 'require_user', return_value={'id': 1}), \
             patch.object(main.store, 'get', return_value=job), \
             patch.object(main.store, 'update'), patch.object(main.store, 'log'), \
             patch.object(main, 'persist_step_workflow_state'), \
             patch.object(main, 'validate_step_audio_snapshot', return_value={'sentence_count': 11}), \
             patch.object(main, '_required_job_config_error', return_value=''), \
             patch.object(main.store, 'advance_step_workflow', return_value={'stage': 'visual_running'}) as advance, \
             patch.object(main.tts_editor, 'regenerate') as revoice:
            result = main.advance_step_workflow('test', main.StepWorkflowAdvanceRequest(
                action='start_visual', parameters={'visual_style_prompt': '简笔画'}), None)
            advance.assert_called_once_with(job, 'start_visual')
            revoice.assert_not_called()
        self.assertEqual(result['stage'], 'visual_running')
        self.assertEqual(job.request['cluster_voice_id'], 'voice_11.wav')
        self.assertIsInstance(job.request['tts_voice_id'], str)
        return job

    def test_historical_null_voice_can_advance_without_revoice(self):
        self.advance({'tts_engine': 'cluster', 'tts_voice_id': None,
                      'cluster_voice_type': 'preset', 'cluster_voice_id': 'voice_11.wav'})

    def test_cluster_revoice_third_and_eleventh_then_advance(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            segment_dir = project / 'other' / 'tts_segments'
            segments = []
            for index in range(1, 12):
                filename = f'segment_{index:04d}.wav'
                wav(segment_dir / filename)
                segments.append(dict(index=index, text=f'第{index}句', filename=filename,
                                     start=index - 1, end=index, duration=1))
            manifest = dict(engine='cluster', tts_voice_id=None, segments=segments,
                            cluster_voice_type='preset', cluster_voice_id='voice_11.wav')
            (segment_dir / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            (project / 'other' / '最终字幕.srt').write_text('\n\n'.join(
                f'{i}\n00:00:{i-1:02d},000 --> 00:00:{i:02d},000\n第{i}句'
                for i in range(1, 12)), encoding='utf-8')
            wav(project / 'input' / '配音.wav')
            job = SimpleNamespace(id='test', request=dict(tts_engine='cluster',
                tts_voice_id='existing-local.wav', cluster_voice_type='preset',
                cluster_voice_id='voice_11.wav'), user_id=1)
            def synthesize(**kwargs):
                self.assertEqual(kwargs['request']['cluster_voice_id'], 'voice_11.wav')
                generated = kwargs['segment_archive_dir']
                rows = []
                for i in range(2):
                    filename = f'new_{i}.wav'
                    wav(generated / filename)
                    rows.append({'filename': filename})
                (generated / 'manifest.json').write_text(json.dumps({'segments': rows}), encoding='utf-8')
            editor = tts_editor.TtsEditor()
            with patch.object(editor, '_project_dir', return_value=project), \
                 patch.object(editor, '_snapshot_history'), patch.object(editor, '_sync_module1_flat_outputs'), \
                 patch.object(tts_editor, '_speech_alignment', return_value={}), \
                 patch.object(tts_editor, '_commit_step_audio_edit'), \
                 patch.object(tts_editor.store, 'log'), patch.object(tts_editor.store, 'update'), \
                 patch('backend.app.cloud_client.cloud_client_for', return_value=Mock()), \
                 patch('backend.app.cloud_tts.synthesize_cloud_tts', side_effect=synthesize):
                editor._regenerate_sync_impl(job, 1, [3, 11], {}, {}, {})
            self.assertEqual(job.request['tts_voice_id'], 'existing-local.wav')
            self.assertEqual(job.request['cluster_voice_id'], 'voice_11.wav')
            self.advance(job.request)

    def test_compatibility_does_not_accept_invalid_voice_types(self):
        with self.assertRaises(ValueError):
            main.GenerateRequest(tts_voice_id=[])

    def test_retry_validation_preserves_uploaded_cluster_voice(self):
        job = SimpleNamespace(id='test', user_id=1, request=dict(tts_engine='cluster',
            tts_voice_id=None, cluster_voice_type='uploaded', cluster_voice_id='custom-voice'))
        with patch.object(main, 'require_user', return_value={'id': 1}), \
             patch.object(main.store, 'get', return_value=job), \
             patch.object(main.store, 'update'), \
             patch.object(main.store, 'retry_tts', return_value={'ok': True}) as retry:
            main.retry_job_tts('test', None, main.RetryTtsRequest(parameters={'tts_speed': 1.1}))
            retry.assert_called_once_with(job)
        self.assertEqual((job.request['cluster_voice_type'], job.request['cluster_voice_id']),
                         ('uploaded', 'custom-voice'))
        self.assertIsInstance(job.request['tts_voice_id'], str)

    def test_boundary_revoice_does_not_erase_cluster_voice_with_empty_manifest(self):
        editor = tts_editor.TtsEditor()
        job = SimpleNamespace(id='test', request=dict(tts_engine='cluster',
            cluster_voice_type='uploaded', cluster_voice_id='custom-voice'))
        with tempfile.TemporaryDirectory() as folder:
            def synthesize(**kwargs):
                self.assertEqual(kwargs['request']['cluster_voice_id'], 'custom-voice')
                self.assertEqual(kwargs['request']['cluster_voice_type'], 'uploaded')
                raise RuntimeError('stop before paid request')
            with patch('backend.app.cloud_client.cloud_client_for', return_value=Mock()), \
                 patch('backend.app.cloud_tts.synthesize_cloud_tts', side_effect=synthesize):
                with self.assertRaisesRegex(RuntimeError, 'stop before paid request'):
                    editor._synthesize_parts(job=job, user_id=1, project_dir=Path(folder),
                        manifest={'engine': 'cluster', 'cluster_voice_id': None, 'cluster_voice_type': ''},
                        texts=['一句'], work_dir=Path(folder) / 'work')
