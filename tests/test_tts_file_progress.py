import tempfile
import threading
import time
import unittest
from pathlib import Path

from module1_agent_director import _watch_generated_wavs


class TtsFileProgressTest(unittest.TestCase):
    def test_reports_stable_wav_without_waiting_for_cli_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wav_path = Path(temp_dir) / "chunk-0001.wav"
            reported: list[tuple[int, str]] = []
            reported_event = threading.Event()
            stop_event = threading.Event()

            def report(index: int, text: str) -> None:
                reported.append((index, text))
                reported_event.set()

            watcher = threading.Thread(
                target=_watch_generated_wavs,
                args=([(3, "测试句子", wav_path)], report, stop_event),
                kwargs={"poll_seconds": 0.02},
                daemon=True,
            )
            watcher.start()
            time.sleep(0.03)
            wav_path.write_bytes(b"RIFF" + (b"\0" * 96))

            self.assertTrue(reported_event.wait(1), "WAV 文件生成后没有及时上报进度")
            time.sleep(0.08)
            stop_event.set()
            watcher.join(timeout=1)
            self.assertEqual(reported, [(3, "测试句子")])


if __name__ == "__main__":
    unittest.main()
