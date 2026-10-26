import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image

from backend.app.presenter_mode import presenter_config, audio_policy
from backend.app.presenter_refresh import inspect_speaker, prepare_presenter
from backend.app import video_studio as studio


class PresenterRefreshTests(unittest.TestCase):
    def test_local_override_without_project_reference(self):
        original = {'id': 's', 'kind': 'video', 'source_subtitles': [{'text': '原文'}], 'image': 'x.png', 'duration': 6}
        updated = prepare_presenter(original, {'speaker_description': '左侧医生', 'image_fingerprint': 'abc'})
        self.assertEqual(audio_policy({}, updated, True, []), (True, True))
        self.assertEqual(updated['source_subtitles'], original['source_subtitles'])
        self.assertEqual(updated['image'], original['image'])
        self.assertNotIn('presenter_override', original)
        updated['presenter_override']['enabled'] = False
        self.assertEqual(audio_policy({}, updated, True, []), (False, False))

    def test_generated_character_mode_needs_no_reference(self):
        params = {'presenter_mode': True, 'presenter_reference_id': '__shot__'}
        self.assertEqual(presenter_config(params, [])['source'], 'shot')
        shot = {'visual_design': {'presenter_visible': True, 'presenter_speaking': True}}
        self.assertEqual(audio_policy(params, shot, True, []), (True, True))
        shot['visual_design']['presenter_speaking'] = False
        self.assertEqual(audio_policy(params, shot, True, []), (False, False))

    def test_vision_rejects_ambiguous_or_hidden_speaker(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / 'image.png'
            Image.new('RGB', (32, 32)).save(image)
            for result in ({'speaker_count': 2, 'selected_unambiguous': True, 'mouth_visible': True, 'speaker_description': '医生'},
                           {'speaker_count': 1, 'selected_unambiguous': True, 'mouth_visible': False, 'speaker_description': '医生'}):
                with patch('backend.app.presenter_refresh.generate_gemini_text', return_value='{}'), patch('backend.app.presenter_refresh.parse_json_response', return_value=result):
                    with self.assertRaises(ValueError):
                        inspect_speaker(image)

    def test_endpoint_preserves_other_shots_audio_and_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'assets').mkdir()
            (root / 'assets/audio.wav').write_bytes(b'audio')
            core = root / 'image.png'
            Image.new('RGB', (32, 32)).save(core)
            shot = {'id': 's', 'kind': 'video', 'image_status': 'completed', 'image': 'image.png',
                    'video': 'old.mp4', 'video_status': 'completed', 'source_subtitles': [{'text': '不变'}], 'duration': 6}
            record = {'id': 'p', 'revision': 1, 'status': 'completed', 'shots': [shot, {'id': 'other'}],
                      'creation_parameters': {'video_generation_backend': 'comfyui'}, 'logs': [], 'references': []}
            before = copy.deepcopy(record)
            with patch.object(studio, 'require_user', return_value={'id': 1}), patch.object(studio, 'directory', return_value=root), \
                 patch.object(studio, 'read', return_value=record), patch.object(studio, 'save'), \
                 patch.object(studio, 'shot_editable', return_value=shot), patch.object(studio, '_storyboard_image_path', return_value=core), \
                 patch.object(studio, 'planning_parameters', side_effect=lambda r:r['creation_parameters']), \
                 patch('backend.app.comfyui_bridge.video_profile', return_value={'mappings': {'audio': {'node_id': '1', 'input_name': 'audio'}}}), \
                 patch.object(studio, 'project_language_scope'), patch('backend.app.presenter_refresh.inspect_speaker', return_value={'speaker_description': '医生'}):
                result = studio.configure_shot_presenter('p', 's', studio.PresenterEdit(revision=1), SimpleNamespace())
            self.assertEqual(result['shots'][1], before['shots'][1])
            self.assertEqual(shot['image'], before['shots'][0]['image'])
            self.assertEqual(shot['source_subtitles'], before['shots'][0]['source_subtitles'])
            self.assertEqual(shot['video_history'][0]['video'], 'old.mp4')
            self.assertFalse(studio.IMAGE_EDITS)

if __name__ == '__main__':
    unittest.main()
