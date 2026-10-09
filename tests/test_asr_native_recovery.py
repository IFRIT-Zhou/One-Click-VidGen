import os
import unittest
from unittest.mock import Mock, patch

from backend.app import pipeline


class AsrNativeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.job = pipeline.Job(id='asr-recovery', step='scene')
        self.store = Mock()

    def test_access_violation_retries_only_asr_on_cpu(self):
        for code in (3221225477, -1073741819):
            with self.subTest(code=code), patch.dict(os.environ, {'ASR_DEVICE': 'auto'}), patch.object(
                pipeline, 'run_command', side_effect=[pipeline.CommandExecutionError('crash', code), None]
            ) as run:
                pipeline.run_asr_stage(self.job, self.store, 'portable-python')
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args.args[2], ['portable-python', 'module2_scene_director.py'])
                self.assertEqual(run.call_args.kwargs['extra_env'], {'ASR_DEVICE': 'cpu'})
                self.assertEqual(os.environ['ASR_DEVICE'], 'auto')

    def test_normal_error_and_explicit_devices_do_not_retry(self):
        for device, code in (('auto', 1), ('cpu', 3221225477), ('cuda', 3221225477)):
            with self.subTest(device=device, code=code), patch.dict(os.environ, {'ASR_DEVICE': device}), patch.object(
                pipeline, 'run_command', side_effect=pipeline.CommandExecutionError('failure', code)
            ) as run:
                with self.assertRaises(pipeline.CommandExecutionError):
                    pipeline.run_asr_stage(self.job, self.store, 'python')
                self.assertEqual(run.call_count, 1)

    def test_failed_cpu_retry_stops_after_one_attempt_with_actionable_error(self):
        with patch.dict(os.environ, {'ASR_DEVICE': 'auto'}), patch.object(
            pipeline, 'run_command', side_effect=[pipeline.CommandExecutionError('GPU', 3221225477),
                                                pipeline.CommandExecutionError('CPU', 1)]
        ) as run:
            with self.assertRaisesRegex(RuntimeError, '自动转 CPU 后仍未完成'):
                pipeline.run_asr_stage(self.job, self.store, 'python')
            self.assertEqual(run.call_count, 2)

    def test_cancel_before_retry_does_not_launch_cpu(self):
        self.store.raise_if_cancelled.side_effect = pipeline.GenerationCancelled('cancelled')
        with patch.dict(os.environ, {'ASR_DEVICE': 'auto'}), patch.object(
            pipeline, 'run_command', side_effect=pipeline.CommandExecutionError('crash', 3221225477)
        ) as run:
            with self.assertRaises(pipeline.GenerationCancelled):
                pipeline.run_asr_stage(self.job, self.store, 'python')
            self.assertEqual(run.call_count, 1)

    def test_child_exit_status_is_preserved_for_recovery(self):
        process = Mock()
        process.stdout = ['正在初始化 Whisper [base] 模型驱动 (CUDA / float16)...\n']
        process.wait.return_value = 3221225477
        self.store.is_cancelled.return_value = False
        with patch.object(pipeline.subprocess, 'Popen', return_value=process), patch(
            'backend.app.subtitle_layout.presentation_env', return_value={}
        ):
            with self.assertRaises(pipeline.CommandExecutionError) as raised:
                pipeline.run_command(self.job, self.store, ['python'], 'ASR')
        self.assertEqual(raised.exception.return_code, 3221225477)
        self.store.detach_process.assert_called_once_with(self.job, process)


if __name__ == '__main__':
    unittest.main()
