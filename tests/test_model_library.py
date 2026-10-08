import os
import tempfile
import unittest
import hashlib
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from backend.app import managed_comfyui as engine, indextts25_local as tts, model_library
from tools.create_portable_archive import is_excluded


class ModelLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        for module, key, value in ((engine, "PROJECT", self.root), (engine, "RUNTIME", self.root / "runtime/comfyui"),
                                   (engine, "DATA", self.root / "workspace"), (tts, "PROJECT_ROOT", self.root)):
            p = patch.object(module, key, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(tts, "load_project_env"); p.start(); self.addCleanup(p.stop)
        p = patch.dict(os.environ, {"INDEXTTS25_ROOT": "", "INDEXTTS25_MODEL_DIR": ""}); p.start(); self.addCleanup(p.stop)

    def put(self, relative, data=b"abc"):
        path = self.root / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        return path

    def test_new_tts_install_follows_relocated_root(self):
        self.assertEqual(tts.load_indextts25_config().model_dir, self.root / "models/tts/indextts25")

    def test_old_tts_install_remains_usable_and_new_folder_takes_over(self):
        self.put("tools/IndexTTS25/checkpoints/config.yaml")
        self.assertEqual(tts.load_indextts25_config().model_dir, self.root / "tools/IndexTTS25/checkpoints")
        self.put("models/tts/indextts25/config.yaml")
        self.assertEqual(tts.load_indextts25_config().model_dir, self.root / "models/tts/indextts25")

    def test_explicit_tts_path_retained(self):
        with patch.dict(os.environ, {"INDEXTTS25_MODEL_DIR": "custom/tts"}):
            self.assertEqual(tts.load_indextts25_config().model_dir, self.root / "custom/tts")

    def test_old_template_path_follows_moved_models(self):
        self.put("models/tts/indextts25/config.yaml")
        with patch.dict(os.environ, {"INDEXTTS25_MODEL_DIR": "tools/IndexTTS25/checkpoints"}):
            self.assertEqual(tts.load_indextts25_config().model_dir, self.root / "models/tts/indextts25")

    def test_zero_byte_tts_and_missing_emotion_weights_are_not_ready(self):
        for relative in tts.REQUIRED_MODEL_FILES:
            self.put("models/tts/indextts25/" + relative)
        self.assertTrue(model_library.installation("tts")["ready"])
        self.put("models/tts/indextts25/qwen0.6bemo4-merge/model.safetensors", b"")
        self.assertFalse(model_library.installation("tts")["ready"])
        self.assertTrue(tts.load_indextts25_config().missing_model_resources())

    def test_incomplete_primary_model_cannot_hide_behind_legacy_duplicate(self):
        self.put("models/comfyui/vae/test.bin", b"a")
        self.put("runtime/comfyui/models/vae/test.bin", b"abc")
        self.assertFalse(engine.model_inventory({"models": [{"path": "vae/test.bin", "bytes": 3}]})[0]["ready"])

    def test_workflow_only_checks_its_own_weights(self):
        self.put("models/comfyui/vae/fast.bin")
        manifest = {"models": [{"path": "vae/fast.bin", "bytes": 3}, {"path": "vae/other.bin", "bytes": 3}]}
        profiles = [{"id": "fast", "name": "fast", "required_models": ["vae/fast.bin"]}]
        with patch.object(engine, "runtime_info", return_value=(self.root, manifest)), patch.object(engine, "builtin_profiles", return_value=profiles):
            self.assertTrue(model_library.installation(profile_id="fast")["ready"])
            self.assertFalse(model_library.installation()["ready"])
            with self.assertRaises(HTTPException): model_library.installation(profile_id="unknown")
            with self.assertRaises(HTTPException): model_library.open_folder("comfyui", "fast", "../../secret")

    def test_lightweight_package_excludes_new_model_locations(self):
        self.assertTrue(is_excluded(Path("models/tts/indextts25/gpt.pth"), without_local_tts=True))
        self.assertTrue(is_excluded(Path("models/comfyui/vae/a.safetensors"), without_local_video=True))
        self.assertFalse(is_excluded(Path("models/tts/indextts25/gpt.pth")))

    def test_model_free_package_keeps_both_engine_environments(self):
        for relative in ("models/tts/indextts25/gpt.pth", "models/comfyui/vae/a.safetensors", "tools/IndexTTS25/checkpoints/gpt.pth", "runtime/comfyui/models/vae/a.safetensors"):
            self.assertTrue(is_excluded(Path(relative), without_models=True))
        for relative in ("runtime/comfyui/releases/v1/python/python.exe", "tools/IndexTTS25/indextts/infer_v2_5.py"):
            self.assertFalse(is_excluded(Path(relative), without_models=True))

    def test_full_check_detects_same_size_corruption(self):
        file = self.put("models/comfyui/vae/test.bin", b"bad")
        items = [{"name": "test.bin", "ready": True, "found_path": str(file), "sha256": hashlib.sha256(b"abc").hexdigest()}]
        with patch.object(model_library, "installation", return_value={"items": items}), patch.object(model_library.threading, "Thread", side_effect=lambda target, **kw: SimpleNamespace(start=target)):
            result = model_library.verify_models()
        self.assertFalse(result["running"])
        self.assertEqual(result["items"][0]["state"], "mismatch")


if __name__ == "__main__": unittest.main()
