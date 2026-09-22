import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import video_studio as studio


class VideoEditSettingsTest(unittest.TestCase):
    def test_workflow_change_preserves_assets_and_style_change_preserves_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            identity = 'a' * 32
            record = dict(id=identity, revision=1, status='completed',
                          settings=dict(name='demo', style='old', characters='', world='', dynamic_text_mode='visual_first'),
                          creation_parameters=dict(dynamic_text_mode='visual_first', scene_references_enabled=True),
                          audio='assets/audio.wav', scenes=[{'slide_id':'scene_001','text':'hello'}],
                          shots=[dict(id='shot1', kind='video', video='clip.mp4', image='core.png', video_status='completed')],
                          logs=[], export={'raw':'final.mp4'})
            with patch.object(studio, 'ROOT', root), patch.object(studio, 'require_user', return_value={'id':1}):
                path = studio.directory(1, identity)
                studio.save(path, record)
                profile = {'mappings': {'audio': {'node_id':'238','input_name':'audio'}}}
                with patch('backend.app.comfyui_bridge.video_profile', return_value=profile):
                    payload = studio.ProjectSettingsEdit(revision=1, name='demo', style='old', video_generation_backend='comfyui', comfyui_profile_id='new', comfyui_reference_audio=True)
                    updated = studio.edit_project_settings(identity, payload, None)
                    self.assertEqual(updated['shots'], record['shots'])
                    self.assertEqual(updated['export'], record['export'])
                    self.assertTrue(updated['creation_parameters']['comfyui_reference_audio'])
                    payload = payload.model_copy(update={'revision':updated['revision'], 'style':'new'})
                    revised = studio.edit_project_settings(identity, payload, None)
                self.assertEqual(revised['audio'], record['audio'])
                self.assertEqual(revised['scenes'], record['scenes'])
                self.assertEqual(revised['status'], 'draft')
                self.assertFalse(revised['shots'])
                self.assertEqual(revised['edit_history'][-1]['shots'], record['shots'])


if __name__ == '__main__':
    unittest.main()
