import copy
import tempfile
import threading
import unittest
from pathlib import Path
from contextlib import ExitStack, nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from backend.app import shot_image_continuity as continuity
from backend.app import video_studio as studio
from backend.app import codex_bridge
from backend.app import codex_image_generation as bridge_images


class PreviousShotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        for name in ('previous.jpg', 'material.jpg', 'scene.jpg'):
            (self.path / name).write_bytes(b'image')
        self.source = dict(id='non-contiguous-9', image='previous.jpg', image_status='completed', image_version='v1')
        self.shot = dict(id='split-child-11', image_prompt='【画面内容】延续上一镜的内胆剖面结构。涂层成片剥落。',
                         reference_ids=[])
        self.record = dict(id='project', shots=[self.source, self.shot], references=[], creation_parameters={})

    def test_explicit_positive_and_negative_phrases(self):
        for text in ('延续上一镜的内胆剖面结构', '沿用上一个镜头的角色', '承接前一分镜的场景',
                     '参考上一张分镜图，保持裂缝位置', '与上一镜的结构保持一致'):
            with self.subTest(text=text):
                self.assertTrue(continuity.needs_previous(text))
        for text in ('不延续上一镜', '不要沿用上一镜的结构', '无需参考上一个镜头', '避免参考上一镜',
                     '旁白总结上一镜的问题', '延续写实画风', '画面文字“延续上一镜”', '不要与上一镜一致'):
            with self.subTest(text=text):
                self.assertFalse(continuity.needs_previous(text))

    def test_appends_previous_without_renumbering_and_is_idempotent(self):
        original = copy.deepcopy(self.record)
        material = str((self.path / 'material.jpg').resolve())
        prompt, refs, _ = studio._image_inputs(self.path, self.record, self.shot,
            '参考图1，' + self.shot['image_prompt'], references=[material], use_scene=False)
        self.assertEqual(refs, [material, str((self.path / 'previous.jpg').resolve())])
        self.assertIn('参考图1', prompt)
        self.assertIn('【自动承接参考】图2为上一镜核心图', prompt)
        repeated = studio._image_inputs(self.path, self.record, self.shot, prompt,
                                        references=[material], use_scene=False)
        self.assertEqual(repeated[:2], (prompt, refs))
        self.assertEqual(self.record, original)

    def test_deduplication_and_full_reference_limit(self):
        previous = str((self.path / 'previous.jpg').resolve())
        prompt, refs = continuity.bind_previous(self.path, self.record, self.shot,
            self.shot['image_prompt'], [previous])
        self.assertEqual(refs, [previous])
        self.assertIn('图1为上一镜', prompt)
        with self.assertRaisesRegex(ValueError, '参考图已满'):
            continuity.bind_previous(self.path, self.record, self.shot,
                self.shot['image_prompt'], ['material.jpg'] * 9)
        self.assertEqual(len(continuity.bind_previous(self.path, self.record, self.shot,
            self.shot['image_prompt'], ['material.jpg'] * 8)[1]), 9)

    def test_scene_and_previous_have_distinct_stable_numbers(self):
        self.record['creation_parameters'] = dict(dynamic_text_mode='visual_first', scene_references_enabled=True)
        self.record['scene_assets'] = [dict(id='room', name='室内', image='scene.jpg', image_status='completed')]
        self.shot['scene_reference_id'] = 'room'
        prompt, refs, _ = studio._image_inputs(self.path, self.record, self.shot, self.shot['image_prompt'])
        self.assertEqual([Path(p).name for p in refs], ['scene.jpg', 'previous.jpg'])
        self.assertIn('【场景参考】图1', prompt)
        self.assertIn('【自动承接参考】图2', prompt)
        self.assertEqual(studio._image_inputs(self.path, self.record, self.shot, prompt)[:2], (prompt, refs))

    def test_missing_busy_failed_outside_and_first_shot_are_explicit(self):
        for fields in (dict(image='missing.jpg'), dict(image='../outside.jpg'), dict(image_status='failed'),
                       dict(image_task={'status': 'running'})):
            with self.subTest(fields=fields), patch.dict(self.source, fields), self.assertRaisesRegex(ValueError, '第 02 镜.*第 01 镜'):
                continuity.bind_previous(self.path, self.record, self.shot, self.shot['image_prompt'], [])
        with self.assertRaisesRegex(ValueError, '首镜'):
            continuity.bind_previous(self.path, self.record, self.source, '延续上一镜', [])

    def test_unrelated_video_or_narration_does_not_bind_and_removed_hint_stays_removed(self):
        self.shot.update(image_prompt='新的剖面', video_prompt='延续上一镜的画面', text='延续上一镜')
        self.assertEqual(studio._image_inputs(self.path, self.record, self.shot, self.shot['image_prompt'])[1], [])
        prompt, refs = continuity.bind_previous(self.path, self.record, self.shot,
            '新的剖面\n【自动承接参考】图1为上一镜核心图。', [])
        self.assertEqual((prompt, refs), ('新的剖面', []))

    def test_bridge_input_mapping_and_snapshot_follow_previous_identity(self):
        prompt, refs, _, _, mapping = bridge_images.reference_inputs(self.path, self.record, self.shot)
        self.assertEqual(mapping[0]['kind'], 'previous_shot')
        self.assertEqual(mapping[0]['id'], self.source['id'])
        self.assertEqual(mapping[0]['local_image_number'], 1)
        self.assertEqual(Path(refs[0]).name, 'previous.jpg')
        with patch.object(bridge_images.bridge, 'timeline_token', return_value='timeline'):
            before = bridge_images.target_state(self.path, self.record, self.shot)
            self.source['image_version'] = 'v2'
            self.assertNotEqual(before, bridge_images.target_state(self.path, self.record, self.shot))

    def test_batch_waits_for_actual_predecessor_and_keeps_unrelated_work_parallel(self):
        source_done = threading.Event()
        independent_started = threading.Event()
        self.source.update(image_status='pending')
        independent = dict(id='independent', image_prompt='独立画面')
        self.record['shots'].append(independent)
        order = []
        def generate(index, shot):
            if shot is self.source:
                self.assertTrue(independent_started.wait(2), 'unrelated images should run concurrently')
                shot['image_status'] = 'completed'
                source_done.set()
            elif shot is independent:
                independent_started.set()
            else:
                self.assertTrue(source_done.is_set())
                self.assertEqual(Path(continuity.previous_image_path(self.path, self.record, shot)).name, 'previous.jpg')
            order.append(shot['id'])
            return 'completed'
        continuity.run_with_dependencies(self.record, list(enumerate(self.record['shots'], 1)),
                                         generate, 2, threading.Event())
        self.assertLess(order.index(self.source['id']), order.index(self.shot['id']))
        self.assertEqual(set(order), {self.source['id'], self.shot['id'], independent['id']})

    def test_failed_previous_does_not_submit_dependent_image_and_stop_drops_pending(self):
        self.source['image_status'] = 'pending'
        submitted = []
        errors = []
        def generate(index, shot):
            try:
                studio._image_inputs(self.path, self.record, shot, shot.get('image_prompt', ''))
            except ValueError as exc:
                errors.append(str(exc))
                return 'failed'
            submitted.append(shot['id'])
            shot['image_status'] = 'failed'
            return 'failed'
        continuity.run_with_dependencies(self.record, list(enumerate(self.record['shots'], 1)),
                                         generate, 1, threading.Event())
        self.assertEqual(submitted, [self.source['id']])
        self.assertEqual(len(errors), 1)
        stopped = threading.Event(); stopped.set()
        continuity.run_with_dependencies(self.record, list(enumerate(self.record['shots'], 1)),
                                         lambda *args: self.fail('must not submit'), 2, stopped)

    def test_preview_reports_previous_identity_and_first_shot_error(self):
        self.assertEqual(continuity.dependency_preview(self.record)[self.shot['id']]['shot_id'], self.source['id'])
        self.source['image_prompt'] = '延续上一镜'
        self.assertIn('首镜', continuity.dependency_preview(self.record)[self.source['id']]['error'])

    def test_actual_batch_injects_image_but_does_not_pollute_saved_video_namespace(self):
        import module4_video_render as visual
        from PIL import Image
        self.record.update(revision=1, logs=[], status='image_generating', audio='keep.wav', scenes=[{'text': '保留字幕'}])
        self.source.update(image_prompt='已有核心图', image_material_numbers_bound=True)
        self.shot['image_status'] = 'pending'
        before = copy.deepcopy(self.record)
        calls = []
        def render(macro, pool):
            calls.append(copy.deepcopy(macro['reference_image_paths']))
            self.assertIn('【自动承接参考】图1为上一镜核心图', macro['image_prompt'])
            target = Path(macro['_output_path'])
            Image.new('RGB', (8, 8), 'red').save(target)
            return target
        class InlineThread:
            def __init__(self, target, **kwargs): self.target = target
            def start(self): self.target()
        with ExitStack() as stack:
            for owner, name, value in ((studio, '_prepare_scene_assets', lambda *a: None),
                                      (studio, '_image_language_scope', lambda *a: nullcontext()),
                                      (studio, 'save', lambda *a: None),
                                      (studio.threading, 'Thread', InlineThread),
                                      (visual, 'shared_runninghub_account_pool', lambda *a, **kw: object()),
                                      (visual, '_render_poster_with_retry', render)):
                stack.enter_context(patch.object(owner, name, value))
            # Avoid replacing Thread inside the actual concurrent executor.
            stack.enter_context(patch.object(continuity, 'run_with_dependencies',
                side_effect=lambda record, pending, fn, workers, event: [fn(*item) for item in pending]))
            studio._start_storyboard_images(self.path, self.record, [{'method': 'mock'}])
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.shot['image_status'], 'completed')
        self.assertNotIn('【自动承接参考】', self.shot['image_prompt'])
        self.assertEqual(self.shot['image_prompt'], self.shot['image_applied_prompt'])
        self.assertTrue(any('自动承接参考' in line for line in self.record['logs']))
        for key in ('audio', 'scenes'):
            self.assertEqual(self.record[key], before[key])
        self.assertEqual(self.source, before['shots'][0])

    def test_actual_redraw_keeps_continuity_numbers_request_local(self):
        import module4_video_render as visual
        from PIL import Image
        self.record.update(revision=1, logs=[], status='image_review')
        self.shot.update(image='current.jpg', image_status='completed')
        Image.new('RGB', (8, 8), 'blue').save(self.path / 'current.jpg')
        prompt, refs, _ = studio._image_inputs(self.path, self.record, self.shot, self.shot['image_prompt'])
        def render(macro, pool):
            self.assertIn('自动承接参考', macro['image_prompt'])
            self.assertEqual(macro['reference_image_paths'], refs)
            target = Path(macro['_output_path'])
            Image.new('RGB', (8, 8), 'red').save(target)
            return target
        class InlineThread:
            def __init__(self, target, **kwargs): self.target = target
            def start(self): self.target()
        with ExitStack() as stack:
            for owner, name, value in ((studio, '_image_language_scope', lambda *a: nullcontext()),
                                      (studio, 'save', lambda *a: None),
                                      (studio, 'read', lambda *a: self.record),
                                      (studio, '_invalidate_shot_video', lambda *a: None),
                                      (studio.threading, 'Thread', InlineThread),
                                      (visual, 'shared_runninghub_account_pool', lambda *a, **kw: object()),
                                      (visual, '_render_poster_with_retry', render)):
                stack.enter_context(patch.object(owner, name, value))
            studio._start_storyboard_redraw(self.path, self.record, self.shot,
                SimpleNamespace(prompt=prompt), [{'method': 'mock'}], refs)
        self.assertEqual(self.shot['image_task']['status'], 'completed')
        self.assertNotIn('自动承接参考', self.shot['image_prompt'])
        self.assertEqual(self.shot['image_applied_prompt'], self.shot['image_prompt'])


if __name__ == '__main__':
    unittest.main()
