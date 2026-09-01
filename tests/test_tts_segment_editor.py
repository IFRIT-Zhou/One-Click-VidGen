import json
import tempfile
import time
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import module1_agent_director as module1
from backend.app.tts_editor import (
    TtsEditor,
    _build_regeneration_plan,
    _concat_wavs,
    _rewrite_srt_times,
)
from backend.app.main import TtsSegmentRegenerateRequest


def write_silent_wav(path: Path, duration: float, sample_rate: int = 16000) -> None:
    frames = int(round(duration * sample_rate))
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\0\0" * frames)


class TtsSegmentEditorTest(unittest.TestCase):
    def test_refine_request_accepts_voice_and_emotion_overrides(self) -> None:
        payload = TtsSegmentRegenerateRequest(
            indices=[1, 2],
            tts_text_overrides={1: "点击chong2绘按钮。"},
            tts_voice_id="upload:voice.wav",
            tts_emotion="sad",
            tts_emotion_weight=0,
        )
        self.assertEqual(payload.tts_voice_id, "upload:voice.wav")
        self.assertEqual(payload.tts_emotion_weight, 0)
        self.assertEqual(payload.tts_text_overrides[1], "点击chong2绘按钮。")
        from_frontend = TtsSegmentRegenerateRequest.model_validate({
            "indices": [1],
            "tts_text_overrides": {"1": "点击chong2绘按钮。"},
        })
        self.assertEqual(from_frontend.tts_text_overrides[1], "点击chong2绘按钮。")

    def test_editor_inspect_returns_saved_refine_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            segment_dir = project / "other" / "tts_segments"
            segment_dir.mkdir(parents=True)
            write_silent_wav(segment_dir / "segment_0001.wav", 0.5)
            (segment_dir / "manifest.json").write_text(json.dumps({
                "engine": "indextts25",
                "tts_voice_id": "voice_05.wav",
                "tts_speed": 1.15,
                "tts_emotion": "calm",
                "tts_emotion_weight": 0.8,
                "segments": [{
                    "index": 1, "text": "点击重绘。", "tts_text": "点击chong2绘。", "filename": "segment_0001.wav",
                    "start": 0, "end": 0.5, "duration": 0.5,
                }],
            }, ensure_ascii=False), encoding="utf-8")
            editor = TtsEditor()
            with (
                patch.object(editor, "_project_dir", return_value=project),
                patch.object(editor, "_migrate_legacy_archive", return_value=True),
            ):
                payload = editor.inspect("job", 1)
        self.assertTrue(payload["available"])
        self.assertEqual(payload["settings"]["tts_speed"], 1.15)
        self.assertEqual(payload["settings"]["tts_emotion"], "calm")
        self.assertEqual(payload["settings"]["tts_emotion_weight"], 0.8)
        self.assertEqual(payload["segments"][0]["text"], "点击重绘。")
        self.assertEqual(payload["segments"][0]["tts_text"], "点击chong2绘。")
        self.assertTrue(payload["segments"][0]["pronunciation_modified"])

    def test_indextts25_pronunciation_override_safely_splits_oversized_sentence(self) -> None:
        text = "甲" * 70 + "。" + "乙" * 69 + "。"
        with patch(
            "backend.app.tts_segmentation.build_indextts25_token_counter",
            return_value=len,
        ):
            chunks, positions, totals = _build_regeneration_plan({1: text}, "indextts25")
        self.assertEqual("".join(chunks), text)
        self.assertEqual(positions, {1: [0, 1]})
        self.assertEqual(totals, {1: len(text)})
        self.assertTrue(all(len(chunk) <= 110 for chunk in chunks))

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

    def test_pause_edit_inserts_silence_without_regeneration_and_can_undo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            segment_dir = project / "other" / "tts_segments"
            segment_dir.mkdir(parents=True)
            (project / "input").mkdir()
            write_silent_wav(segment_dir / "segment_0001.wav", 1.0)
            write_silent_wav(segment_dir / "segment_0002.wav", 1.0)
            manifest = {
                "engine": "indextts25",
                "segments": [
                    {"index": 1, "text": "前句。", "filename": "segment_0001.wav", "start": 0, "end": 1, "duration": 1},
                    {"index": 2, "text": "后句。", "filename": "segment_0002.wav", "start": 1, "end": 2, "duration": 1},
                ],
            }
            (segment_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            (project / "other" / "最终字幕.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n前句。\n\n"
                "2\n00:00:01,000 --> 00:00:02,000\n后句。\n",
                encoding="utf-8",
            )
            _concat_wavs(
                [segment_dir / "segment_0001.wav", segment_dir / "segment_0002.wav"],
                project / "input" / "配音.wav",
            )
            editor = TtsEditor()
            job = SimpleNamespace(id="job", request={}, user_id=1)
            with (
                patch.object(editor, "_project_dir", return_value=project),
                patch.object(editor, "_ensure_module1_layout"),
                patch.object(editor, "_migrate_legacy_archive", return_value=True),
                patch("backend.app.tts_editor.store.log"),
            ):
                result = editor.set_pause(job=job, user_id=1, left_index=1, seconds=0.6)
                self.assertEqual(result["history_count"], 1)
                changed = json.loads((segment_dir / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(changed["segments"][0]["pause_after"], 0.6)
                with wave.open(str(project / "input" / "配音.wav"), "rb") as audio:
                    self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), 2.6, places=2)
                subtitle = (project / "other" / "最终字幕.srt").read_text(encoding="utf-8")
                self.assertIn("00:00:01,600 --> 00:00:02,600", subtitle)
                editor.undo(job=job, user_id=1)
                restored = json.loads((segment_dir / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(float(restored["segments"][0].get("pause_after") or 0), 0)
                with wave.open(str(project / "input" / "配音.wav"), "rb") as audio:
                    self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), 2.0, places=2)

    def test_structural_split_revoices_two_parts_and_is_undoable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            segment_dir = project / "other" / "tts_segments"
            segment_dir.mkdir(parents=True)
            (project / "input").mkdir()
            original = segment_dir / "segment_0001.wav"
            generated_left = project / "left.wav"
            generated_right = project / "right.wav"
            write_silent_wav(original, 2.0)
            write_silent_wav(generated_left, 0.8)
            write_silent_wav(generated_right, 1.1)
            manifest = {
                "engine": "qwen",
                "segments": [{
                    "index": 1, "text": "前半句后半句", "tts_text": "前半句后半句",
                    "filename": original.name, "start": 0, "end": 2, "duration": 2,
                }],
            }
            (segment_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            (project / "other" / "最终字幕.srt").write_text(
                "1\n00:00:00,000 --> 00:00:02,000\n前半句后半句\n", encoding="utf-8"
            )
            _concat_wavs([original], project / "input" / "配音.wav")
            editor = TtsEditor()
            job = SimpleNamespace(id="job", request={}, user_id=1)
            with (
                patch.object(editor, "_project_dir", return_value=project),
                patch.object(editor, "_ensure_module1_layout"),
                patch.object(editor, "_synthesize_parts", return_value=[generated_left, generated_right]),
                patch("backend.app.tts_editor.store.log"),
            ):
                editor.resegment(
                    job=job, user_id=1, start_index=1, replace_count=1,
                    parts=[
                        {"text": "前半句", "tts_text": "前半句", "pause_after": 0.6},
                        {"text": "后半句", "tts_text": "后半句", "pause_after": 0},
                    ],
                    settings_override={"tts_speed": 1.15},
                )
                deadline = time.time() + 3
                while editor.status("job")["status"] == "running" and time.time() < deadline:
                    time.sleep(0.01)
                self.assertEqual(editor.status("job")["status"], "completed")
                changed = json.loads((segment_dir / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual([item["text"] for item in changed["segments"]], ["前半句", "后半句"])
                self.assertEqual(changed["segments"][0]["pause_after"], 0.6)
                self.assertEqual(changed["tts_speed"], 1.15)
                with wave.open(str(project / "input" / "配音.wav"), "rb") as audio:
                    self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), 2.5, places=2)
                subtitle = (project / "other" / "最终字幕.srt").read_text(encoding="utf-8")
                self.assertIn("00:00:00,000 --> 00:00:00,800", subtitle)
                self.assertIn("00:00:01,400 --> 00:00:02,500", subtitle)
                editor.undo(job=job, user_id=1)
                restored = json.loads((segment_dir / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(len(restored["segments"]), 1)


if __name__ == "__main__":
    unittest.main()
