import json
import tempfile
import unittest
import wave
from array import array
from pathlib import Path

from backend.app.cloud_client import CloudConfig
from backend.app.cloud_tts import (
    CLOUD_MAX_CHUNKS,
    _sanitize_pcm16_wav,
    _wav_info,
    assemble_cloud_audio,
    build_job_payload,
    build_quote_payload,
    split_cloud_text,
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
    def test_long_script_is_coalesced_to_cloud_chunk_limit_without_text_loss(self) -> None:
        paragraphs = [
            f"第{index}段用于验证集群报价分块限制，内容保持完整并按原始顺序提交。"
            for index in range(30)
        ]
        source = "\n".join(paragraphs)

        chunks = split_cloud_text(source)

        self.assertLessEqual(len(chunks), CLOUD_MAX_CHUNKS)
        self.assertEqual("".join(chunks), "".join(paragraphs))

    def test_sanitize_pcm16_chunk_removes_full_scale_peak_before_assembly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clipped.wav"
            write_wav(path, 1000, value=32767)

            quality = _sanitize_pcm16_wav(path)

            self.assertEqual(quality["sample_count"], 1000)
            self.assertEqual(quality["clipped_samples"], 1000)
            self.assertLessEqual(quality["peak"], 0.95)
            with wave.open(str(path), "rb") as audio:
                payload = audio.readframes(audio.getnframes())
            samples = array("h")
            samples.frombytes(payload)
            self.assertLessEqual(max(abs(value) for value in samples), round(0.95 * 32767))

    def test_sanitize_pcm16_chunk_is_lossless_when_peak_has_headroom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "safe.wav"
            write_wav(path, 1000, value=12000)
            before = path.read_bytes()

            quality = _sanitize_pcm16_wav(path)

            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(quality["clipped_samples"], 0)
            self.assertLess(quality["peak"], 0.95)

    def test_wav_info_rejects_a_truncated_download(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "truncated.wav"
            write_wav(path, 24000)
            payload = path.read_bytes()
            path.write_bytes(payload[:-100])

            with self.assertRaisesRegex(RuntimeError, "下载不完整"):
                _wav_info(path)

    def test_failed_job_surfaces_remote_error_instead_of_stale_message(self) -> None:
        class FakeClient:
            config = CloudConfig(
                base_url="https://cluster.example/api/v1",
                poll_interval=0.001,
                max_wait_seconds=30,
            )

            def create_job(self, payload, *, idempotency_key):
                return {
                    "job_id": "job_failed_1",
                    "status": "failed",
                    "progress": 0,
                    "message": "Submitting to Ray",
                    "error": "Ray service is unavailable",
                }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "Ray service is unavailable"):
                synthesize_cloud_tts(
                    client=FakeClient(),
                    local_job_id="local-failed-001",
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
                    chunks_override=["测试。"],
                )

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
                if payload["client_job_id"] != idempotency_key:
                    raise AssertionError("Cloud API requires client_job_id to match Idempotency-Key")
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
            self.assertEqual(client.created["client_job_id"], "local-001:tts:v1")
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
