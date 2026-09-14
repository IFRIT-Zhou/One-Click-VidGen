import unittest
from pathlib import Path

from tools.create_portable_archive import is_excluded


class PortableArchiveTest(unittest.TestCase):
    def test_lightweight_package_omits_only_local_tts_checkpoints(self) -> None:
        checkpoint = Path("tools/IndexTTS25/checkpoints/gpt.pth")
        torch_runtime = Path("runtime/python/Lib/site-packages/torch/lib/torch_cuda.dll")
        whisper_model = Path("tools/whisper_models/faster-whisper-base/model.bin")

        self.assertFalse(is_excluded(checkpoint))
        self.assertTrue(is_excluded(checkpoint, without_local_tts=True))
        self.assertFalse(is_excluded(torch_runtime, without_local_tts=True))
        self.assertFalse(is_excluded(whisper_model, without_local_tts=True))


if __name__ == "__main__":
    unittest.main()
