import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.pipeline import Job, _segment_archives_match, sync_refined_step_audio_assets


class StepAudioArchiveSyncTest(unittest.TestCase):
    def test_identical_archive_is_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            source = project / "other" / "tts_segments"
            target = root / "jobs" / "test-job" / "artifacts" / "tts_segments"
            source.mkdir(parents=True)
            target.mkdir(parents=True)
            for directory in (source, target):
                (directory / "manifest.json").write_text('{"segments": [1]}', encoding="utf-8")
                (directory / "segment_0001.wav").write_bytes(b"same audio")
            self.assertTrue(_segment_archives_match(source, target))
            job = Job(id="test-job")
            revision = {"fingerprint": "unchanged", "sentence_count": 1}
            with (
                patch("backend.app.pipeline.WORKSPACE_DIR", root / "workspace"),
                patch("backend.app.pipeline.JOBS_DIR", root / "jobs"),
                patch("backend.app.pipeline._step_audio_revision", return_value=revision),
                patch("backend.app.pipeline._copy_file_atomic"),
                patch("backend.app.pipeline._write_json_atomic"),
                patch("backend.app.pipeline.os.replace", side_effect=PermissionError(5, "Access denied")) as replace,
                patch("backend.app.pipeline.store.update"),
            ):
                result = sync_refined_step_audio_assets(job, project)
            self.assertEqual(result, revision)
            replace.assert_not_called()
            self.assertEqual((target / "segment_0001.wav").read_bytes(), b"same audio")

    def test_changed_audio_is_detected_even_when_file_size_is_equal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            (source / "segment_0001.wav").write_bytes(b"new")
            (target / "segment_0001.wav").write_bytes(b"old")
            self.assertFalse(_segment_archives_match(source, target))


if __name__ == "__main__":
    unittest.main()
