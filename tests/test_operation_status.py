import unittest
from unittest.mock import patch
from backend.app.visual_editor import VisualEditor


class OperationStatusTests(unittest.TestCase):
    def test_image_timer_survives_progress_and_restarts_next_attempt(self):
        editor = VisualEditor()
        with patch('backend.app.visual_editor.time.time', return_value=100):
            editor._set_image_task('job', 'shot', status='running', message='提交中')
        with patch('backend.app.visual_editor.time.time', return_value=110):
            editor._set_image_task('job', 'shot', status='running', message='处理中')
        self.assertEqual(editor._image_tasks['job']['shot']['started_at'], 100)
        with patch('backend.app.visual_editor.time.time', return_value=120):
            editor._set_image_task('job', 'shot', status='failed', message='失败')
        self.assertEqual(editor._image_tasks['job']['shot']['finished_at'], 120)
        with patch('backend.app.visual_editor.time.time', return_value=150):
            editor._set_image_task('job', 'shot', status='running')
        self.assertEqual(editor._image_tasks['job']['shot']['started_at'], 150)
        self.assertIsNone(editor._image_tasks['job']['shot']['finished_at'])

    def test_render_records_completion_time(self):
        editor = VisualEditor()
        editor._set_task('job', status='running')
        editor._set_task('job', status='completed')
        self.assertGreaterEqual(editor._tasks['job']['finished_at'], editor._tasks['job']['started_at'])
