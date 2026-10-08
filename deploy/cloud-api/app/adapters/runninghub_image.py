from __future__ import annotations

import ipaddress
import socket
from decimal import Decimal, InvalidOperation
from typing import Any, Iterator
from urllib.parse import urlparse

import httpx

from ..config import settings
from ..exceptions import UpstreamAuthError, UpstreamBusyError, UpstreamPermanentError


SUCCESS_STATES = {"SUCCESS", "SUCCEEDED", "COMPLETED", "COMPLETE", "FINISHED"}
RUNNING_STATES = {"", "RUNNING", "QUEUED", "PENDING", "SUBMITTED"}


def _first(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and item is not None and item != "":
                return item
        for item in value.values():
            found = _first(item, keys)
            if found is not None and found != "":
                return found
    elif isinstance(value, list):
        for item in value:
            found = _first(item, keys)
            if found is not None and found != "":
                return found
    return None


def _error_code(payload: Any, status_code: int) -> int:
    raw = _first(payload, {"code", "errorCode"})
    try:
        return int(raw)
    except (TypeError, ValueError):
        return status_code


def _non_negative_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _actual_cost_credits(payload: Any) -> str | None:
    coins = _non_negative_decimal(_first(payload, {"consumeCoins"}))
    if coins is not None:
        return format(coins, "f")
    upstream_usd = _non_negative_decimal(_first(payload, {"thirdPartyConsumeMoney"}))
    if upstream_usd is not None:
        return format(upstream_usd * settings.billing_usd_to_cny_rate, "f")
    legacy = _non_negative_decimal(_first(payload, {
        "actualCostCredits", "actualCostCoins", "costCredits", "costCoins",
        "consumedCredits", "consumePower", "consumedPower", "powerCost",
    }))
    return format(legacy, "f") if legacy is not None else None


def _raise_upstream(payload: Any, status_code: int) -> None:
    code = _error_code(payload, status_code)
    if code in {401, 403, 1014}:
        raise UpstreamAuthError(upstream_code=code)
    if code == 40310:
        raise UpstreamAuthError(
            "Image account is not valid for the configured RunningHub region",
            upstream_code=code,
        )
    if code in {414, 416, 812}:
        raise UpstreamPermanentError(
            "Image account has insufficient upstream balance",
            # The worker already rotates/deactivates depleted accounts on 414.
            # Preserve the v2 provider code separately while using that stable
            # internal category for the newer balance codes.
            upstream_code=414,
            provider_code=code,
        )
    if code == 1501:
        raise UpstreamPermanentError(
            "Image request failed content verification",
            upstream_code=code,
        )
    if code == 1504:
        raise UpstreamBusyError(
            "Image generation timed out upstream",
            upstream_code=code,
        )
    if code in {408, 409, 421, 429, 500, 502, 503, 504, 1005, 1010, 1011, 1012}:
        raise UpstreamBusyError(upstream_code=code)
    raise UpstreamPermanentError("Image provider rejected the request", upstream_code=code)


class RunningHubImageAdapter:
    def configured(self) -> bool:
        return bool(settings.runninghub_image_api_keys)

    def _key(self, slot: int) -> str:
        if not self.configured():
            raise UpstreamBusyError("Image pool is not configured")
        return settings.runninghub_image_api_keys[slot % len(settings.runninghub_image_api_keys)]

    @staticmethod
    def _url(endpoint: str) -> str:
        if endpoint.startswith(("http://", "https://")):
            return endpoint
        return f"{settings.runninghub_image_base_url}/{endpoint.lstrip('/')}"

    def _json_request(self, method: str, endpoint: str, *, slot: int, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self._key(slot)}"
        try:
            response = httpx.request(
                method,
                self._url(endpoint),
                headers=headers,
                timeout=settings.image_pool_request_timeout_seconds,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise UpstreamBusyError("Image provider is temporarily unreachable") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstreamBusyError("Image provider returned invalid JSON") from exc
        if response.status_code >= 400 or not isinstance(payload, dict):
            _raise_upstream(payload, response.status_code)
        code = _error_code(payload, response.status_code)
        if code not in {0, 200}:
            _raise_upstream(payload, response.status_code)
        return payload

    def submit(self, *, slot: int, job_id: str, payload: dict[str, Any]) -> str:
        del job_id  # Idempotency is enforced by cloud-api and is not an upstream field.
        endpoint = (
            settings.runninghub_image_to_image_endpoint
            if payload.get("imageUrls")
            else settings.runninghub_text_to_image_endpoint
        )
        upstream = {
            key: payload[key]
            for key in ("prompt", "aspectRatio", "resolution")
            if key in payload
        }
        if payload.get("imageUrls"):
            upstream["imageUrls"] = payload["imageUrls"]
        result = self._json_request(
            "POST", endpoint, slot=slot,
            headers={"Content-Type": "application/json"},
            json=upstream,
        )
        task_id = _first(result, {"taskId", "taskID", "id"})
        if not task_id:
            raise UpstreamBusyError("Image provider did not return a task id")
        return str(task_id)

    def query(self, *, slot: int, task_id: str) -> dict[str, Any]:
        result = self._json_request(
            "POST", settings.runninghub_query_endpoint, slot=slot,
            headers={"Content-Type": "application/json"}, json={"taskId": task_id},
        )
        raw_status = _first(result, {"status", "state", "taskStatus"})
        state = str(raw_status or "").upper()
        image_url = _first(result, {"fileUrl", "fileURL", "imageUrl", "imageURL", "downloadUrl", "downloadURL", "url"})
        actual_cost = _actual_cost_credits(result)
        if state in SUCCESS_STATES or image_url:
            if not image_url:
                raise UpstreamBusyError("Image provider completed without a result URL")
            return {"status": "SUCCESS", "image_url": str(image_url), "actual_cost_credits": actual_cost}
        if state in RUNNING_STATES:
            return {"status": "RUNNING" if state not in {"QUEUED", "PENDING"} else "QUEUED"}
        code = _error_code(result, 400)
        if code in {414, 1014}:
            _raise_upstream(result, 400)
        raise UpstreamPermanentError("Image generation failed", upstream_code=code)

    def upload(self, *, slot: int, filename: str, content_type: str, payload: bytes) -> str:
        result = self._json_request(
            "POST", settings.runninghub_media_upload_endpoint, slot=slot,
            files={"file": (filename, payload, content_type)},
        )
        url = _first(result, {"download_url", "downloadUrl", "fileUrl", "url"})
        if not url:
            raise UpstreamBusyError("Image provider upload did not return a URL")
        return str(url)

    def stream_result(self, *, slot: int, url: str) -> tuple[Iterator[bytes], str]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise UpstreamPermanentError("Image provider returned an invalid download URL")
        host = parsed.hostname.lower().rstrip(".")
        provider_url = urlparse(settings.runninghub_image_base_url)
        provider_host = (provider_url.hostname or "").lower().rstrip(".")
        allowed_hosts = {provider_host}
        allowed_hosts.update(item.lower().rstrip(".") for item in settings.runninghub_download_hosts if item.strip())
        if not host or host not in allowed_hosts:
            raise UpstreamPermanentError("Image download host is not allowlisted")
        allowed_ports = {None, 80, 443, provider_url.port}
        if parsed.port not in allowed_ports:
            raise UpstreamPermanentError("Image download port is not allowed")
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)}
        except socket.gaierror as exc:
            raise UpstreamBusyError("Image download host could not be resolved") from exc
        if not addresses:
            raise UpstreamBusyError("Image download host could not be resolved")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise UpstreamPermanentError("Image download host resolves to a non-public address")
        client = httpx.Client(timeout=settings.image_pool_request_timeout_seconds)
        headers = {}
        if host == provider_host:
            headers["Authorization"] = f"Bearer {self._key(slot)}"
        request = client.build_request("GET", url, headers=headers)
        response = client.send(request, stream=True)
        if response.status_code >= 300:
            response.close()
            client.close()
            _raise_upstream({}, response.status_code)

        def chunks() -> Iterator[bytes]:
            try:
                yield from response.iter_bytes()
            finally:
                response.close()
                client.close()

        return chunks(), response.headers.get("content-type", "application/octet-stream")


runninghub_image = RunningHubImageAdapter()
