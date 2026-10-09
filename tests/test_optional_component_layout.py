import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import indextts25_local as tts, managed_comfyui as engine
from tools.create_portable_archive import is_excluded


class OptionalComponentLayoutTests(unittest.TestCase):
    def test_old_explicit_tts_defaults_follow_moved_component(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            for name in ('tts/IndexTTS25/examples', 'tts/IndexTTS25/python_packages', 'tts/python', 'models/tts/indextts25'):
                (root / name).mkdir(parents=True)
            (root / 'tts/python/python.exe').touch()
            (root / 'models/tts/indextts25/config.yaml').touch()
            env = {'INDEXTTS25_ROOT': 'tools/IndexTTS25', 'INDEXTTS25_MODEL_DIR': 'tools/IndexTTS25/checkpoints',
                   'INDEXTTS25_EXAMPLES_DIR': 'tools/IndexTTS25/examples', 'INDEXTTS25_PACKAGES_DIR': 'tools/IndexTTS25/python_packages'}
            with patch.object(tts, 'PROJECT_ROOT', root), patch.object(tts, 'load_project_env'), patch.dict(os.environ, env, clear=True):
                config = tts.load_indextts25_config()
                self.assertEqual(config.root, root / 'tts/IndexTTS25')
                self.assertEqual(config.python, root / 'tts/python/python.exe')
                self.assertEqual(config.model_dir, root / 'models/tts/indextts25')
                self.assertEqual(config.examples_dir, root / 'tts/IndexTTS25/examples')
                self.assertEqual(config.packages_dir, root / 'tts/IndexTTS25/python_packages')

    def test_legacy_video_engine_still_resolves(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            release = root / 'runtime/comfyui/releases/old'
            for name in ('python/python.exe', 'ComfyUI/main.py'):
                p = release / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.touch()
            engine.write_json(release / 'component.json', {'version': 'old'})
            engine.write_json(root / 'runtime/comfyui/active.json', {'version': 'old'})
            with patch.object(engine, 'PROJECT', root), patch.object(engine, 'RUNTIME', root / 'comfyui/engine'):
                self.assertEqual(engine.runtime_info()[0], release)

    def test_base_package_omits_optional_code_and_models(self):
        for name in ('comfyui/engine/releases/v/python/python.exe', 'comfyui/custom_nodes/SelfLift/nodes.py',
                     'tts/python/python.exe', 'tts/IndexTTS25/indextts/infer_v2_5.py',
                     'models/tts/indextts25/gpt.pth', 'models/comfyui/vae/model.safetensors'):
            self.assertTrue(is_excluded(Path(name), without_local_tts=True, without_local_video=True))
        self.assertFalse(is_excluded(Path('plugins/codex_bridge/plugin.json'), without_local_tts=True, without_local_video=True))


if __name__ == '__main__':
    unittest.main()
