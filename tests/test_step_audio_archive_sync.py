import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.pipeline import Job, _segment_archives_match, _sync_segment_archive_files, sync_refined_step_audio_assets


class StepAudioArchiveSyncTest(unittest.TestCase):
    def test_changed_archive_never_renames_open_directory(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source, target = root / 'source', root / 'target'
            source.mkdir(); target.mkdir()
            for path, content in ((source, b'new'), (target, b'old')):
                (path / 'segment.wav').write_bytes(content)
                (path / 'manifest.json').write_bytes(content)
            real_replace = os.replace
            def deny_directory_replace(src, dst):
                if Path(src).is_dir():
                    raise PermissionError(5, 'directory in use')
                return real_replace(src, dst)
            with patch('backend.app.pipeline.os.replace', side_effect=deny_directory_replace):
                _sync_segment_archive_files(source, target)
            self.assertEqual((target / 'segment.wav').read_bytes(), b'new')
            self.assertEqual((target / 'manifest.json').read_bytes(), b'new')

    def test_failed_commit_restores_old_checkpoint(self):
        import backend.app.pipeline as pipeline
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source, target = root / 'source', root / 'target'
            source.mkdir(); target.mkdir()
            for path, content in ((source, b'new'), (target, b'old')):
                (path / 'segment.wav').write_bytes(content)
                (path / 'manifest.json').write_bytes(content)
            original = pipeline._copy_file_atomic
            def fail_manifest(src, dst):
                if src == source / 'manifest.json':
                    raise PermissionError(5, 'busy')
                original(src, dst)
            with patch('backend.app.pipeline._copy_file_atomic', side_effect=fail_manifest):
                with self.assertRaises(PermissionError):
                    _sync_segment_archive_files(source, target)
            self.assertEqual((target / 'segment.wav').read_bytes(), b'old')
            self.assertEqual((target / 'manifest.json').read_bytes(), b'old')

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
