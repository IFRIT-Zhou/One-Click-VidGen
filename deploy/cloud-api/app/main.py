from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api import router
from .password_recovery import router as password_recovery_router
from .asr_api import router as asr_router, start_cleanup as start_asr_cleanup
from .editor_api import router as editor_router, start_cleanup
from .editor_timeline import router as timeline_router
from .config import settings
from .db import Base, engine
from .exceptions import ServiceError
from .upload_boundary import UploadBoundaryMiddleware

settings.validate()
logging.basicConfig(level=logging.INFO)

@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.auto_create_schema:
        Base.metadata.create_all(engine)
    cleanup_stop = start_cleanup()
    asr_cleanup_stop = start_asr_cleanup()
    try:
        yield
    finally:
        cleanup_stop.set()
        asr_cleanup_stop.set()

app = FastAPI(title="Ray Cloud Acceleration API", version="0.1.0", docs_url="/api/v1/docs", openapi_url="/api/v1/openapi.json", lifespan=lifespan)
app.include_router(router)
app.include_router(password_recovery_router)
app.include_router(asr_router)
app.include_router(editor_router)
app.include_router(timeline_router)
app.add_middleware(UploadBoundaryMiddleware)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex}"


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "request_id": _request_id(request), "details": exc.details},
    )


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    codes = {
        400: "BAD_REQUEST", 401: "UNAUTHORIZED", 402: "INSUFFICIENT_CREDITS",
        403: "FORBIDDEN", 404: "NOT_FOUND", 409: "CONFLICT",
        413: "PAYLOAD_TOO_LARGE", 415: "UNSUPPORTED_MEDIA_TYPE",
        422: "VALIDATION_ERROR", 429: "RATE_LIMITED", 502: "BAD_GATEWAY",
        503: "SERVICE_UNAVAILABLE",
    }
    detail = exc.detail
    if isinstance(detail, dict):
        code = str(detail.get("code") or codes.get(exc.status_code, "REQUEST_FAILED"))
        message = str(detail.get("message") or "Request failed")
        details = detail.get("details") if isinstance(detail.get("details"), dict) else {}
    else:
        code = codes.get(exc.status_code, "REQUEST_FAILED")
        message = str(detail or "Request failed")
        details = {}
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": code, "message": message, "request_id": _request_id(request), "details": details},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {key: value for key, value in item.items() if key in {"type", "loc", "msg"}}
        for item in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR", "message": "Request validation failed",
            "request_id": _request_id(request),
            "details": {"errors": errors},
        },
    )

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex}"
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception:
        logging.exception("Unhandled request error: %s", request_id)
        response = JSONResponse(status_code=500, content={"code": "INTERNAL_ERROR", "message": "Internal server error", "request_id": request_id, "details": {}})
    response.headers["X-Request-ID"] = request_id
    return response
