"""Owned local-process diagnostics; no browser, provider, or real project work."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from backend.app import vibe_remotion as renderer


def marker(stage, status='started', **extra):
    return 'OCV_RENDER_STAGE:' + json.dumps(dict(stage=stage, status=status,
        elapsed_seconds=1, **extra)) + '\n'


class RemotionProcessLogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.log = Path(temp.name)/'render.log'
        self.proc = MagicMock()
        self.proc.poll.return_value = None
        self.proc.returncode = None

    def terminate(self, proc):
        self.assertIs(proc, self.proc)
        proc.poll.return_value = -9
        proc.returncode = -9

    def test_timeout_uses_cumulative_bytes_and_bounded_final_drain(self):
        prefix = '准备\n'
        progress = prefix + marker('poster')
        complete = progress + marker('frame_end', secret='not-for-ui')
        self.proc.communicate.side_effect = [
            subprocess.TimeoutExpired(['node'], .2, output=prefix.encode()),
            subprocess.TimeoutExpired(['node'], .2, output=progress.encode()),
            (complete, None)]
        with patch.object(renderer.subprocess, 'Popen', return_value=self.proc), \
             patch.object(renderer.time, 'monotonic', side_effect=[0, 0, .3, 1.1]), \
             patch.object(renderer, '_terminate', side_effect=self.terminate) as stop:
            with self.assertRaises(TimeoutError) as caught:
                renderer._run(['node'], timeout=1, log=self.log)
        self.assertEqual(self.log.read_text(encoding='utf-8'), complete)
        self.assertIn('当前阶段：结尾帧截图', str(caught.exception))
        self.assertNotIn('not-for-ui', str(caught.exception))
        stop.assert_called_once_with(self.proc)
        self.assertEqual(self.proc.communicate.call_args.kwargs, {'timeout': 2})
        self.proc.__exit__.assert_not_called()

    def test_drain_timeout_retains_snapshot_without_waiting_or_closing_live_pipe(self):
        progress = marker('frame_hold', 'completed')
        self.proc.communicate.side_effect = [
            subprocess.TimeoutExpired(['node'], .2, output=progress.encode()),
            subprocess.TimeoutExpired(['node'], 2, output=progress.encode())]
        with patch.object(renderer.subprocess, 'Popen', return_value=self.proc), \
             patch.object(renderer.time, 'monotonic', side_effect=[0, 0, 2]), \
             patch.object(renderer, '_terminate', side_effect=self.terminate):
            with self.assertRaisesRegex(TimeoutError, '最近完成阶段：停留帧截图'):
                renderer._run(['node'], timeout=1, log=self.log)
        self.assertEqual(self.log.read_text(encoding='utf-8'), progress)
        self.assertEqual(self.proc.communicate.call_count, 2)
        self.proc.stdout.close.assert_not_called()
        self.proc.__exit__.assert_not_called()

    def test_cancellation_preserves_output_and_never_restarts_process(self):
        progress = marker('frame_middle')
        self.proc.communicate.side_effect = [
            subprocess.TimeoutExpired(['node'], .2, output=progress.encode()),
            (progress + 'terminated\n', None)]
        checks = iter((False, False, True))
        with patch.object(renderer.subprocess, 'Popen', return_value=self.proc) as spawn, \
             patch.object(renderer, '_terminate', side_effect=self.terminate) as stop:
            with self.assertRaisesRegex(InterruptedError, '已停止 Remotion'):
                renderer._run(['node'], timeout=10, log=self.log, cancelled=lambda: next(checks))
        spawn.assert_called_once()
        stop.assert_called_once_with(self.proc)
        self.assertEqual(self.log.read_text(encoding='utf-8'), progress + 'terminated\n')

    def test_cleanup_and_log_failures_do_not_mask_original_cancellation(self):
        self.proc.communicate.side_effect = [
            subprocess.TimeoutExpired(['node'], .2, output=b'partial'),
            OSError('drain failed')]
        self.proc.kill.side_effect = OSError('kill failed')
        log = MagicMock()
        log.write_text.side_effect = OSError('disk full')
        checks = iter((False, False, True))
        with patch.object(renderer.subprocess, 'Popen', return_value=self.proc), \
             patch.object(renderer, '_terminate', side_effect=OSError('tree cleanup failed')) as stop:
            with self.assertRaisesRegex(InterruptedError, '已停止 Remotion'):
                renderer._run(['node'], timeout=10, log=log, cancelled=lambda: next(checks))
        stop.assert_called_once_with(self.proc)
        self.proc.kill.assert_called_once()
        self.proc.communicate.assert_called_with(timeout=2)
        log.write_text.assert_called_once_with('partial', encoding='utf-8')
        self.proc.__exit__.assert_not_called()

    def test_log_failure_does_not_mask_validation_or_execution_error(self):
        for output, expected in [('{"kind":"validation","error":"bad text"}', ValueError),
                                 ('Missing renderer module', RuntimeError)]:
            with self.subTest(output=output):
                self.proc.communicate.return_value = (output, None)
                self.proc.poll.return_value = 1
                self.proc.returncode = 1
                log = MagicMock()
                log.write_text.side_effect = OSError('disk full')
                with patch.object(renderer.subprocess, 'Popen', return_value=self.proc):
                    with self.assertRaises(expected) as caught:
                        renderer._run(['node'], timeout=1, log=log)
                self.assertNotIn('disk full', str(caught.exception))
                log.write_text.assert_called_once_with(output, encoding='utf-8')

    def test_success_output_and_log_tail_unchanged_after_timeout_poll(self):
        complete = 'x'*100_005 + '完整结果\n'
        self.proc.communicate.side_effect = [
            subprocess.TimeoutExpired(['node'], .2, output=b'x'*100_005),
            (complete, None)]
        self.proc.poll.return_value = 0
        self.proc.returncode = 0
        with patch.object(renderer.subprocess, 'Popen', return_value=self.proc), \
             patch.object(renderer, '_terminate') as stop:
            result = renderer._run(['node'], timeout=10, log=self.log)
        self.assertEqual(result, complete)
        self.assertEqual(self.log.read_text(encoding='utf-8'), complete[-100_000:])
        stop.assert_not_called()

    def test_success_is_not_failed_by_optional_diagnostic_log(self):
        self.proc.communicate.return_value = ('ok\n', None)
        self.proc.poll.return_value = 0
        self.proc.returncode = 0
        log = MagicMock()
        log.write_text.side_effect = OSError('permission denied')
        with patch.object(renderer.subprocess, 'Popen', return_value=self.proc):
            self.assertEqual(renderer._run(['node'], timeout=1, log=log), 'ok\n')

    def test_timeout_never_exposes_unrecognized_marker_or_arbitrary_output(self):
        output = 'private-debug-content\n' + marker('secret-stage') + 'OCV_RENDER_STAGE:not-json\n'
        self.proc.communicate.side_effect = [
            subprocess.TimeoutExpired(['node'], .2, output=output.encode()), (output, None)]
        with patch.object(renderer.subprocess, 'Popen', return_value=self.proc), \
             patch.object(renderer.time, 'monotonic', side_effect=[0, 0, 2]), \
             patch.object(renderer, '_terminate', side_effect=self.terminate):
            with self.assertRaises(TimeoutError) as caught:
                renderer._run(['node'], timeout=1, log=self.log)
        self.assertEqual(str(caught.exception), 'Remotion 本地渲染超时；未修改正式镜头')
        self.assertEqual(self.log.read_text(encoding='utf-8'), output)

    def test_real_local_child_output_survives_timeout(self):
        # Only this synthetic Python child is started and killed, not OCV,
        # Chromium, a model provider, or another user's process.
        code = "import time;print('OCV_RENDER_STAGE:' + " + repr(json.dumps(
            dict(stage='frame_end', status='started', elapsed_seconds=0))) + ",flush=True);time.sleep(30)"
        with self.assertRaisesRegex(TimeoutError, '结尾帧截图'):
            renderer._run([sys.executable, '-u', '-c', code], timeout=1, log=self.log)
        self.assertIn('"stage": "frame_end"', self.log.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
