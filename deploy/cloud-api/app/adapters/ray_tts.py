from __future__ import annotations

from collections.abc import Iterator
import threading
import time
from typing import Any

import httpx

from ..config import settings


HEALTH_CACHE_TTL_SECONDS = 5.0
_health_probe_lock = threading.Lock()
_health_cache: tuple[float, dict[str, Any]] | None = None


class RayTTSClient:
    def __init__(self) -> None:
        self.base_url = settings.ray_tts_base_url
        self.headers = {"X-API-Key": settings.ray_tts_api_key}
        self.timeout = settings.ray_request_timeout_seconds

    def health(self) -> dict[str, Any]:
        # Serialize health RPCs so browser polling cannot queue redundant
        # probes behind a busy single-GPU model worker.
        global _health_cache
        now = time.monotonic()
        with _health_probe_lock:
            cached = _health_cache
            if cached and now - cached[0] < HEALTH_CACHE_TTL_SECONDS:
                return dict(cached[1])

            # Ray's dependency checks can take five seconds while a replica is
            # busy, so the former hard three-second limit caused false outages.
            timeout = max(10.0, min(float(self.timeout), 15.0))
            with httpx.Client(timeout=timeout) as client:
                response = client.get(f"{self.base_url}/v1/health")
                response.raise_for_status()
                payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Ray health response must be a JSON object")
            if payload.get("ok") is True:
                _health_cache = (time.monotonic(), dict(payload))
            return payload

    def upload_voice(self, *, filename: str, content_type: str, payload: bytes) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/v1/voices",
                headers=self.headers,
                files={"file": (filename, payload, content_type)},
            )
            response.raise_for_status()
            return response.json()

    def delete_voice(self, voice_id: str) -> None:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.delete(
                f"{self.base_url}/v1/voices/{voice_id}", headers=self.headers
            )
            if response.status_code != 404:
                response.raise_for_status()

    def create_job(self, cloud_job_id: str, payload: dict[str, Any]) -> str:
        request = dict(payload)
        request["client_job_id"] = cloud_job_id
        request.pop("gpu_acceleration", None)
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/v1/jobs", json=request, headers=self.headers
            )
            response.raise_for_status()
            return str(response.json()["job_id"])

    def get_job(self, ray_job_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                f"{self.base_url}/v1/jobs/{ray_job_id}", headers=self.headers
            )
            response.raise_for_status()
            return response.json()

    def cancel_job(self, ray_job_id: str) -> None:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.delete(
                f"{self.base_url}/v1/jobs/{ray_job_id}", headers=self.headers
            )
            response.raise_for_status()

    def stream_audio(self, ray_job_id: str, chunk_index: int) -> Iterator[bytes]:
        with httpx.stream(
            "GET",
            f"{self.base_url}/v1/jobs/{ray_job_id}/chunks/{chunk_index}/audio",
            headers=self.headers,
            timeout=self.timeout,
        ) as response:
            response.raise_for_status()
            yield from response.iter_bytes()


ray_tts = RayTTSClient()
