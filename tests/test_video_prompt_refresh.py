import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from backend.app import video_prompt_refresh, video_studio


class VideoPromptRefreshTest(unittest.TestCase):
    def shot(self):
        return dict(id='shot1', kind='video', action='用户动作', image_prompt='用户核心图',
                    source_subtitles=[{'text':'原文'}], duration=8, generation_duration=8,
                    intent='表达原文', reference_ids=[])

    def plan(self):
        return dict(version=2, scene_anchor='讲台', participants=['主角'],
                    beats=[{'action':'主角抬手','texts':[]}], reference_beat=1,
                    reference_visual='主角站在讲台上', reference_participants=['主角'], reference_texts=[])

    def test_new_dynamic_shot_can_derive_motion_from_core_without_manual_action(self):
        calls = []
        def ask(system, payload):
            calls.append((system, payload))
            return {'shots':[{'id':'shot1','motion_plan':self.plan()}]}
        result = video_prompt_refresh.revise_motion({}, self.shot(), [], '', '主角站在讲台', 'image_prompt', ask=ask)
        self.assertEqual(result['beats'][0]['action'], '主角抬手')
        self.assertIn('从静态改为动态', calls[0][0])
        with self.assertRaises(ValueError):
            video_prompt_refresh.revise_motion({}, self.shot(), [], '', '原图', 'image_prompt', ask=ask, refresh_basis='action')
        with patch.object(video_prompt_refresh, 'revise_motion', return_value=self.plan()), \
             patch.object(video_prompt_refresh, 'write_video_prompts', return_value=[{'video_prompt':'主角抬手'}]):
            updated, _ = video_prompt_refresh.refresh({}, '', self.shot(), [], basis='image', action='', image_prompt='原图')
        self.assertEqual(updated['action'], '主角抬手')
        self.assertEqual(updated['image_prompt'], '原图')

    def test_auto_motion_discards_blank_participant_placeholders_only(self):
        dirty = self.plan()
        dirty['participants'] = ['主角', '  ']
        dirty['reference_participants'] = ['主角', '']
        def ask(_system, _payload):
            return {'shots': [{'id': 'shot1', 'motion_plan': dirty}]}
        result = video_prompt_refresh.revise_motion(
            {}, self.shot(), [], '', '主角站在讲台', 'image_prompt', ask=ask)
        self.assertEqual(result['participants'], ['主角'])
        self.assertEqual(result['reference_participants'], ['主角'])
        invalid = self.plan()
        invalid['participants'] = ['主角']
        invalid['reference_participants'] = ['主角']
        invalid['beats'][0]['texts'] = [
            {'text': '你好', 'owner': '', 'container': '气泡'}]
        with self.assertRaisesRegex(ValueError, '文字归属'):
            video_prompt_refresh.revise_motion(
                {}, self.shot(), [], '', '主角站在讲台', 'image_prompt',
                ask=lambda *_: {'shots': [{'id': 'shot1', 'motion_plan': invalid}]})

    def test_auto_motion_cleans_null_object_and_duplicate_name_placeholders(self):
        dirty = self.plan()
        dirty['participants'] = ['主角', None, {}, {'name': ' 主角 '}, {'name': '观众'}]
        dirty['reference_participants'] = [{'name': '主角'}, None, '']
        result = video_prompt_refresh.revise_motion(
            {}, self.shot(), [], '', '主角站在讲台', 'image_prompt',
            ask=lambda *_: {'shots': [{'id': 'shot1', 'motion_plan': dirty}]})
        self.assertEqual(result['participants'], ['主角', '观众'])
        self.assertEqual(result['reference_participants'], ['主角'])

    def test_invalid_generated_names_get_one_bounded_schema_repair(self):
        dirty = self.plan()
        dirty['participants'] = ['主角', '过长' * 100]
        repaired = self.plan()
        calls = []
        def ask(system, payload):
            calls.append((system, payload))
            plan = dirty if len(calls) == 1 else repaired
            return {'shots': [{'id': 'shot1', 'motion_plan': plan}]}
        result = video_prompt_refresh.revise_motion(
            {}, self.shot(), [], '', '主角站在讲台', 'image_prompt', ask=ask)
        self.assertEqual(result['participants'], ['主角'])
        self.assertEqual(len(calls), 2)
        self.assertIn('JSON 格式修复员', calls[1][0])

    def test_auto_action_does_not_invoke_or_overwrite_prompt_finalizers(self):
        shot = self.shot()
        shot['video_prompt'] = '保留视频提示词'
        before = copy.deepcopy(shot)
        with patch.object(video_prompt_refresh, 'revise_motion', return_value=self.plan()), \
             patch.object(video_prompt_refresh, 'write_image_prompts') as image, \
             patch.object(video_prompt_refresh, 'write_video_prompts') as video:
            result, _ = video_prompt_refresh.refresh({}, '', shot, [], basis='image', action='',
                image_prompt=shot['image_prompt'], action_only=True)
        self.assertEqual(result['action'], '主角抬手')
        self.assertEqual(result['video_prompt'], '保留视频提示词')
        self.assertEqual(result['image_prompt'], shot['image_prompt'])
        self.assertEqual(shot, before)
        image.assert_not_called()
        video.assert_not_called()

    def test_action_refresh_updates_both_prompts_only_from_user_action(self):
        shot=self.shot()
        with patch.object(video_prompt_refresh,'revise_motion',return_value=self.plan()) as motion, \
             patch.object(video_prompt_refresh,'write_image_prompts',return_value=[{'id':'shot1','image_prompt':'新图提示'}]) as image, \
             patch.object(video_prompt_refresh,'write_video_prompts',return_value=[{'id':'shot1','video_prompt':'新视频提示'}]) as video:
            result, analysis=video_prompt_refresh.refresh({},'简笔画',shot,[],basis='action',
                action='用户明确要求先抬手再转身',image_prompt='旧图提示')
        self.assertIsNone(analysis)
        self.assertEqual(result['action'],'用户明确要求先抬手再转身')
        self.assertEqual(result['image_prompt'],'新图提示')
        self.assertEqual(result['video_prompt'],'新视频提示')
        self.assertEqual(motion.call_args.args[3],'用户明确要求先抬手再转身')
        self.assertEqual(motion.call_args.kwargs['refresh_basis'], 'action')
        image.assert_called_once(); video.assert_called_once()

    def test_reference_image_refresh_uses_vision_and_only_updates_video_prompt(self):
        shot=self.shot()
        analysis={'fingerprint':'x','description':'实际图片里主角位于右侧','visible_texts':[],'participants':['主角']}
        with patch.object(video_prompt_refresh,'analyze_image',return_value=analysis) as vision, \
             patch.object(video_prompt_refresh,'revise_motion',return_value=self.plan()) as motion, \
             patch.object(video_prompt_refresh,'write_image_prompts') as image, \
             patch.object(video_prompt_refresh,'write_video_prompts',return_value=[{'id':'shot1','video_prompt':'按实图更新'}]):
            result, used=video_prompt_refresh.refresh({},'简笔画',shot,[],basis='image',action='用户动作',
                image_prompt='参考模式下的旧提示词',image_path=Path('any.jpg'),force_vision=True)
        vision.assert_called_once()
        self.assertEqual(motion.call_args.args[4],'实际图片里主角位于右侧')
        self.assertEqual(motion.call_args.args[5],'image_analysis')
        self.assertEqual(motion.call_args.kwargs['refresh_basis'], 'image')
        image.assert_not_called()
        self.assertEqual(result['image_prompt'],'参考模式下的旧提示词')
        self.assertEqual(result['video_prompt'],'按实图更新')
        self.assertEqual(used,analysis)

    def test_manual_speaker_change_does_not_reuse_old_attribution_in_finalizers(self):
        shot = self.shot()
        shot['semantic'] = {'message': '提问与回应', 'speech_turns': [
            {'source_text': '原文', 'speaker': '主角', 'addressee': '观众',
             'mode': 'spoken', 'basis': '旧的自动规划'}]}
        before = copy.deepcopy(shot)
        with patch.object(video_prompt_refresh, 'revise_motion', return_value=self.plan()) as motion, \
             patch.object(video_prompt_refresh, 'write_image_prompts', return_value=[
                 {'id': 'shot1', 'image_prompt': '新图提示'}]) as image, \
             patch.object(video_prompt_refresh, 'write_video_prompts', return_value=[
                 {'id': 'shot1', 'video_prompt': '新视频提示'}]) as video:
            updated, _ = video_prompt_refresh.refresh({}, '简笔画', shot, [], basis='action',
                action='观众向主角提问，主角认真倾听', image_prompt='用户改好的画面')
        self.assertIn('手动纠正', video_prompt_refresh.MOTION_SYSTEM)
        self.assertEqual(motion.call_args.args[3], '观众向主角提问，主角认真倾听')
        self.assertNotIn('speech_turns', image.call_args.args[2][0]['semantic'])
        self.assertNotIn('speech_turns', video.call_args.args[1][0]['semantic'])
        self.assertEqual(updated['semantic']['message'], '提问与回应')
        self.assertEqual(shot, before)

    def test_transparent_png_with_jpg_name_is_flattened_to_real_white_jpeg(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'frame.jpg'
            image=Image.new('RGBA',(8,8),(0,0,0,0))
            image.putpixel((4,4),(255,0,0,255))
            image.save(path,format='PNG')
            self.assertTrue(video_studio._normalize_rgb_image(path,strict=True))
            with Image.open(path) as result:
                self.assertEqual(result.format,'JPEG')
                self.assertEqual(result.mode,'RGB')
                self.assertGreater(min(result.getpixel((0,0))),240)


if __name__=='__main__':
    unittest.main()
