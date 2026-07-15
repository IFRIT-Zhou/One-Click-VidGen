import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app import indextts2_local


class IndexTtsVoiceUploadTest(unittest.TestCase):
    def test_uploaded_reference_audio_is_resolved_inside_user_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "workspace" / "editor" / "user_7" / "uploads" / "abc_voice.wav"
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b"RIFF")
            config = Mock(default_voice="voice_05.wav", examples_dir=root / "examples")
            with patch.object(indextts2_local, "PROJECT_ROOT", root):
                resolved = indextts2_local.resolve_voice_reference(
                    config,
                    "upload:abc_voice.wav",
                    user_id=7,
                )
            self.assertEqual(resolved, audio.resolve())

    def test_uploaded_reference_rejects_path_traversal(self) -> None:
        config = Mock(default_voice="voice_05.wav", examples_dir=Path("examples"))
        with self.assertRaises(ValueError):
            indextts2_local.resolve_voice_reference(
                config,
                "upload:../voice.wav",
                user_id=7,
            )


if __name__ == "__main__":
    unittest.main()
