"""ICAN GPT Image 2.5 only. Synchronous upstream, never retry a POST implicitly."""
from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import socket
import time
from urllib.parse import urlparse

import httpx

from ..config import settings
from ..exceptions import ServiceError

BASE_URL = "https://ican-gpt.com"


def image_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ServiceError("ICAN_IMAGE_INVALID", "只支持 PNG、JPEG、WebP 图片", 415)


class IcanImageAdapter:
    def configured(self):
        return bool(settings.ican_image_api_keys)

    def _key(self, slot):
        if not self.configured() or not 0 <= slot < len(settings.ican_image_api_keys):
            raise ServiceError("ICAN_NOT_CONFIGURED", "ICAN 尚未配置", 503)
        return settings.ican_image_api_keys[slot]

    def _request(self, method, path, *, slot, **kwargs):
        limit = settings.ican_image_max_bytes * 2 + 1024 * 1024
        started = time.monotonic()
        try:
            with httpx.Client(timeout=httpx.Timeout(settings.ican_image_timeout_seconds, connect=15),
                              follow_redirects=False) as client:
                with client.stream(method, BASE_URL + path,
                                   headers={"Authorization": "Bearer " + self._key(slot)}, **kwargs) as response:
                    if response.status_code != 200:
                        code = response.status_code
                        category = "ICAN_OUTCOME_UNKNOWN" if code >= 500 else "ICAN_UPSTREAM_REJECTED"
                        # Never include upstream bodies/headers: they can contain keys or image URLs.
                        raise ServiceError(category, "ICAN 请求未完成", 502, {"upstream_status": code})
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > limit or time.monotonic() - started > settings.ican_image_timeout_seconds:
                            raise ServiceError("ICAN_OUTCOME_UNKNOWN", "ICAN 响应超时或超过大小限制", 502)
                        chunks.append(chunk)
            payload = json.loads(b"".join(chunks))
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except httpx.HTTPError:
            raise ServiceError("ICAN_OUTCOME_UNKNOWN", "ICAN 连接中断，未自动重新提交", 502) from None
        except (ValueError, UnicodeError):
            raise ServiceError("ICAN_OUTCOME_UNKNOWN", "ICAN 返回格式无效，未自动重新提交", 502) from None

    def models(self, *, slot=0):
        data = self._request("GET", "/v1/models", slot=slot).get("data", [])
        if not isinstance(data, list):
            raise ServiceError("ICAN_MODELS_INVALID", "ICAN 模型列表无效", 502)
        return sorted({row["id"] for row in data if isinstance(row, dict)
                       and isinstance(row.get("id"), str)
                       and (row["id"] == "gpt-image-2.5" or row["id"].startswith("gpt-image-2.5-"))})

    def _download(self, url):
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port not in (None, 443)
                or host not in settings.ican_image_download_hosts):
            raise ServiceError("ICAN_RESULT_HOST_DENIED", "ICAN 图片下载域名未配置", 502)
        try:
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise ServiceError("ICAN_RESULT_HOST_DENIED", "图片下载地址无效", 502)
            # Signed output URLs receive NO ICAN authorization, and redirects are not followed.
            started = time.monotonic()
            with httpx.stream("GET", url, timeout=60, follow_redirects=False) as response:
                if response.status_code != 200:
                    raise ServiceError("ICAN_RESULT_DOWNLOAD_FAILED", "图片转存失败", 502)
                parts, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > settings.ican_image_max_bytes or time.monotonic() - started > 60:
                        raise ServiceError("ICAN_RESULT_TOO_LARGE", "图片超过大小限制", 502)
                    parts.append(chunk)
                return b"".join(parts)
        except (httpx.HTTPError, OSError, ValueError):
            raise ServiceError("ICAN_RESULT_DOWNLOAD_FAILED", "图片转存失败", 502) from None

    def generate(self, *, slot, payload, references):
        if payload["model"] not in self.models(slot=slot):
            raise ServiceError("ICAN_MODEL_UNAVAILABLE", "当前 ICAN Key 未开通该 Image 2.5 模型", 503)
        body = {key: payload[key] for key in ("model", "prompt", "size")}
        body["response_format"] = "b64_json"
        if references:
            body["images"] = [{"image_url": "data:" + image_type(data) + ";base64," +
                               base64.b64encode(data).decode("ascii")} for data in references]
        path = "/v1/images/edits" if references else "/v1/images/generations"
        result = self._request("POST", path, slot=slot, json=body)
        items = result.get("data")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise ServiceError("ICAN_RESULT_INVALID", "ICAN 未返回单张图片", 502)
        item = items[0]
        encoded = item.get("b64_json")
        if isinstance(encoded, str) and encoded:
            if len(encoded) > (settings.ican_image_max_bytes + 2) // 3 * 4:
                raise ServiceError("ICAN_RESULT_TOO_LARGE", "图片超过大小限制", 502)
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise ServiceError("ICAN_RESULT_INVALID", "ICAN 图片编码无效", 502) from None
        elif isinstance(item.get("url"), str):
            data = self._download(item["url"])
        else:
            raise ServiceError("ICAN_RESULT_INVALID", "ICAN 未返回图片内容", 502)
        if not data or len(data) > settings.ican_image_max_bytes:
            raise ServiceError("ICAN_RESULT_INVALID", "ICAN 图片大小无效", 502)
        return data, image_type(data)


ican_image = IcanImageAdapter()
