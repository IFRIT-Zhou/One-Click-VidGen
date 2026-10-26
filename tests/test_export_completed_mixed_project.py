import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from backend.app import video_export as export, video_studio as studio


class CompletedMixedExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        (self.path / 'clip.mp4').write_bytes(b'clip')
        (self.path / 'image.jpg').write_bytes(b'image')
        self.record = dict(id='test', revision=8, status='video_generation_ready', logs=[], shots=[
            dict(id='static', kind='static', image='image.jpg', image_status='completed', video_status='pending'),
            dict(id='motion', kind='video', video='clip.mp4', video_status='completed'),
        ])

    def tearDown(self):
        studio.ACTIVE.discard(str(self.path))
        self.temp.cleanup()

    def run_export(self):
        with patch.object(studio, 'require_user', return_value={'id': 1}), \
             patch.object(studio, 'directory', return_value=self.path), \
             patch.object(studio, 'read', return_value=self.record), \
             patch.object(studio, 'project_has_image_edits', return_value=False), \
             patch.object(studio, 'save'), patch.object(export.threading, 'Thread'):
            return export.start_export('test', export.ExportRequest(revision=8), None)

    def test_completed_mixed_project_ignores_static_old_video_status(self):
        shots = copy.deepcopy(self.record['shots'])
        self.assertEqual(self.run_export()['status'], 'exporting')
        self.assertEqual(self.record['shots'], shots)

    def test_all_static_still_exports(self):
        self.record['shots'].pop()
        self.assertEqual(self.run_export()['status'], 'exporting')

    def test_unfinished_dynamic_still_blocked(self):
        self.record['shots'][1]['video_status'] = 'pending'
        with self.assertRaisesRegex(HTTPException, '动态镜头未完成'):
            self.run_export()

    def test_missing_file_names_shot(self):
        (self.path / 'clip.mp4').unlink()
        with self.assertRaisesRegex(HTTPException, '第 02 镜'):
            self.run_export()

    def test_unfinished_static_and_design_review_blocked(self):
        for field, value in [('image_status', 'pending'), ('design_needs_review', True)]:
            with self.subTest(field=field):
                old = self.record['shots'][0].get(field)
                self.record['shots'][0][field] = value
                with self.assertRaises(HTTPException):
                    self.run_export()
                self.record['shots'][0][field] = old

    def test_queued_and_active_projects_blocked(self):
        self.record['video_waiting_queue'] = ['motion']
        with self.assertRaises(HTTPException):
            self.run_export()
        self.record['video_waiting_queue'] = []
        studio.ACTIVE.add(str(self.path))
        with self.assertRaises(HTTPException):
            self.run_export()

    def test_unconfirmed_storyboard_blocked(self):
        self.record['status'] = 'storyboard_review'
        with self.assertRaises(HTTPException):
            self.run_export()
