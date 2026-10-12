import tempfile
import unittest
from pathlib import Path
from backend.app.video_generation import _next_confirmed_attempt


class AttemptAllocationTests(unittest.TestCase):
    def test_preserves_orphan_requests_and_uses_new_number(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            old = path / 'assets/videos/shot/attempt_003'
            old.mkdir(parents=True)
            request = old / 'request.json'
            request.write_text('original', encoding='utf-8')
            shot = dict(id='shot', video_attempt=2, video_history=[{'video_attempt': 1}])
            self.assertEqual(_next_confirmed_attempt(path, shot), 4)
            self.assertEqual(request.read_text(encoding='utf-8'), 'original')
            self.assertEqual(shot['video_attempt'], 2)

    def test_history_high_water_mark(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(_next_confirmed_attempt(Path(folder), dict(id='shot', video_attempt=1, video_history=[{'video_attempt': 7}])), 8)
