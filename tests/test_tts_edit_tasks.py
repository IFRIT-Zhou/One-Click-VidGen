import unittest
from unittest.mock import patch
from types import SimpleNamespace
from backend.app.tts_edit_tasks import TtsEditTasks, TtsEditCancelled


class TtsEditTaskTests(unittest.TestCase):
    def test_cancel_is_independent_and_not_terminal_until_worker_exits(self):
        editor = TtsEditTasks()
        task = editor._start_edit('job', revision=4, total=12)
        result = editor.cancel('job', task['task_id'])
        self.assertTrue(result['accepted'])
        self.assertEqual(result['task']['status'], 'running')
        self.assertFalse(result['task']['cancellation_complete'])
        with self.assertRaises(TtsEditCancelled):
            editor._begin_edit_commit('job')
        editor._edit_failed('job', RuntimeError(), 'failed')
        self.assertEqual(editor.status('job')['status'], 'cancelled')
        self.assertEqual(editor.status('job')['adopted_version'], 4)
        self.assertIsNone(editor.status('job')['pending_version'])

    def test_stale_cancel_does_not_cancel_new_attempt(self):
        editor = TtsEditTasks()
        first = editor._start_edit('job')
        editor._set_task('job', status='completed')
        second = editor._start_edit('job', revision=1)
        with self.assertRaises(ValueError):
            editor.cancel('job', first['task_id'])
        self.assertNotEqual(first['task_id'], second['task_id'])
        self.assertFalse(editor._edit_cancelled('job'))

    def test_commit_cannot_be_reported_cancelled(self):
        editor = TtsEditTasks()
        task = editor._start_edit('job')
        editor._begin_edit_commit('job')
        self.assertFalse(editor.cancel('job', task['task_id'])['accepted'])
        editor._set_task('job', status='completed')
        self.assertEqual(editor.status('job')['adopted_version'], 1)

    def test_duplicate_start_and_progress_do_not_erase_cancel(self):
        editor = TtsEditTasks()
        task = editor._start_edit('job')
        with self.assertRaises(RuntimeError):
            editor._start_edit('job')
        editor.cancel('job', task['task_id'])
        editor._set_task('job', status='running', progress=50, message='synthesis')
        self.assertTrue(editor.status('job')['cancel_requested'])
        self.assertIn('停止', editor.status('job')['message'])

    def test_parent_cancel_does_not_block_status_or_cross_user_access(self):
        from backend.app import main
        from fastapi import HTTPException
        job = SimpleNamespace(user_id=7, status='cancelled')
        with patch.object(main, 'require_user', return_value={'id':7}), patch.object(main.store, 'get', return_value=job), \
             patch.object(main.tts_editor, 'revision', return_value=4), \
             patch.object(main.tts_editor, 'pending_alignment', return_value=None):
            data = main.get_tts_editor_status('job', None)
            self.assertEqual(data['adopted_version'], 4)
            job.user_id = 8
            with self.assertRaises(HTTPException):
                main.get_tts_editor_status('job', None)

    def test_remote_cancel_is_not_reported_confirmed_until_upstream_terminal(self):
        editor = TtsEditTasks()
        task = editor._start_edit('job', revision=4, total=12)
        editor._remote_edit('job', 'remote1', {'status':'running', 'completed_chunks':3, 'total_chunks':12})
        self.assertEqual(editor.status('job')['completed_segments'], 3)
        self.assertEqual(editor.status('job')['phase'], 'cloud_running')
        editor.cancel('job', task['task_id'])
        editor._edit_failed('job', RuntimeError(), 'failed')
        self.assertTrue(editor.status('job')['local_cancellation_complete'])
        self.assertFalse(editor.status('job')['cancellation_complete'])
        self.assertTrue(editor.status('job')['upstream_may_still_run'])
        editor._start_edit('job', revision=4)
        self.assertFalse(editor._edit_cancelled('job'))

    def test_failed_rollback_is_not_successful_cancellation(self):
        editor = TtsEditTasks()
        task = editor._start_edit('job')
        editor.cancel('job', task['task_id'])
        editor._edit_failed('job', RuntimeError(), '回滚异常', rollback_failed=True)
        self.assertEqual(editor.status('job')['status'], 'failed')
        self.assertEqual(editor.status('job')['message'], '回滚异常')


if __name__ == '__main__':
    unittest.main()
