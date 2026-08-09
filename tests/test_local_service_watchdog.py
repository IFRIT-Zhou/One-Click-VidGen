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

    def test_stale_heartbeat_keeps_services_when_another_launcher_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            heartbeat = Path(temp_dir) / "launcher_own.heartbeat"
            heartbeat.write_text("alive", encoding="utf-8")
            with (
                patch("tools.local_service_watchdog._has_live_peer_heartbeat", return_value=True),
                patch("tools.local_service_watchdog._terminate_process_tree") as terminate,
            ):
                monitor(
                    heartbeat,
                    [101, 202],
                    stale_after=0.02,
                    poll_interval=0.005,
                )
                terminate.assert_not_called()
                self.assertFalse(heartbeat.exists())


if __name__ == "__main__":
    unittest.main()
