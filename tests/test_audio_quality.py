import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from module1_agent_director import _apply_audio_controls


@unittest.skipUnless(shutil.which("ffmpeg"), "system ffmpeg is required")
class AudioQualityTests(unittest.TestCase):
    def test_default_controls_still_run_peak_safe_output_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(22050)
                output.writeframes(
                    struct.pack("<" + "h" * 4096, *([32767] * 4096))
                )

            _apply_audio_controls(path, speed=1.0, volume=1.0, pitch=0)

            with wave.open(str(path), "rb") as audio:
                payload = audio.readframes(audio.getnframes())
            samples = struct.unpack("<" + "h" * (len(payload) // 2), payload)
            # FFmpeg's limiter is allowed one PCM16 quantization step above
            # the floating-point limit; it remains safely below full scale.
            self.assertLessEqual(max(abs(value) for value in samples), round(0.95 * 32767) + 1)
            self.assertNotIn(32767, samples)

    def test_pitch_path_uses_audible_resampling_without_clipping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.wav"
            samples = [int(30000 * ((index % 32) / 31 * 2 - 1)) for index in range(4096)]
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(22050)
                output.writeframes(struct.pack("<" + "h" * len(samples), *samples))

            _apply_audio_controls(path, speed=1.0, volume=1.0, pitch=2)

            with wave.open(str(path), "rb") as audio:
                payload = audio.readframes(audio.getnframes())
            output_samples = struct.unpack("<" + "h" * (len(payload) // 2), payload)
            self.assertLessEqual(max(abs(value) for value in output_samples), round(0.95 * 32767) + 1)


if __name__ == "__main__":
    unittest.main()
