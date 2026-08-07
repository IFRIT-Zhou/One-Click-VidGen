import json
import tempfile
import unittest
import wave
from pathlib import Path

from backend.app.cloud_client import CloudConfig
from backend.app.cloud_tts import (
    assemble_cloud_audio,
    build_job_payload,
    build_quote_payload,
    synthesize_cloud_tts,
)


def write_wav(path: Path, frames: int, value: int = 1000) -> None:
    sample = int(value).to_bytes(2, byteorder="little", signed=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(sample * frames)


class CloudTtsTest(unittest.TestCase):
    def test_payload_normalizes_custom_voice_for_deployed_cloud_contract(self) -> None:
        request = {
            "script": "这是一段足够长的集群配音测试文案，用于确认报价和任务参数保持一致。",
            "cluster_voice_type": "custom",
            "cluster_voice_id": "voice_usr_123",
            "tts_speed": 1.1,
            "tts_volume": 0.9,
            "tts_pitch": 2,
            "tts_emotion": "calm",
        }
        quote = build_quote_payload(request)
        job = build_job_payload(request, [item["text"] for item in quote["chunks"]], client_job_id="local-001")

        self.assertEqual(quote["voice"], {"type": "uploaded", "id": "voice_usr_123"})
        self.assertEqual(job["client_job_id"], "local-001")
        self.assertEqual(job["voice"], quote["voice"])
        self.assertNotIn("scheduling", job)
        self.assertEqual(job["emotion"]["name"], "calm")

    def test_assemble_writes_wav_srt_and_editable_segment_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.wav"
            second = root / "second.wav"
            write_wav(first, 24000)
            write_wav(second, 12000)

            result = assemble_cloud_audio(
                ["第一句。", "第二句。"],
                [first, second],
                output_dir=root / "output",
                segment_archive_dir=root / "segments",
                manifest_metadata={"cloud_job_id": "job_cloud_1"},
            )

            with wave.open(result["audio_path"], "rb") as audio:
                self.assertEqual(audio.getnframes(), 36000)
            subtitle = Path(result["subtitle_path"]).read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:01,000", subtitle)
            self.assertIn("00:00:01,000 --> 00:00:01,500", subtitle)
            manifest = json.loads((root / "segments" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["engine"], "cluster")
            self.assertEqual(manifest["cloud_job_id"], "job_cloud_1")
            self.assertEqual(len(manifest["segments"]), 2)
            self.assertTrue((root / "segments" / "segment_0002.wav").is_file())

    def test_orchestrator_submits_idempotently_downloads_in_index_order_and_finishes(self) -> None:
        class FakeClient:
            config = CloudConfig(
                base_url="https://cluster.example/api/v1",
                poll_interval=0.01,
                max_wait_seconds=30,
            )

            def __init__(self) -> None:
                self.created = None
                self.idempotency_key = ""

            def create_job(self, payload, *, idempotency_key):
                self.created = payload
                self.idempotency_key = idempotency_key
                return {
                    "job_id": "job_remote_1",
                    "status": "completed",
                    "progress": 100,
                    "reserved_credits": 5,
                    "consumed_credits": 4,
                    "released_credits": 1,
                    "result": {
                        "chunks": [
                            {"index": 1, "audio_url": "/api/v1/cloud/jobs/job_remote_1/chunks/1/audio"},
                            {"index": 0, "audio_url": "/api/v1/cloud/jobs/job_remote_1/chunks/0/audio"},
                        ]
                    },
                }

            def download_to(self, url, destination):
                index = 1 if "/1/audio" in url else 0
                write_wav(destination, 12000 if index else 24000, value=1000 + index)

        client = FakeClient()
        events = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = synthesize_cloud_tts(
                client=client,
                local_job_id="local-001",
                request={
                    "script": "unused",
                    "cluster_voice_type": "preset",
                    "cluster_voice_id": "voice_01.wav",
                    "tts_parallelism": 2,
                },
                output_dir=root / "output",
                segment_archive_dir=root / "segments",
                temp_dir=root / "temp",
                is_cancelled=lambda: False,
                on_progress=lambda percent, message: events.append((percent, message)),
                on_log=lambda message: events.append(("log", message)),
                on_remote_job=lambda job_id, payload: events.append((job_id, payload["status"])),
                chunks_override=["第一句。", "第二句。"],
            )

            self.assertEqual(client.idempotency_key, "local-001:tts:v1")
            self.assertEqual(client.created["client_job_id"], "local-001")
            self.assertEqual([item["index"] for item in client.created["chunks"]], [0, 1])
            self.assertTrue(Path(result["audio_path"]).is_file())
            with wave.open(result["audio_path"], "rb") as audio:
                self.assertEqual(audio.getnframes(), 36000)
            self.assertIn(("job_remote_1", "completed"), events)

    def test_orchestrator_downloads_each_chunk_before_parent_job_completes(self) -> None:
        class FakeClient:
            config = CloudConfig(
                base_url="https://cluster.example/api/v1",
                poll_interval=0.001,
                max_wait_seconds=30,
            )

            def __init__(self) -> None:
                self.downloaded_indexes: list[int] = []

            def create_job(self, payload, *, idempotency_key):
                return {
                    "job_id": "job_progressive_1",
                    "status": "running",
                    "progress": 50,
                    "completed_chunks": 1,
                    "reserved_credits": 5,
                    "result": {
                        "chunks": [
                            {"index": 1, "audio_url": "/api/v1/cloud/jobs/job_progressive_1/chunks/1/audio"},
                        ]
                    },
                }

            def get_job(self, job_id):
                self.assert_parent_chunk_already_downloaded()
                return {
                    "job_id": job_id,
                    "status": "completed",
                    "progress": 100,
                    "completed_chunks": 2,
                    "reserved_credits": 5,
                    "consumed_credits": 5,
                    "released_credits": 0,
                    "result": {
                        "chunks": [
                            {"index": 0, "audio_url": f"/api/v1/cloud/jobs/{job_id}/chunks/0/audio"},
                            {"index": 1, "audio_url": f"/api/v1/cloud/jobs/{job_id}/chunks/1/audio"},
                        ]
                    },
                }

            def assert_parent_chunk_already_downloaded(self):
                if self.downloaded_indexes != [1]:
                    raise AssertionError("客户端没有在父任务完成前下载已就绪分块")

            def download_to(self, url, destination):
                index = 1 if "/1/audio" in url else 0
                self.downloaded_indexes.append(index)
                write_wav(destination, 12000 if index else 24000, value=1000 + index)

        client = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = synthesize_cloud_tts(
                client=client,
                local_job_id="local-progressive-001",
                request={
                    "script": "unused",
                    "cluster_voice_type": "preset",
                    "cluster_voice_id": "voice_01.wav",
                },
                output_dir=root / "output",
                segment_archive_dir=root / "segments",
                temp_dir=root / "temp",
                is_cancelled=lambda: False,
                on_progress=lambda *_: None,
                on_log=lambda *_: None,
                on_remote_job=lambda *_: None,
                chunks_override=["第一句。", "第二句。"],
            )

            self.assertEqual(client.downloaded_indexes, [1, 0])
            with wave.open(result["audio_path"], "rb") as audio:
                self.assertEqual(audio.getnframes(), 36000)


if __name__ == "__main__":
    unittest.main()
