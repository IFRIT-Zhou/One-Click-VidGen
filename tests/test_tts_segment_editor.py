import json
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import module1_agent_director as module1
from backend.app.tts_editor import TtsEditor, _concat_wavs, _rewrite_srt_times


def write_silent_wav(path: Path, duration: float, sample_rate: int = 16000) -> None:
    frames = int(round(duration * sample_rate))
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\0\0" * frames)


class TtsSegmentEditorTest(unittest.TestCase):
    def test_legacy_module1_srt_recovers_exact_sentence_wavs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "jobs"
            project = root / "output" / "project"
            artifact_dir = jobs / "legacyjob" / "artifacts"
            artifact_dir.mkdir(parents=True)
            (project / "input").mkdir(parents=True)
            (project / "other").mkdir(parents=True)
            write_silent_wav(project / "input" / "配音.wav", 2.0)
            (artifact_dir / "final_output.srt").write_text(
                "1\n00:00:00,000 --> 00:00:00,750\n第一句。\n\n"
                "2\n00:00:00,750 --> 00:00:02,000\n第二句。\n",
                encoding="utf-8",
            )
            with patch("backend.app.tts_editor.JOBS_DIR", jobs):
                self.assertTrue(TtsEditor._migrate_legacy_archive("legacyjob", project))
            manifest = json.loads((project / "other" / "tts_segments" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual([item["text"] for item in manifest["segments"]], ["第一句。", "第二句。"])
            self.assertAlmostEqual(manifest["segments"][0]["duration"], 0.75, places=3)

    def test_module1_archives_exact_chunks_and_processed_wavs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.wav"
            second = root / "second.wav"
            archive = root / "archive"
            write_silent_wav(first, 0.5)
            write_silent_wav(second, 0.75)
            args = SimpleNamespace(
                tts_engine="indextts2", tts_voice_id="voice.wav", tts_speed=1,
                tts_volume=1, tts_pitch=0, tts_emotion="", qwen_voice="", qwen_instructions="",
            )
            module1.export_tts_segments(archive, ["第一句。", "第二句。"], [first, second], args)
            manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual([item["text"] for item in manifest["segments"]], ["第一句。", "第二句。"])
            self.assertAlmostEqual(manifest["segments"][1]["start"], 0.5, places=3)
            self.assertTrue((archive / "segment_0002.wav").is_file())

    def test_changed_sentence_duration_warps_subtitle_times_and_rebuilds_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.wav"
            second = root / "second.wav"
            output = root / "merged.wav"
            subtitle = root / "subtitle.srt"
            write_silent_wav(first, 1.0)
            write_silent_wav(second, 2.0)
            _concat_wavs([first, second], output)
            with wave.open(str(output), "rb") as audio:
                self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), 3.0, places=3)
            subtitle.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n第一句\n\n"
                "2\n00:00:01,000 --> 00:00:02,000\n第二句\n",
                encoding="utf-8",
            )
            # Simulate sentence one growing from 1s to 2s; all later cues move.
            _rewrite_srt_times(subtitle, lambda value: value * 2 if value <= 1 else value + 1)
            rewritten = subtitle.read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:02,000", rewritten)
            self.assertIn("00:00:02,000 --> 00:00:03,000", rewritten)


if __name__ == "__main__":
    unittest.main()
