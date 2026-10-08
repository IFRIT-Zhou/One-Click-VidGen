from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ServiceError(Exception):
    code: str
    message: str
    status_code: int = 400
    details: dict[str, Any] = field(default_factory=dict)


class UpstreamAuthError(ServiceError):
    def __init__(self, message: str = "Upstream authentication failed", **details: Any) -> None:
        super().__init__("UPSTREAM_AUTH_ERROR", message, 502, details)


class UpstreamBusyError(ServiceError):
    def __init__(self, message: str = "Upstream service busy", **details: Any) -> None:
        super().__init__("UPSTREAM_BUSY", message, 503, details)


class UpstreamPermanentError(ServiceError):
    def __init__(self, message: str = "Upstream request rejected", **details: Any) -> None:
        super().__init__("UPSTREAM_REJECTED", message, 400, details)
