import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app.presenter_mode import audio_policy, presenter_config, with_optional_h3_audio
from backend.app.video_plan import normalize_shots
from backend.app.video_agents import design_core_images
from backend.app.comfyui_bridge import _patch_graph, WorkflowMappings
from backend.app import h3_prompt_agent as h3
from test_h3_prompt_authority import INLINE


class PresenterModeTests(unittest.TestCase):
    def setUp(self):
        self.parameters = {'presenter_mode': True, 'presenter_reference_id': 'person'}
        self.shot = {'id': 's', 'kind': 'video', 'slide_ids': ['a'], 'reference_ids': ['person'],
                     'visual_design': {'presenter_visible': True, 'presenter_speaking': True},
                     'video_prompt': '讲解员自然开口讲述', 'start': 1, 'duration': 5,
                     'source_subtitles': [{'text': '大家好，今天介绍胃黏膜。'}]}

    def test_h3_presenter_gets_audio_but_ordinary_h3_does_not(self):
        self.assertEqual(audio_policy(self.parameters, self.shot, True), (True, True))
        self.assertEqual(audio_policy({'comfyui_reference_audio': True}, self.shot, True), (False, False))
        self.assertEqual(audio_policy({'comfyui_reference_audio': True}, self.shot, False), (True, True))

    def test_only_real_presenter_and_manual_switches_are_respected(self):
        for changes in ({'reference_ids': []}, {'visual_design': {'presenter_visible': False, 'presenter_speaking': True}},
                        {'visual_design': {'presenter_visible': True, 'presenter_speaking': False}},
                        {'reference_audio_enabled': False}, {'reference_audio_lipsync': False}):
            with self.subTest(changes=changes):
                self.assertEqual(audio_policy(self.parameters, dict(self.shot, **changes), True), (False, False))

    def test_flags_survive_normalization(self):
        shot = normalize_shots([self.shot], [{'slide_id': 'a', 'text': '讲解', 'start': 1, 'end': 6}], ['person'])[0]
        self.assertEqual(audio_policy(self.parameters, shot, True), (True, True))

    def test_imported_and_legacy_reference_ids_resolve(self):
        parameters = dict(self.parameters, reference_image_ids=['person'], reference_image_labels={'person': '图2'})
        for refs in ([{'id': 'ref_01', 'source_asset_id': 'person'}], [{'id': 'ref_01', 'label': '图2'}]):
            self.assertEqual(presenter_config(parameters, refs)['reference_id'], 'ref_01')
            self.assertEqual(audio_policy(parameters, dict(self.shot, reference_ids=['ref_01']), True, refs), (True, True))
        self.assertEqual(presenter_config(parameters, [{'id': 'ref_01', 'label': '图3'}]), {})

    def test_enable_mode_does_not_clear_existing_storyboard(self):
        from backend.app import video_studio as studio
        record = dict(id='p', revision=1, status='image_review', references=[{'id': 'person'}],
                      creation_parameters={'dynamic_text_mode': 'visual_first', 'scene_references_enabled': True},
                      settings={'style': '', 'characters': '', 'world': ''}, shots=[copy.deepcopy(self.shot)], logs=[])
        data = studio.ProjectSettingsEdit(revision=1, name='test', presenter_mode=True, presenter_reference_id='person')
        with patch.object(studio, 'require_user', return_value={'id': 1}), \
             patch.object(studio, 'directory', return_value=Path('unused')), \
             patch.object(studio, 'read', return_value=record), \
             patch.object(studio, 'editable'), patch.object(studio, 'save'):
            result = studio.edit_project_settings('p', data, SimpleNamespace())
        self.assertEqual(result['shots'], [self.shot])
        self.assertEqual(result['status'], 'image_review')
        self.assertEqual(result['context']['video_direction']['presenter']['reference_id'], 'person')

    def test_image_refresh_retains_only_presenter_flags_not_stale_design(self):
        from backend.app.video_prompt_refresh import refresh
        shot = copy.deepcopy(self.shot)
        shot['visual_design']['expression'] = '过期的左右分屏'
        with patch('backend.app.video_prompt_refresh.revise_motion', return_value={'beats': [{'action': '讲解员自然讲话'}]}), \
             patch('backend.app.video_prompt_refresh.write_video_prompts', return_value=[{'video_prompt': '讲话'}]) as writer:
            result, _ = refresh({'video_direction': {'presenter': {'reference_id': 'person'}}}, '', shot, [],
                                basis='image', action='', image_prompt='讲解员')
        self.assertTrue(result['visual_design']['presenter_speaking'])
        self.assertNotIn('expression', writer.call_args.args[1][0]['visual_design'])

    def test_core_agent_retries_missing_presenter_decision(self):
        context = {'video_direction': {'presenter': presenter_config(self.parameters, [{'id': 'person'}])}}
        good = dict(id='s', visual_description='讲解员出镜', reference_ids=['person'],
                    visual_design=self.shot['visual_design'])
        ask = Mock(side_effect=[{'shots': [dict(good, visual_design={})]}, {'shots': [good]}])
        rows = design_core_images(context, [], [self.shot], [{'id': 'person'}], ask=ask)
        self.assertTrue(rows[0]['visual_design']['presenter_speaking'])
        self.assertEqual(ask.call_count, 2)
        self.assertIn('讲解员模式', ask.call_args.args[0])

    def test_optional_workflow_audio_is_really_connected_and_removed_when_unused(self):
        original = {'workflow': {'1': {'class_type': 'MiniMaxH3ReferenceToVideo', 'inputs': {'prompt': 'x'}}},
                    'mappings': {'prompt': {'node_id': '1', 'input_name': 'prompt'}}}
        profile = with_optional_h3_audio(original)
        self.assertNotIn('ocv_presenter_audio', original['workflow'])
        mappings = WorkflowMappings(**profile['mappings'])
        args = dict(prompt='test', duration=5, width=1344, height=768, seed=1)
        graph = _patch_graph(profile['workflow'], mappings, audio_name='voice.wav', **args)
        self.assertEqual(graph['1']['inputs']['ref_audios.ref_audio_0'], ['ocv_presenter_audio', 0])
        self.assertEqual(graph['ocv_presenter_audio']['inputs']['audio'], 'voice.wav')
        silent = _patch_graph(profile['workflow'], mappings, **args)
        self.assertNotIn('ocv_presenter_audio', silent)
        self.assertNotIn('ref_audios.ref_audio_0', silent['1']['inputs'])

    def test_orphan_companion_audio_repaired_but_paired_video_preserved(self):
        graph = {'1': {'class_type': 'MiniMaxH3ReferenceToVideo', 'inputs': {'prompt': '', 'ref_video_audios.ref_video_audio_0': ['2', 0]}},
                 '2': {'class_type': 'LoadAudio', 'inputs': {'audio': ''}}}
        mappings = WorkflowMappings(prompt={'node_id': '1', 'input_name': 'prompt'}, audio={'node_id': '2', 'input_name': 'audio'})
        args = dict(prompt='x', duration=5, width=1280, height=720, seed=1, audio_name='voice.wav')
        result = _patch_graph(graph, mappings, **args)
        self.assertIn('ref_audios.ref_audio_0', result['1']['inputs'])
        graph['1']['inputs']['ref_videos.ref_video_0'] = ['3', 0]
        result = _patch_graph(graph, mappings, **args)
        self.assertIn('ref_video_audios.ref_video_audio_0', result['1']['inputs'])

    def test_h3_receives_exact_script_and_invalidates_cache(self):
        shot = dict(self.shot, reference_audio_digest='audio1')
        with patch.object(h3, 'generate_gemini_text', return_value=json.dumps({'h3_prompt': INLINE})) as ask:
            prompt, fingerprint = h3.convert_for_h3(shot, 10, reference_audio=True, lipsync=True)
        payload = json.loads(ask.call_args.kwargs['user_prompt'])
        self.assertEqual(payload['spoken_text'], '大家好，今天介绍胃黏膜。')
        self.assertIn(payload['spoken_text'], h3._section_body(prompt, 'subject_definitions'))
        self.assertIn('lip-sync to <Audio 1>', prompt)
        self.assertIn('never closed-mouth narration', prompt)
        for key, value in [('reference_audio_digest', 'audio2'), ('start', 2), ('source_subtitles', [{'text': '新的台词'}])]:
            changed = copy.deepcopy(shot)
            changed[key] = value
            self.assertNotEqual(fingerprint, h3.source_fingerprint(changed, 10, reference_audio=True, lipsync=True))


if __name__ == '__main__':
    unittest.main()
