"""Business contract and private artifact storage for the ICAN image channel."""
from __future__ import annotations

import json
import os
import re
import tempfile
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .adapters.ican_image import ican_image, image_type
from .config import settings
from .exceptions import ServiceError
from .models import ImagePoolJob, ImagePoolMedia, new_id
from .services import json_hash, reserve_image_pool_credits, wallet_for_update


def prices():
    try:
        result = json.loads(settings.ican_image_prices_json)
        if not isinstance(result, dict):
            raise ValueError
        for model, spec in result.items():
            if not re.fullmatch(r"gpt-image-2\.5(?:-[a-zA-Z0-9._-]+)?", model):
                raise ValueError
            if spec["currency"] not in {"CNY", "USD"} or not isinstance(spec["sizes"], dict):
                raise ValueError
            for size, value in spec["sizes"].items():
                if not re.fullmatch(r"[1-9][0-9]{2,3}x[1-9][0-9]{2,3}", size):
                    raise ValueError
                amount = Decimal(str(value))
                if not amount.is_finite() or amount <= 0:
                    raise ValueError
        return result
    except (ValueError, KeyError, TypeError, InvalidOperation):
        raise ServiceError("ICAN_PRICING_INVALID", "ICAN 价格配置无效", 503) from None


def price_quote(model, size):
    spec = prices().get(model)
    if not spec or size not in spec["sizes"]:
        raise ServiceError("ICAN_MODEL_SIZE_NOT_CONFIGURED", "该模型或尺寸尚未配置价格", 422)
    base = Decimal(str(spec["sizes"][size]))
    credits = base * (settings.billing_usd_to_cny_rate if spec["currency"] == "USD" else 1)
    credits = (credits * Decimal(100 + settings.ican_image_markup_percent) / 100).quantize(
        Decimal("0.000001"), rounding=ROUND_CEILING)
    return {"upstream_price": str(base), "currency": spec["currency"], "credits": str(credits),
            "markup_percent": settings.ican_image_markup_percent,
            "usd_to_cny_rate": str(settings.billing_usd_to_cny_rate)}


def storage_path(key):
    if not re.fullmatch(r"(?:reference|result)/[a-z]+_[0-9a-f]{32}\.bin", key):
        raise ServiceError("ICAN_ASSET_INVALID", "图片存储标识无效", 404)
    root = Path(settings.ican_image_storage_dir).resolve()
    target = (root / key).resolve()
    if not target.is_relative_to(root):
        raise ServiceError("ICAN_ASSET_INVALID", "图片存储标识无效", 404)
    return target


def save_image(key, data):
    image_type(data)
    target = storage_path(key)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".image-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return key


def read_image(key):
    try:
        with storage_path(key).open("rb") as stream:
            data = stream.read(settings.ican_image_max_bytes + 1)
    except OSError:
        raise ServiceError("ICAN_ASSET_MISSING", "图片文件不可用", 404) from None
    if len(data) > settings.ican_image_max_bytes:
        raise ServiceError("ICAN_ASSET_INVALID", "图片超过大小限制", 413)
    return data


def resolve_references(db, user_id, urls):
    keys = []
    for value in urls:
        path = urlparse(value).path
        match = re.fullmatch(r"/api/v1/image-pool/media/(imgasset_[0-9a-f]{32})", path)
        if not match:
            raise ServiceError("IMAGE_REFERENCE_NOT_ALLOWED", "参考图须先通过 ICAN 渠道上传", 400)
        asset = db.scalar(select(ImagePoolMedia).where(
            ImagePoolMedia.id == match[1], ImagePoolMedia.user_id == user_id,
            ImagePoolMedia.provider == "ican"))
        if not asset:
            raise ServiceError("IMAGE_ASSET_NOT_FOUND", "找不到该渠道的参考图", 404)
        keys.append(asset.upstream_url)
    return keys


def enqueue(db, user, payload, idempotency_key):
    if not ican_image.configured():
        raise ServiceError("ICAN_NOT_CONFIGURED", "ICAN 尚未配置", 503)
    if {"aspect_ratio", "resolution"} & payload.model_fields_set:
        raise ServiceError("ICAN_SIZE_REQUIRED", "ICAN 使用 size 指定尺寸，请勿传 aspectRatio/resolution", 422)
    model = payload.model or settings.ican_image_model
    size = (payload.size or "1024x1024").lower().replace("×", "x")
    if not payload.prompt.strip():
        raise ServiceError("IMAGE_PROMPT_EMPTY", "图片描述不能为空", 422)
    request = {"provider": "ican", "model": model, "size": size, "prompt": payload.prompt.strip(),
               "references": resolve_references(db, user.id, payload.image_urls)}
    digest = json_hash(request)
    if idempotency_key is not None and not idempotency_key.strip():
        idempotency_key = None
    if idempotency_key and payload.client_task_id and idempotency_key != payload.client_task_id:
        raise ServiceError("IDEMPOTENCY_CONFLICT", "Idempotency-Key 与 clientJobId 不一致", 409)
    key = idempotency_key or payload.client_task_id or new_id("imgreq")

    def existing_response():
        existing = db.scalar(select(ImagePoolJob).where(
            ImagePoolJob.user_id == user.id, ImagePoolJob.idempotency_key == key))
        if existing:
            if existing.provider != "ican" or existing.request_hash != digest:
                raise ServiceError("IDEMPOTENCY_CONFLICT", "幂等键对应的请求内容不一致", 409)
            return response(existing)
        return None

    found = existing_response()
    if found:
        return found
    quote = price_quote(model, size)
    request["billing"] = quote  # Price snapshot is excluded from idempotency hash.
    wallet_for_update(db, user.id)  # Serialize quota and idempotency checks per user.
    found = existing_response()
    if found:
        return found
    active = db.scalar(select(func.count()).select_from(ImagePoolJob).where(
        ImagePoolJob.user_id == user.id, ImagePoolJob.status.in_(["queued", "submitting", "running"]))) or 0
    if active >= settings.image_pool_max_user_jobs:
        raise ServiceError("IMAGE_QUEUE_LIMIT", "图片队列已达上限", 429)
    job = ImagePoolJob(id=new_id("img"), user_id=user.id, provider="ican", idempotency_key=key,
                       status="queued", request_json=request, request_hash=digest,
                       reserved_credits=Decimal(quote["credits"]))
    db.add(job)
    try:
        reserve_image_pool_credits(db, user_id=user.id, job_id=job.id, credits=job.reserved_credits)
        db.commit()
    except IntegrityError:
        db.rollback()
        found = existing_response()
        if found:
            return found
        raise
    except HTTPException as exc:
        db.rollback()
        if exc.status_code == 409:
            raise ServiceError("INSUFFICIENT_CREDITS", "积分不足", 402) from None
        raise
    return response(job)


def response(job):
    return {"code": 0, "data": {"taskId": job.id, "clientJobId": job.idempotency_key,
                                "provider": "ican", "model": job.request_json["model"]},
            "reserved_credits": float(job.reserved_credits)}


def catalog():
    configured = ican_image.configured()
    remote = ican_image.models() if configured else []
    items = []
    for model, spec in prices().items():
        items.append({"id": model, "available": configured and model in remote,
                      "sizes": [{"size": size, **price_quote(model, size)} for size in spec["sizes"]]})
    return {"provider": "ican", "configured": configured, "models": items,
            "remote_models": remote, "billing_mode": "fixed_per_image", "max_images": 1,
            "reference_upload_provider": "ican", "max_references": 4}
