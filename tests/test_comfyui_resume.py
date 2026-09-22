import unittest

from backend.app.video_generation import _recover_local_prompt_id


class ComfyUIResumeTest(unittest.TestCase):
    def test_legacy_log_recovers_prompt_identity_without_resubmission(self):
        shot = {'id': 'shot01', 'video_request': {'backend': 'comfyui'}}
        record = {'logs': [
            'shot01：正在向 ComfyUI 上传核心分镜图',
            'shot01：ComfyUI 已接收任务 7cf72151-29a5-430f-807e-dfac70e2c889，正在生成',
            "shot01：HTTPConnectionPool(host='127.0.0.1', port=8188): Read timed out.",
        ]}
        prompt_id = _recover_local_prompt_id(record, shot)
        self.assertEqual(prompt_id, '7cf72151-29a5-430f-807e-dfac70e2c889')
        self.assertEqual(shot['video_task_id'], prompt_id)

    def test_persisted_prompt_identity_wins_over_old_logs(self):
        shot = {'id': 'shot01', 'video_task_id': 'current-task'}
        record = {'logs': ['shot01：ComfyUI 已接收任务 old-task-123456789，正在生成']}
        self.assertEqual(_recover_local_prompt_id(record, shot), 'current-task')

    def test_abandoned_prompt_is_not_recovered_from_old_logs(self):
        prompt_id = '7cf72151-29a5-430f-807e-dfac70e2c889'
        shot = {'id': 'shot01', 'video_task_id': '', 'video_abandoned_task_ids': [prompt_id]}
        record = {'logs': [f'shot01：ComfyUI 已接收任务 {prompt_id}，正在生成']}
        self.assertEqual(_recover_local_prompt_id(record, shot), '')


if __name__ == '__main__':
    unittest.main()
