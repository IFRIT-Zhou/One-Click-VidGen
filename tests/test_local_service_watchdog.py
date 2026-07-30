import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.local_service_watchdog import monitor


class LocalServiceWatchdogTest(unittest.TestCase):
    def test_stale_heartbeat_terminates_only_supplied_pids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            heartbeat = Path(temp_dir) / "launcher.heartbeat"
            heartbeat.write_text("alive", encoding="utf-8")
            with patch("tools.local_service_watchdog._terminate_process_tree") as terminate:
                worker = threading.Thread(
                    target=monitor,
                    args=(heartbeat, [101, 202]),
                    kwargs={"stale_after": 0.05, "poll_interval": 0.01},
                )
                worker.start()
                worker.join(timeout=1)
                self.assertFalse(worker.is_alive())
                self.assertEqual([call.args[0] for call in terminate.call_args_list], [101, 202])


if __name__ == "__main__":
    unittest.main()
