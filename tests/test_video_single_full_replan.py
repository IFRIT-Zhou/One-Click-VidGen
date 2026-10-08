import copy
import unittest
from unittest.mock import patch

from backend.app.video_prompt_refresh import refresh


class SingleFullReplanTests(unittest.TestCase):
    def test_image_refresh_does_not_reuse_old_split_screen(self):
        shot = dict(id='s', kind='video', duration=8, intent='山巅远眺',
                    action='古今左右分屏', image_prompt='古今左右分屏',
                    video_prompt='古今左右分屏', visual_description='古今左右分屏',
                    motion_plan={'old': '古今左右分屏'}, semantic={'message': '古今左右分屏'})
        plan = {'beats': [{'action': '庄周眺望云海'}], 'reference_visual': '庄周独立山巅'}
        with patch('backend.app.video_prompt_refresh.revise_motion', return_value=plan) as revise, \
             patch('backend.app.video_prompt_refresh.write_video_prompts', return_value=[{'video_prompt': '图1庄周眺望云海'}]) as writer:
            result, _ = refresh({}, '水墨', shot, [], basis='image', action=shot['action'],
                image_prompt=shot['image_prompt'], force_vision=True,
                image_analysis={'description': '庄周独立山巅'})
        self.assertEqual(revise.call_args.args[3], '')
        self.assertEqual(revise.call_args.args[4], '庄周独立山巅')
        self.assertNotIn('古今左右分屏', str(writer.call_args.args[1]))
        self.assertEqual(result['image_prompt'], shot['image_prompt'])
        self.assertEqual(result['action'], '庄周眺望云海')

    def test_image_refresh_fills_action_and_final_prompt_together(self):
        shot = dict(id='s1', kind='video', start=0, end=8, duration=8,
                    action='', video_prompt='', image_prompt='当前核心图', image='core.png')
        original = copy.deepcopy(shot)
        with patch('backend.app.video_prompt_refresh.revise_motion', return_value={'beats': [{'action': '衣袖随风摆动'}]}), \
             patch('backend.app.video_prompt_refresh.write_video_prompts', return_value=[{'video_prompt': '镜头稳定，衣袖随风摆动'}]) as writer, \
             patch('backend.app.video_prompt_refresh.write_image_prompts') as image_writer:
            result, _ = refresh({}, '', shot, [], basis='image', action='', image_prompt=shot['image_prompt'])
        self.assertEqual(result['action'], '衣袖随风摆动')
        self.assertEqual(result['video_prompt'], '镜头稳定，衣袖随风摆动')
        self.assertEqual(writer.call_args.args[1][0]['action'], '衣袖随风摆动')
        image_writer.assert_not_called()
        self.assertEqual(shot, original)
        for key in ('start', 'end', 'duration', 'image', 'image_prompt'):
            self.assertEqual(result[key], original[key])

    def test_rebuilds_all_prompts_without_mutating_source_or_timing(self):
        shot = dict(id='s8', kind='video', start=10, end=15, duration=5,
                    source_subtitles='原字幕', action='旧长台词', image_prompt='旧图',
                    video_prompt='旧视频', visual_description='旧设计',
                    reference_ids=[], semantic={'message': '原意'})
        original = copy.deepcopy(shot)
        with patch('backend.app.video_prompt_refresh.design_core_images', return_value=[
                dict(id='s8', visual_description='无字图案', reference_ids=[], semantic={'speech_turns': []})]) as core, \
             patch('backend.app.video_prompt_refresh.direct_motion', return_value=[
                dict(action='摊手思考', motion_plan={'version': 2})]), \
             patch('backend.app.video_prompt_refresh.write_image_prompts', return_value=[dict(image_prompt='新图')]), \
             patch('backend.app.video_prompt_refresh.write_video_prompts', return_value=[dict(video_prompt='新视频')]):
            result, analysis = refresh({}, '', shot, [], basis='full', action='旧长台词', image_prompt='旧图')
        self.assertEqual(shot, original)
        self.assertNotIn('action', core.call_args.args[2][0])
        for key in ('id', 'start', 'end', 'duration', 'source_subtitles'):
            self.assertEqual(result[key], shot[key])
        self.assertNotIn('message', result['semantic'])
        self.assertNotIn('intent', core.call_args.args[2][0])
        self.assertTrue(core.call_args.args[2][0]['design_needs_review'])
        self.assertEqual((result['action'], result['image_prompt'], result['video_prompt']), ('摊手思考', '新图', '新视频'))
        self.assertIsNone(analysis)
