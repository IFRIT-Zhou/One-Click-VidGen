from __future__ import annotations

import base64
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import HTTPException, status

from ..config import settings


@dataclass(frozen=True)
class PaymentProduct:
    product_id: str
    amount_fen: int
    credits: int


@dataclass(frozen=True)
class PaymentNotification:
    event_id: str
    order_id: str
    provider_order_id: str
    amount_fen: int
    status: str
    payload: dict[str, Any]


class PaymentProvider:
    name: str

    def create_payment(
        self, order_id: str, product: PaymentProduct
    ) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError

    def parse_notification(
        self, raw_body: bytes, signature: str | None
    ) -> PaymentNotification:
        raise NotImplementedError


class MockPaymentProvider(PaymentProvider):
    name = "mock"

    def create_payment(
        self, order_id: str, product: PaymentProduct
    ) -> tuple[str, dict[str, Any]]:
        return f"mock_{order_id}", {
            "provider": "mock",
            "confirm_url": f"/api/v1/payments/mock/notify",
        }

    def parse_notification(
        self, raw_body: bytes, signature: str | None
    ) -> PaymentNotification:
        if settings.app_env == "production":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Mock payment disabled")
        if not signature or not hmac.compare_digest(
            signature, settings.mock_payment_secret
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")
        try:
            payload = json.loads(raw_body)
            return PaymentNotification(
                event_id=str(payload["event_id"]),
                order_id=str(payload["order_id"]),
                provider_order_id=str(payload["provider_order_id"]),
                amount_fen=int(payload["amount_fen"]),
                status=str(payload["status"]),
                payload=payload,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Invalid payment notification"
            ) from exc


def _pem_public_key(raw: bytes) -> bytes:
    value = b"".join(raw.strip().split())
    if raw.lstrip().startswith(b"-----BEGIN"):
        return raw
    lines = [value[index : index + 64] for index in range(0, len(value), 64)]
    return b"-----BEGIN PUBLIC KEY-----\n" + b"\n".join(lines) + b"\n-----END PUBLIC KEY-----\n"


def _signing_content(parameters: dict[str, str]) -> bytes:
    parts = [
        f"{key}={value}"
        for key, value in sorted(parameters.items())
        if key != "sign" and value not in {None, ""}
    ]
    return "&".join(parts).encode("utf-8")


class AlipayPaymentProvider(PaymentProvider):
    name = "alipay"

    def __init__(
        self,
        *,
        app_id: str,
        private_key_path: str,
        alipay_public_key_path: str,
        gateway_url: str,
        notify_url: str,
        return_url: str,
        environment: str,
        seller_id: str = "",
    ) -> None:
        self.app_id = app_id
        self.gateway_url = gateway_url
        self.notify_url = notify_url
        self.return_url = return_url
        self.environment = environment
        self.seller_id = seller_id
        try:
            private_key = serialization.load_pem_private_key(
                Path(private_key_path).read_bytes(), password=None
            )
            public_key = serialization.load_pem_public_key(
                _pem_public_key(Path(alipay_public_key_path).read_bytes())
            )
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError("Unable to load Alipay RSA keys") from exc
        if not isinstance(private_key, rsa.RSAPrivateKey) or not isinstance(
            public_key, rsa.RSAPublicKey
        ):
            raise RuntimeError("Alipay keys must be RSA keys")
        if private_key.key_size < 2048 or public_key.key_size < 2048:
            raise RuntimeError("Alipay RSA2 keys must contain at least 2048 bits")
        self.private_key = private_key
        self.alipay_public_key = public_key

    def _sign(self, parameters: dict[str, str]) -> str:
        signature = self.private_key.sign(
            _signing_content(parameters), padding.PKCS1v15(), hashes.SHA256()
        )
        return base64.b64encode(signature).decode("ascii")

    def create_payment(
        self, order_id: str, product: PaymentProduct
    ) -> tuple[str, dict[str, Any]]:
        amount = f"{Decimal(product.amount_fen) / Decimal(100):.2f}"
        biz_content = json.dumps(
            {
                "out_trade_no": order_id,
                "product_code": "FAST_INSTANT_TRADE_PAY",
                "total_amount": amount,
                "subject": f"One-Click VidGen {product.credits}积分",
                "body": "One-Click VidGen 云端 GPU 服务积分充值",
                "timeout_express": "30m",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        parameters = {
            "app_id": self.app_id,
            "method": "alipay.trade.page.pay",
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "notify_url": self.notify_url,
            "return_url": self.return_url,
            "biz_content": biz_content,
        }
        parameters["sign"] = self._sign(parameters)
        payment_url = f"{self.gateway_url}?{urlencode(parameters)}"
        return order_id, {
            "provider": self.name,
            "method": "computer_web",
            "environment": self.environment,
            "payment_url": payment_url,
        }

    def parse_notification(
        self, raw_body: bytes, signature: str | None = None
    ) -> PaymentNotification:
        try:
            parameters = dict(parse_qsl(raw_body.decode("utf-8"), keep_blank_values=True))
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Invalid Alipay notification encoding"
            ) from exc
        encoded_signature = parameters.pop("sign", "")
        parameters.pop("sign_type", None)
        if not encoded_signature:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Missing Alipay signature"
            )
        try:
            signature_bytes = base64.b64decode(
                encoded_signature.replace(" ", "+"), validate=True
            )
            self.alipay_public_key.verify(
                signature_bytes,
                _signing_content(parameters),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except (InvalidSignature, ValueError) as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Invalid Alipay signature"
            ) from exc

        if parameters.get("app_id") != self.app_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Alipay app_id mismatch"
            )
        if self.seller_id and parameters.get("seller_id") != self.seller_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Alipay seller_id mismatch"
            )
        try:
            amount_fen = int(
                (Decimal(parameters["total_amount"]) * Decimal(100)).quantize(
                    Decimal("1")
                )
            )
            order_id = parameters["out_trade_no"]
            trade_no = parameters["trade_no"]
            trade_status = parameters["trade_status"]
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Invalid Alipay notification payload"
            ) from exc

        normalized_status = {
            "TRADE_SUCCESS": "paid",
            "TRADE_FINISHED": "paid",
            "WAIT_BUYER_PAY": "pending",
            "TRADE_CLOSED": "cancelled",
        }.get(trade_status, trade_status.lower())
        event_id = parameters.get("notify_id") or f"{trade_no}:{trade_status}"
        return PaymentNotification(
            event_id=event_id,
            order_id=order_id,
            provider_order_id=order_id,
            amount_fen=amount_fen,
            status=normalized_status,
            payload={**parameters, "trade_no": trade_no},
        )


def payment_provider(name: str) -> PaymentProvider:
    if name != settings.payment_provider:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            f"Payment provider {name!r} is not configured",
        )
    if name == "mock":
        return MockPaymentProvider()
    if name == "alipay":
        return AlipayPaymentProvider(
            app_id=settings.alipay_app_id,
            private_key_path=settings.alipay_private_key_path,
            alipay_public_key_path=settings.alipay_public_key_path,
            gateway_url=settings.alipay_gateway_url,
            notify_url=settings.alipay_notify_url,
            return_url=settings.alipay_return_url,
            environment=settings.alipay_environment,
            seller_id=settings.alipay_seller_id,
        )
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        f"Payment provider {name!r} is not configured",
    )
