import json
import os
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).parents[2] / "app"))
from gpu_admission import tts_admission


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        os.environ['GPU_SCHEDULER_ENABLED'] = '1'

    def tearDown(self):
        os.environ.pop('GPU_SCHEDULER_ENABLED', None)
        self.temp.cleanup()

    def write(self, **changes):
        value = {'time': 100, 'scheduler_ready': True, 'queue_ready': True}
        value.update(changes)
        (self.root / 'heartbeat.json').write_text(json.dumps(value))

    def test_fresh_allows_sleeping_model_admission(self):
        self.write()
        self.assertEqual(tts_admission(now=105, state_path=self.root), dict(accepting=True, on_demand=True, scheduler_ready=True, queue_ready=True))

    def test_missing_stale_future_failed_heartbeat_fail_closed(self):
        self.assertFalse(tts_admission(now=105, state_path=self.root)['accepting'])
        for value in [{'time': 80}, {'time': 110}, {'queue_ready': False}, {'scheduler_ready': False}]:
            self.write(**value)
            self.assertFalse(tts_admission(now=105, state_path=self.root)['accepting'])

    def test_disabled_ignores_recent_heartbeat(self):
        self.write()
        os.environ['GPU_SCHEDULER_ENABLED'] = '0'
        self.assertFalse(tts_admission(now=105, state_path=self.root)['accepting'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
