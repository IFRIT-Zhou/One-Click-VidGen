"""Enforce upload bounds before multipart data reaches the form parser."""
from __future__ import annotations

from fastapi import HTTPException
from starlette.responses import JSONResponse

# Additional upload routes can declare their own cap without changing the wrapper.
UPLOAD_LIMITS = {
    "/api/v1/image-pool/media/upload": 21 * 1024 * 1024,
    "/api/v1/editor/exports": 232 * 1024 * 1024,
    "/api/v1/editor/timeline": 232 * 1024 * 1024,
    "/api/v1/asr/jobs": 51 * 1024 * 1024,
}


class UploadBoundaryMiddleware:
    def __init__(self, app, limits: dict[str, int] | None = None):
        self.app = app
        self.limits = dict(UPLOAD_LIMITS if limits is None else limits)

    async def __call__(self, scope, receive, send):
        limit = self.limits.get(scope.get("path", "").rstrip("/"))
        if scope["type"] != "http" or scope.get("method") != "POST" or limit is None:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))

        async def reject(status, code, message):
            await JSONResponse(
                {"code": code, "message": message, "details": {}}, status_code=status,
            )(scope, receive, send)

        authorization = headers.get(b"authorization", b"").split()
        if len(authorization) != 2 or authorization[0].lower() != b"bearer":
            await reject(401, "UNAUTHORIZED", "Authentication required")
            return
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                size = int(declared)
                if size < 0:
                    raise ValueError
            except ValueError:
                await reject(400, "BAD_REQUEST", "Invalid Content-Length")
                return
            if size > limit:
                await reject(413, "PAYLOAD_TOO_LARGE", "Upload exceeds the request size limit")
                return
        consumed = 0

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    # FastAPI's exception handler turns this into a 413 while its
                    # multipart parser/endpoint never sees the excess body chunk.
                    raise HTTPException(413, "Upload exceeds the request size limit")
            return message

        await self.app(scope, bounded_receive, send)
