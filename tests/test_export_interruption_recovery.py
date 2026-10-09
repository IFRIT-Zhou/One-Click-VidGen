import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import video_studio as studio, video_export as export


class ExportInterruptionTests(unittest.TestCase):
    def record(self):
        return dict(id='test', status='exporting', revision=5, logs=[],
                    shots=[dict(id='s', kind='video', video_status='completed', video='clip.mp4')],
                    audio='audio.wav', scenes=[dict(start=0, end=1)],
                    export_settings=dict(use_video_audio=False))

    def test_restart_recovers_and_allows_retry_without_changing_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            before = self.record()
            (path / 'record.json').write_text(json.dumps(before), encoding='utf-8')
            with patch.object(studio, 'project_has_image_edits', return_value=False):
                recovered = studio.read(path)
                self.assertEqual(recovered['status'], 'export_failed')
                self.assertEqual(recovered['revision'], 6)
                self.assertEqual(recovered['shots'], before['shots'])
                self.assertEqual(recovered['audio'], before['audio'])
                self.assertEqual(studio.read(path), recovered)
                with patch.object(studio, 'require_user', return_value={'id': 1}), \
                        patch.object(studio, 'directory', return_value=path), \
                        patch.object(export.threading, 'Thread') as thread:
                    try:
                        result = export.start_export('test', export.ExportRequest(revision=6), None)
                        self.assertEqual(result['status'], 'exporting')
                        thread.return_value.start.assert_called_once()
                    finally:
                        studio.ACTIVE.discard(str(path))

    def test_live_export_is_not_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            before = self.record()
            (path / 'record.json').write_text(json.dumps(before), encoding='utf-8')
            studio.ACTIVE.add(str(path))
            try:
                with patch.object(studio, 'project_has_image_edits', return_value=False):
                    self.assertEqual(studio.read(path), before)
            finally:
                studio.ACTIVE.discard(str(path))

    def test_completed_export_is_not_demoted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            before = self.record()
            before.update(status='completed', export={'raw': 'finished.mp4'})
            (path / 'record.json').write_text(json.dumps(before), encoding='utf-8')
            with patch.object(studio, 'project_has_image_edits', return_value=False):
                self.assertEqual(studio.read(path), before)
