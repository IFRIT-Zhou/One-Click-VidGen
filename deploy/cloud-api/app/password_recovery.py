"""Email ownership verification for self-service password recovery."""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
import hashlib
import hmac
import logging
import math
import secrets
import smtplib
import ssl

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import DateTime, ForeignKey, Integer, String, select, text
from sqlalchemy.orm import Mapped, mapped_column

from .config import settings
from .db import Base, SessionLocal
from .deps import Db
from .models import User, UserSession, UserStatus, utcnow
from .security import hash_password

router = APIRouter(prefix="/api/v1/auth/password-reset", tags=["Password recovery"])
logger = logging.getLogger(__name__)
EXPIRY_SECONDS = 1800
GENERIC_MESSAGE = "如果该邮箱已绑定可用账户，验证码将发送至邮箱，30 分钟内有效。请同时检查垃圾邮件。"
INVALID_MESSAGE = "验证码无效、已过期或已使用，请重新获取。"


class PasswordRecoveryCode(Base):
    __tablename__ = "password_recovery_codes"
    email_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    nonce: Mapped[str] = mapped_column(String(64))
    code_hash: Mapped[str] = mapped_column(String(64))
    auth_version: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasswordRecoveryLimit(Base):
    __tablename__ = "password_recovery_limits"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    count: Mapped[int] = mapped_column(Integer, default=0)


class EmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr


class ConfirmRequest(EmailRequest):
    code: str = Field(pattern=r"^[0-9]{6}$")
    password: str = Field(min_length=10, max_length=200)


def aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def digest(scope, value):
    return hmac.new(settings.jwt_secret.encode(), f"password-recovery:{scope}:{value}".encode(), hashlib.sha256).hexdigest()


def rate_limit(db, limits):
    """Persistent counters shared across API workers; no raw email/IP stored."""
    dialect = db.bind.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    now = utcnow()
    for scope, identity, seconds, maximum in sorted(limits):
        key = digest(scope, identity)
        db.execute(insert(PasswordRecoveryLimit).values(key=key, window_start=now, count=0).on_conflict_do_nothing(index_elements=["key"]))
        row = db.scalar(select(PasswordRecoveryLimit).where(PasswordRecoveryLimit.key == key).with_for_update())
        elapsed = (now - aware(row.window_start)).total_seconds()
        if elapsed >= seconds:
            row.window_start = now
            row.count = 0
        if row.count >= maximum:
            db.rollback()
            retry = max(1, math.ceil(seconds - elapsed))
            raise HTTPException(429, "操作过于频繁，请稍后再试。", headers={"Retry-After": str(retry)})
        row.count += 1
    db.commit()


def configured():
    return bool(settings.smtp_host and settings.smtp_username and settings.smtp_password and settings.smtp_sender)


def begin_recovery_transaction(db):
    # SQLite ignores FOR UPDATE. Reserve its write lock before reading the user
    # or code so concurrent confirmations cannot consume the same code twice.
    # rate_limit has already committed its counters before this transaction.
    if db.bind.dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))


def send_code(email, code):
    msg = EmailMessage()
    msg["Subject"] = "One-Click VidGen 密码重置验证码"
    msg["From"] = formataddr(("One-Click VidGen", settings.smtp_sender))
    msg["To"] = email
    msg.set_content(f"您的密码重置验证码是：{code}\n\n验证码有效期为 30 分钟，仅可使用一次。重新发送后请使用最新验证码。\n如果不是您本人操作，请忽略此邮件，不要向他人提供验证码。\n\nOne-Click VidGen\nhttps://www.oneclickvidgen.com\n")
    with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=ssl.create_default_context()) as smtp:
        smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg)


def deliver_code(email, email_key, nonce, code):
    # Background delivery keeps the response identical for registered/unknown emails.
    try:
        with SessionLocal() as db:
            row = db.get(PasswordRecoveryCode, email_key)
            if not row or row.nonce != nonce or row.used_at or aware(row.expires_at) <= utcnow():
                return
        send_code(email, code)
    except Exception:
        # SMTP errors may echo recipient/credentials: never log their raw content.
        logger.error("Password recovery email delivery failed")
        with SessionLocal() as db:
            row = db.scalar(select(PasswordRecoveryCode).where(PasswordRecoveryCode.email_key == email_key).with_for_update())
            if row and row.nonce == nonce:
                row.used_at = utcnow()
                db.commit()


@router.post("/request")
def request_code(payload: EmailRequest, request: Request, background: BackgroundTasks, db: Db):
    if not configured():
        raise HTTPException(503, "验证码邮件服务暂不可用，请稍后再试。")
    email = str(payload.email).lower()
    ip = request.client.host if request.client else "unknown"
    rate_limit(db, [("send-email-cooldown",email,60,1), ("send-email-hour",email,3600,5), ("send-ip-hour",ip,3600,20)])
    begin_recovery_transaction(db)
    # Lock user first in every password-changing path to prevent login/reset races.
    user = db.scalar(select(User).where(User.email == email).with_for_update())
    if user and user.status == UserStatus.ACTIVE.value:
        key = digest("email",email)
        row = db.scalar(select(PasswordRecoveryCode).where(PasswordRecoveryCode.email_key == key).with_for_update())
        if row is None:
            row = PasswordRecoveryCode(email_key=key,user_id=user.id)
            db.add(row)
        code = f"{secrets.randbelow(1000000):06d}"
        row.nonce = secrets.token_hex(24)
        row.code_hash = digest("code",f"{key}:{row.nonce}:{code}")
        row.auth_version = user.auth_version
        row.expires_at = utcnow() + timedelta(seconds=EXPIRY_SECONDS)
        row.attempts = 0
        row.used_at = None
        db.commit()
        background.add_task(deliver_code,email,key,row.nonce,code)
    else:
        db.rollback()
    return {"message": GENERIC_MESSAGE, "expires_in": EXPIRY_SECONDS, "retry_after": 60}


@router.post("/confirm")
def confirm_reset(payload: ConfirmRequest, request: Request, db: Db):
    email = str(payload.email).lower()
    ip = request.client.host if request.client else "unknown"
    rate_limit(db, [("verify-email",email,900,10), ("verify-ip",ip,900,30)])
    begin_recovery_transaction(db)
    user = db.scalar(select(User).where(User.email == email).with_for_update())
    key = digest("email",email)
    row = db.scalar(select(PasswordRecoveryCode).where(PasswordRecoveryCode.email_key == key).with_for_update())
    now = utcnow()
    if (not user or user.status != UserStatus.ACTIVE.value or not row or row.user_id != user.id
            or row.used_at or aware(row.expires_at) <= now or row.attempts >= 5
            or row.auth_version != user.auth_version):
        db.rollback()
        raise HTTPException(400, INVALID_MESSAGE)
    expected = digest("code",f"{key}:{row.nonce}:{payload.code}")
    if not hmac.compare_digest(row.code_hash, expected):
        row.attempts += 1
        if row.attempts >= 5:
            row.used_at = now
        db.commit()
        raise HTTPException(400, INVALID_MESSAGE)
    user.password_hash = hash_password(payload.password)
    user.auth_version += 1
    row.used_at = now
    db.query(UserSession).filter(UserSession.user_id == user.id,UserSession.revoked_at.is_(None)).update({"revoked_at": now})
    db.commit()
    return {"ok": True, "message": "密码已重置，请使用新密码重新登录。"}
