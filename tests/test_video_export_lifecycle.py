import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app import video_export


class FakeStore:
    def __init__(self, job):
        self.job = job
        self.updates = []
        self.logs = []

    def get(self, identity):
        return self.job if identity == self.job.id else None

    def update(self, job, **changes):
        self.updates.append(changes)
        for key, value in changes.items():
            setattr(job, key, value)

    def log(self, job, line):
        self.logs.append(line)


class DynamicVideoLifecycleTest(unittest.TestCase):
    def test_completed_dynamic_export_closes_source_guided_job(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw, subtitles = root / 'raw.mp4', root / 'subtitles.mp4'
            raw.write_bytes(b'raw')
            subtitles.write_bytes(b'subtitles')
            job = SimpleNamespace(
                id='source-job', user_id=7, status='waiting_confirmation', step='await_audio_review',
                progress=46, message='等待确认', error=None, artifacts={},
                request={'dynamic_video': True, '_step_mode_stage': 'audio_review'},
            )
            store = FakeStore(job)
            record = {
                'id': 'dynamic-project', 'status': 'completed',
                'source_project': {'id': 'source-job'},
                'export': {'directory': str(root), 'raw': str(raw), 'subtitles': str(subtitles)},
            }
            registered = []
            with patch.object(video_export, 'store', store), \
                    patch.object(video_export, 'persist_step_workflow_state') as persisted, \
                    patch.object(video_export, 'register_job_asset', side_effect=lambda *args: registered.append(args)):
                self.assertTrue(video_export.reconcile_source_job(record, 7))
            self.assertEqual(job.status, 'completed')
            self.assertEqual(job.step, 'completed')
            self.assertEqual(job.progress, 100)
            self.assertEqual(job.request['_step_mode_stage'], 'completed')
            self.assertEqual(job.request['_dynamic_video_project_id'], 'dynamic-project')
            self.assertEqual(job.request['_step_output_dir'], root.name)
            self.assertEqual(job.artifacts['video_raw'], '/api/video-studio/dynamic-project/export/raw')
            self.assertEqual(job.artifacts['video_with_subtitles'], '/api/video-studio/dynamic-project/export/subtitles')
            persisted.assert_called_once_with(job, 'completed', message=job.message)
            self.assertEqual(len(registered), 2)

    def test_reconciliation_is_safe_for_unrelated_or_missing_exports(self):
        job = SimpleNamespace(id='source-job', user_id=7, request={'dynamic_video': True})
        with patch.object(video_export, 'store', FakeStore(job)):
            self.assertFalse(video_export.reconcile_source_job({'status': 'video_review'}, 7))
            self.assertFalse(video_export.reconcile_source_job({
                'status': 'completed', 'source_project': {'id': 'source-job'},
                'export': {'directory': 'missing', 'raw': 'missing', 'subtitles': 'missing'},
            }, 7))


if __name__ == '__main__':
    unittest.main()
