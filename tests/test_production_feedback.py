import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import ExitStack

from fastapi import HTTPException
from backend.app import pipeline, video_sources
from backend.app.tts_editor import tts_editor
from backend.app.subtitle_proofread import preview, timing_token
from test_step_workflow_v2 import StepWorkflowV2Test


class AudioHandoffTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.job = StepWorkflowV2Test._write_audio_snapshot(self.root, 2, 2)
        self.job.user_id = 7; self.job.status = 'cancelled'; self.job.request['dynamic_video'] = True
        self.source = self.root/'output/guided'
        (self.source/'other/audio_revision.json').write_text(json.dumps(pipeline._step_audio_revision(self.source)), encoding='utf-8')
        store = SimpleNamespace(get=lambda _: self.job)
        self.stack.enter_context(patch.object(video_sources, 'dependencies', return_value=(store, None, None)))
        self.stack.enter_context(patch.object(tts_editor, '_project_dir', return_value=self.source))
        self.stack.enter_context(patch.object(tts_editor, 'status', return_value={'status':'cancelled'}))

    def test_cancelled_job_completed_snapshot_importable_without_new_tts(self):
        result = video_sources.source_readiness(7, self.job.id)
        self.assertTrue(result['can_import'], result)
        job, source = video_sources.source_project(7, self.job.id, audio_task=True)
        original = (source/'input/配音.wav').read_bytes()
        target = self.root/'new-project'
        video_sources.copy_assets(source, target)
        self.assertEqual((target/'assets/audio.wav').read_bytes(), original)
        self.assertEqual(job.status, 'cancelled')

    def test_unsealed_missing_and_running_sources_blocked(self):
        marker = self.source/'other/audio_revision.json'
        marker.unlink()
        self.assertFalse(video_sources.source_readiness(7, self.job.id)['can_import'])
        self.job.status = 'running'
        self.assertFalse(video_sources.source_readiness(7, self.job.id)['can_import'])
        self.job.status = 'cancelled'
        (self.source/'input/配音.wav').unlink()
        self.assertFalse(video_sources.source_readiness(7, self.job.id)['audio_ready'])

    def test_active_editor_and_other_user_cannot_import(self):
        with patch.object(tts_editor, 'status', return_value={'status':'running', 'cancel_requested':True}):
            self.assertFalse(video_sources.source_readiness(7, self.job.id)['can_import'])
        with self.assertRaises(HTTPException):
            video_sources.source_readiness(8, self.job.id)

    def test_copy_rejects_changed_source(self):
        original = video_sources.source_fingerprint
        count = 0
        def fingerprint(source):
            nonlocal count
            count += 1
            result = original(source)
            if count > 1:
                result['input/配音.wav'] = 'changed'
            return result
        with patch.object(video_sources, 'source_fingerprint', side_effect=fingerprint):
            with self.assertRaises(HTTPException):
                video_sources.copy_assets(self.source, self.root/'new-project')

    def test_uploaded_archive_never_replaces_directory(self):
        import os
        real_replace = os.replace
        def replace(source, target):
            if Path(source).is_dir():
                raise PermissionError(5, 'directory in use')
            return real_replace(source, target)
        archive = self.root/'segments'; archive.mkdir(); (archive/'unrelated').write_bytes(b'keep')
        with patch.object(pipeline.os, 'replace', side_effect=replace):
            pipeline._archive_uploaded_audio_segments(self.job, self.source/'input/配音.wav', self.source/'other/最终字幕.srt', archive)
        self.assertTrue((archive/'manifest.json').is_file())
        self.assertEqual((archive/'unrelated').read_bytes(), b'keep')

    def test_failed_uploaded_archive_keeps_candidate_and_old_files(self):
        archive = self.root/'segments'; archive.mkdir(); (archive/'keep.wav').write_bytes(b'keep')
        with patch.object(pipeline, '_copy_file_atomic', side_effect=PermissionError(5, 'file in use')):
            with self.assertRaisesRegex(RuntimeError, '候选分段保留'):
                pipeline._archive_uploaded_audio_segments(self.job, self.source/'input/配音.wav', self.source/'other/最终字幕.srt', archive)
        self.assertEqual((archive/'keep.wav').read_bytes(), b'keep')
        self.assertEqual(len(list(self.root.glob('.segments.*.tmp/manifest.json'))), 1)


class SubtitleProofreadTests(unittest.TestCase):
    def test_invalid_save_returns_422_before_any_write(self):
        from backend.app import main
        rows = [{'slide_id':'s1','start':0,'end':2,'text_content':'甲'}]
        job = SimpleNamespace(user_id=7, status='waiting_confirmation',
            request={'step_mode':True, '_step_workflow_version':2, '_step_mode_stage':'audio_review'})
        with patch.object(main, 'require_user', return_value={'id':7}), \
             patch.object(main.store, 'get', return_value=job), \
             patch.object(main, '_step_subtitle_payload', return_value=(None, None, rows)):
            with self.assertRaises(HTTPException) as caught:
                main.save_step_workflow_subtitles('job', main.StepWorkflowSubtitleRequest(updates={'missing':'乙'}), None)
            self.assertEqual(caught.exception.status_code, 422)
            self.assertEqual(rows[0]['text_content'], '甲')

    def test_preview_has_diff_glossary_and_preserves_time(self):
        rows = [{'slide_id':'s1', 'start':0, 'end':2.5, 'text':'奔门'}]
        before = copy.deepcopy(rows)
        data = preview(rows, {'s1':'贲门'}, '贲门', ['贲门'])
        self.assertEqual(data['changes'][0]['before'], '奔门')
        self.assertEqual(data['changes'][0]['after'], '贲门')
        self.assertEqual(data['terms_to_review'], ['贲门'])
        self.assertEqual(data['timing_token'], timing_token(rows))
        self.assertEqual(rows, before)
        self.assertFalse(data['generation_started'])

    def test_unknown_or_empty_text_rejected(self):
        rows = [{'slide_id':'s1', 'start':0, 'end':2.5, 'text':'甲'}]
        for changes in ({'s2':'乙'}, {'s1':' '}):
            with self.assertRaises(ValueError):
                preview(rows, changes)
