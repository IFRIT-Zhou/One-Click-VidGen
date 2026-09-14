import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import portable_preflight


class PortableOptionalTtsTest(unittest.TestCase):
    def test_missing_local_tts_weights_do_not_block_startup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_dir = root / "tools" / "IndexTTS25" / "checkpoints"
            browser_dir = root / "runtime" / "hyperframes" / "chrome"
            required = (
                root / "tools" / "IndexTTS25" / "indextts" / "infer_v2_5.py",
                root / "tools" / "IndexTTS25" / "python_packages" / "whisper" / "__init__.py",
                root / "tools" / "IndexTTS25" / "python_packages" / "tiktoken" / "__init__.py",
                root / "tools" / "IndexTTS25" / "examples" / "voice_05.wav",
                root / "tools" / "whisper_models" / "faster-whisper-base" / "config.json",
                root / "tools" / "whisper_models" / "faster-whisper-base" / "model.bin",
                root / "tools" / "whisper_models" / "faster-whisper-base" / "tokenizer.json",
                root / "tools" / "whisper_models" / "faster-whisper-base" / "vocabulary.txt",
                browser_dir / "chrome-headless-shell.exe",
            )
            for path in required:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test")

            output = io.StringIO()
            with (
                patch.object(portable_preflight, "PROJECT_ROOT", root),
                patch.object(portable_preflight, "MODEL_DIR", model_dir),
                patch.object(
                    portable_preflight,
                    "WHISPER_MODEL_DIR",
                    root / "tools" / "whisper_models" / "faster-whisper-base",
                ),
                patch.object(portable_preflight, "HYPERFRAMES_BROWSER_DIR", browser_dir),
                contextlib.redirect_stdout(output),
            ):
                result = portable_preflight.main()

            self.assertEqual(result, 0)
            self.assertIn("Optional IndexTTS-2.5 model is not installed", output.getvalue())


if __name__ == "__main__":
    unittest.main()
