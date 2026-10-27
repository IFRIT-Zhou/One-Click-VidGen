"""Offline preview tests: no production projects, LLMs or paid generation."""
import copy
import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image, ImageDraw
from backend.app import storyboard_preview as preview

ACTIONS = ('主角面对镜头，双手持创可贴与酒精瓶，动作专注地将酒精喷洒在创可贴棉垫上，持续3秒。\n'
           '主角将喷好酒精的创可贴贴在自己的手臂内侧，并用手指轻轻按压固定，持续4秒。\n'
           '主角保持按压手臂创可贴的姿势，画面自然停留至结束。')
PROMPT = ('图1是单张复合内容参考，不是必须照搬的首帧，也不表示所有内容同时存在。'
          '按提示词把图中的元素拆成先后阶段，输出始终是普通全屏视频。\n'
          '本镜保持主角（参考图1）的儿童简笔手绘风格，场景为室内演示桌前，主角服装与造型严格参考图1。\n'
          + ACTIONS + '\n禁止擅自生成任何字幕、自动字幕、底部字幕条、旁白文字转写、标题卡或水印。'
          '只有阶段方案明确批准的少量场景内文字可以按原文出现；不得把配音、旁白、动作说明或提示词复制成屏幕字幕。')


def wave_file(path, seconds):
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\0\0' * round(seconds * 16000))


class PreviewTextTests(unittest.TestCase):
    def test_user_example_keeps_only_three_action_paragraphs(self):
        shot = dict(kind='video', video_prompt=PROMPT, h3_prompt='DO NOT DISPLAY H3')
        self.assertEqual(preview.effective_action(shot), ACTIONS)

    def test_editable_prompt_wins_over_stale_plan(self):
        shot = dict(kind='video', video_prompt='主角放下杯子。',
                    motion_plan=dict(beats=[dict(action='主角拿起杯子。')]))
        self.assertEqual(preview.effective_action(shot), '主角放下杯子。')

    def test_static_has_no_motion_caption_and_missing_prompt_uses_beats(self):
        self.assertEqual(preview.effective_action(dict(kind='static', video_prompt=PROMPT)), '')
        self.assertEqual(preview.effective_action(dict(kind='video',
            motion_plan=dict(beats=[dict(action='手掌摊开。'), dict(action='手掌收回。')]))), '手掌摊开。\n手掌收回。')

    def test_legacy_action_strips_stage_labels_and_display_boilerplate(self):
        shot = dict(kind='video', action='场景基础：室内\n第1阶段：小人抬手。\n当前可见文字：无。\n第2阶段：小人坐下。')
        self.assertEqual(preview.effective_action(shot), '小人抬手。\n小人坐下。')

    def test_overlay_badges_transparency_and_pagination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.png'
            Image.new('RGB', (1920, 1080), 'white').save(source)
            for kind, color in [('video', (35, 153, 87)), ('static', (207, 65, 73))]:
                frames = preview.annotated_frames(source, root / kind, kind=kind,
                    note=ACTIONS if kind == 'video' else '', size=(1920, 1080))
                with Image.open(frames[0]) as image:
                    self.assertEqual(image.getpixel((62, 32)), color)
                    # Outside the < 1/4 panel and away from the badge is untouched.
                    self.assertEqual(image.getpixel((500, 500)), (255, 255, 255))
                    if kind == 'video':
                        self.assertEqual(image.getpixel((1800, 925)), (51, 51, 51))
            pages = preview.annotated_frames(source, root / 'long', kind='video',
                note=ACTIONS*20, size=(1080, 1920), transparency=80)
            self.assertGreater(len(pages), 1)
            self.assertTrue(all(p.is_file() for p in pages))


class PreviewRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        wave_file(self.root / 'latest.wav', 2.3)
        wave_file(self.root / 'old.wav', 1)
        (self.root / 'latest.srt').write_text('1\n00:00:00,000 --> 00:00:02,300\n最终字幕\n', encoding='utf-8')
        Image.new('RGB', (160, 90), '#dfdfdf').save(self.root / 'image.png')
        self.record = dict(id='a'*32, revision=7, status='image_review', settings=dict(ratio='16:9'),
            audio='latest.wav', subtitles='latest.srt', logs=[], export=dict(raw='existing-final.mp4'),
            shots=[dict(id='one', kind='video', start=0, end=2.3, image='image.png',
                        image_status='completed', video_status='pending', video_prompt=PROMPT)])
        for obj, name, value in [(preview.studio, 'directory', lambda *args: self.root),
                                 (preview.studio, 'require_user', lambda request: {'id': 1}),
                                 (preview.studio, 'read', lambda path: copy.deepcopy(self.record)),
                                 (preview.studio, 'save', self.save)]:
            mock = patch.object(obj, name, value)
            mock.start(); self.addCleanup(mock.stop)
        preview.RUNNING.discard(str(self.root))
        self.addCleanup(lambda: preview.RUNNING.discard(str(self.root)))

    def save(self, path, record):
        self.record = copy.deepcopy(record)

    def start(self, **kwargs):
        with patch.object(preview.threading, 'Thread') as thread:
            result = preview.start_preview(self.record['id'], preview.PreviewRequest(revision=7, **kwargs), None)
            return result, thread.call_args.kwargs['args']

    def test_freezes_final_audio_images_and_does_not_require_motion_or_change_status(self):
        original = copy.deepcopy(self.record)
        result, args = self.start(notes={'one': '人工修改的说明'})
        root = args[1]
        self.assertEqual((root/'audio.wav').read_bytes(), (self.root/'latest.wav').read_bytes())
        self.assertEqual((root/'subtitles.srt').read_bytes(), (self.root/'latest.srt').read_bytes())
        self.assertEqual(result['status'], 'image_review')
        self.assertEqual(result['shots'], original['shots'])
        self.assertEqual(result['export'], original['export'])
        self.assertEqual(result['revision'], 7)
        frozen = (root/'image_0000.png').read_bytes()
        Image.new('RGB', (160, 90), 'red').save(self.root/'image.png')
        self.assertEqual((root/'image_0000.png').read_bytes(), frozen)
        self.assertEqual(args[3].notes['one'], '人工修改的说明')

    def test_completion_merges_only_preview_metadata_and_preserves_later_edits(self):
        _, args = self.start()
        self.record['revision'] = 8
        self.record['shots'][0]['video_prompt'] = '后续新提示词'
        original = copy.deepcopy(self.record)
        output = args[1]/'分镜预览.mp4'
        output.write_bytes(b'test')
        with patch.object(preview, 'render_preview', return_value=output):
            preview._worker(*args)
        self.assertEqual(self.record['storyboard_preview_task']['status'], 'completed')
        self.assertEqual(self.record['shots'], original['shots'])
        self.assertEqual(self.record['export'], original['export'])
        self.assertEqual(self.record['revision'], 8)
        self.assertEqual(self.record['status'], original['status'])
        self.assertNotIn(str(self.root), preview.RUNNING)

    def test_failure_retains_last_successful_preview(self):
        self.record['storyboard_preview'] = {'file': 'storyboard_previews/old/final.mp4'}
        _, args = self.start()
        with patch.object(preview, 'render_preview', side_effect=ValueError('测试失败')):
            preview._worker(*args)
        self.assertEqual(self.record['storyboard_preview_task']['status'], 'failed')
        self.assertEqual(self.record['storyboard_preview']['file'], 'storyboard_previews/old/final.mp4')

    def test_precise_missing_image_error_and_revision_guard(self):
        self.record['shots'][0]['image'] = 'missing.png'
        with self.assertRaises(HTTPException) as caught:
            self.start()
        self.assertIn('第 01 镜', caught.exception.detail)
        self.record['revision'] = 8
        with self.assertRaises(HTTPException) as caught:
            self.start()
        self.assertEqual(caught.exception.status_code, 409)

    def test_notes_cannot_reference_missing_shot_and_second_job_cannot_start(self):
        with self.assertRaises(HTTPException) as caught:
            self.start(notes={'other': '无效'})
        self.assertEqual(caught.exception.status_code, 400)
        self.start()
        with self.assertRaises(HTTPException) as caught:
            self.start()
        self.assertIn('正在导出', caught.exception.detail)

    def test_restart_recovery_keeps_project_status_and_previous_preview(self):
        self.record['storyboard_preview_task'] = {'status': 'running'}
        result = preview.get_plan(self.record['id'], None)
        self.assertEqual(result['task']['status'], 'failed')
        self.assertEqual(self.record['status'], 'image_review')
        self.assertEqual(self.record['revision'], 7)

    def test_download_checks_auth_and_preview_directory(self):
        self.record['storyboard_preview'] = {'file': 'image.png'}
        with self.assertRaises(HTTPException) as caught:
            preview.preview_file(self.record['id'], None)
        self.assertEqual(caught.exception.status_code, 404)
        with patch.object(preview.studio, 'require_user', side_effect=HTTPException(401, 'login')):
            with self.assertRaises(HTTPException) as caught:
                preview.get_plan(self.record['id'], None)
        self.assertEqual(caught.exception.status_code, 401)


class PreviewRenderTests(unittest.TestCase):
    def test_real_ffmpeg_landscape_portrait_final_duration_and_narration(self):
        # Keep an optional synthetic output for visual QA; never use user project assets.
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(os.environ.get('OCV_PREVIEW_TEST_OUTPUT') or temporary)
            for ratio in ('16:9', '9:16'):
                root = parent/ratio.replace(':', '-')
                root.mkdir(parents=True, exist_ok=True)
                wave_file(root/'audio.wav', 3.1)
                (root/'subtitles.srt').write_text('1\n00:00:00,000 --> 00:00:01,300\n这是最终配音的字幕。\n\n2\n00:00:01,500 --> 00:00:03,100\n动静分镜预览测试。\n', encoding='utf-8')
                for i, color in enumerate(('#e7efe9', '#ece3d7')):
                    image = Image.new('RGB', (960, 540), color)
                    draw = ImageDraw.Draw(image)
                    draw.ellipse((110, 90, 350, 330), fill='#7db4a5')
                    draw.rectangle((350, 280, 650, 440), fill='#587c8d')
                    image.save(root/f'image_{i:04d}.png')
                snapshot = dict(settings=dict(ratio=ratio), shots=[
                    dict(id='a', kind='video', start=0, end=1.3, video_prompt=PROMPT),
                    dict(id='b', kind='static', start=1.5, end=3.0)])
                result = preview.render_preview(root, snapshot, preview.PreviewRequest(revision=1))
                self.assertTrue(result.is_file())
                self.assertAlmostEqual(preview.probe_media_duration(result), 3.1, delta=.1)
                # Pause belongs to preceding shot; rounded boundaries do not accumulate.
                self.assertAlmostEqual(preview.probe_media_duration(root/'clip_0000_000.mp4'), 1.5, delta=.05)
                self.assertAlmostEqual(preview.probe_media_duration(root/'clip_0001_000.mp4'), 1.6, delta=.05)
                self.assertTrue((root/'subtitles.ass').is_file())


if __name__ == '__main__':
    unittest.main()
