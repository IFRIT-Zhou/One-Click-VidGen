import unittest
from pathlib import Path
from unittest.mock import patch
from backend.app import comfyui_bridge as bridge


class ComfyTerminalStateTests(unittest.TestCase):
    def interrupted(self):
        return {'status': {'status_str': 'error', 'completed': False, 'messages': [
            ['execution_start', {}], ['execution_interrupted', {'node_type': 'SelfLiftH3Sampler'}]]}, 'outputs': {}}

    def test_interrupted_task_is_terminal_and_retryable(self):
        with patch.object(bridge, '_connection', return_value={}), \
             patch.object(bridge, '_history_payload', return_value={'old': self.interrupted()}), \
             patch.object(bridge, '_queued_prompt_ids') as queue:
            self.assertEqual(bridge.video_task_state(1, 'old'), 'failed')
            queue.assert_not_called()

    def test_polling_stops_immediately_without_resubmission(self):
        with patch.object(bridge, 'video_profile', return_value={}), \
             patch.object(bridge, '_connection', return_value={}), \
             patch.object(bridge, '_history_payload', return_value={'old': self.interrupted()}), \
             patch.object(bridge.requests, 'post') as post, \
             patch.object(bridge.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, '已中断.*SelfLiftH3Sampler'):
                bridge.run_video_profile(1, 'profile', prompt='', image_path=Path('unused.jpg'),
                    duration=10, ratio='16:9', output_path=Path('unused.mp4'), existing_prompt_id='old')
            post.assert_not_called()
            sleep.assert_not_called()

    def test_incomplete_or_unreachable_is_not_terminal(self):
        self.assertEqual(bridge._history_failure({'status': {'completed': False,
            'messages': [['execution_start', {}], ['execution_cached', {}]]}}), '')
        with patch.object(bridge, '_connection', return_value={}), \
             patch.object(bridge, '_history_payload', return_value=None):
            self.assertEqual(bridge.video_task_state(1, 'old'), 'unknown')

    def test_status_only_failure_and_missing_error_details(self):
        self.assertTrue(bridge._history_failure({'status': {'status_str': 'error'}}))
        self.assertTrue(bridge._history_failure({'status': {'messages': [['execution_error']]}}))
        self.assertEqual(bridge._history_failure({'status': {'status_str': 'success', 'completed': True}}), '')


if __name__ == '__main__':
    unittest.main()
