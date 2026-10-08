import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.video_generation import _publish_prepared_attempt


class VideoRequestStagingTest(unittest.TestCase):
    def test_transient_windows_lock_retries_without_submitting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            staging = Path(temp_dir) / '.preparing-test'
            target = Path(temp_dir) / 'attempt_001'
            staging.mkdir()
            (staging / 'request.json').write_text('{}', encoding='utf-8')
            original = Path.replace
            attempts = []

            def replace(path, destination):
                attempts.append(destination)
                if len(attempts) == 1:
                    raise PermissionError(5, 'Access denied')
                return original(path, destination)

            with patch.object(Path, 'replace', replace), patch('backend.app.video_generation.time.sleep'):
                _publish_prepared_attempt(staging, target)
            self.assertEqual(len(attempts), 2)
            self.assertTrue((target / 'request.json').is_file())

    def test_locked_request_reports_not_submitted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            staging = Path(temp_dir) / '.preparing-test'
            target = Path(temp_dir) / 'attempt_001'
            staging.mkdir()
            with patch.object(Path, 'replace', side_effect=PermissionError(5, 'Access denied')), \
                 patch('backend.app.video_generation.time.sleep'):
                with self.assertRaisesRegex(ValueError, '尚未提交视频 API'):
                    _publish_prepared_attempt(staging, target)
            self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
