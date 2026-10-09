from __future__ import annotations

from typing import Any

import httpx

from ..config import settings
from ..exceptions import UpstreamAuthError, UpstreamBusyError, UpstreamPermanentError


class ModelPoolAdapter:
    def configured(self) -> bool:
        return bool(settings.model_pool_api_keys and settings.model_pool_models)

    def complete(
        self,
        *,
        slot: int,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not self.configured():
            raise UpstreamBusyError("Model pool is not configured")
        api_key = settings.model_pool_api_keys[slot % len(settings.model_pool_api_keys)]
        models = settings.model_pool_models
        requested = str(payload.get("model") or "auto")
        candidates = list(models) if requested == "auto" else [requested]
        last_error: Exception | None = None
        for model in candidates:
            upstream_payload = dict(payload)
            upstream_payload["model"] = model
            try:
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                if idempotency_key:
                    headers["Idempotency-Key"] = idempotency_key
                response = httpx.post(
                    f"{settings.model_pool_base_url}/chat/completions",
                    headers=headers,
                    json=upstream_payload,
                    timeout=settings.model_pool_timeout_seconds,
                )
            except httpx.HTTPError as exc:
                last_error = exc
                continue
            if response.status_code in {401, 403}:
                raise UpstreamAuthError(account_slot=slot)
            if response.status_code == 402:
                raise UpstreamBusyError(
                    "Text model account has insufficient upstream balance",
                    account_slot=slot,
                    upstream_status=response.status_code,
                )
            if response.status_code in {408, 409, 429, 500, 502, 503, 504}:
                last_error = UpstreamBusyError(account_slot=slot, upstream_status=response.status_code)
                continue
            if response.status_code >= 400:
                raise UpstreamPermanentError(
                    "Text model rejected the request",
                    upstream_status=response.status_code,
                )
            try:
                result = response.json()
            except ValueError as exc:
                last_error = UpstreamBusyError("Text model returned invalid JSON", account_slot=slot)
                continue
            choices = result.get("choices") if isinstance(result, dict) else None
            usage = result.get("usage") if isinstance(result, dict) else None
            if not isinstance(choices, list) or not choices or not isinstance(usage, dict):
                last_error = UpstreamBusyError("Text model returned an invalid completion", account_slot=slot)
                continue
            return result
        if isinstance(last_error, UpstreamBusyError):
            raise last_error
        raise UpstreamBusyError("Text model is temporarily unavailable", account_slot=slot) from last_error


model_pool = ModelPoolAdapter()
