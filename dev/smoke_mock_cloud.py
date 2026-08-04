"""Real-HTTP smoke test for the local mock cloud and production client."""

from __future__ import annotations

import tempfile
import time
import wave
import os
from pathlib import Path

import requests

from backend.app.cloud_client import CloudClient, CloudConfig, CloudSessionStore


BASE_URL = os.getenv("OCV_MOCK_SMOKE_BASE_URL", "http://127.0.0.1:8030/api/v1").rstrip("/")


def main() -> None:
    requests.post(f"{BASE_URL}/mock/reset", timeout=5).raise_for_status()
    client = CloudClient(
        999,
        config=CloudConfig(base_url=BASE_URL, poll_interval=0.2, max_wait_seconds=20, retry_count=0),
        session_store=CloudSessionStore(),
    )
    session = client.login("demo@example.com", "demo12345")
    assert session["authenticated"] is True
    account = client.account_summary()
    assert account["credits"]["available"] == 5000
    voices = client.list_voices()
    voice_id = voices["items"][0]["id"]
    chunks = [{"index": 0, "text": "这是一句真实 HTTP 模拟云端测试文案。"}]
    quote = client.quote({"chunks": chunks, "voice": {"type": "preset", "id": voice_id}})
    assert quote["estimated_credits"] >= 1
    job = client.create_job(
        {"client_job_id": "smoke-local", "chunks": chunks, "voice": {"type": "preset", "id": voice_id}},
        idempotency_key="smoke-local:v1",
    )
    job_id = job["job_id"]
    deadline = time.monotonic() + 10
    while job["status"] not in {"completed", "failed", "cancelled"} and time.monotonic() < deadline:
        time.sleep(0.25)
        job = client.get_job(job_id)
    assert job["status"] == "completed", job
    with tempfile.TemporaryDirectory() as directory:
        destination = Path(directory) / "chunk.wav"
        client.download_to(job["result"]["chunks"][0]["audio_url"], destination)
        with wave.open(str(destination), "rb") as audio:
            assert audio.getframerate() == 24000
            assert audio.getnchannels() == 1
            assert audio.getnframes() > 0
    print(f"OK: login, credits, voices, quote, job and WAV download passed ({job_id})")


if __name__ == "__main__":
    main()
