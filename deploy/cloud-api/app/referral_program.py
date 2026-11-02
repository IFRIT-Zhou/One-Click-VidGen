from __future__ import annotations

from datetime import timedelta
from datetime import timezone
from email.message import EmailMessage
from email.utils import formataddr
import hashlib
import hmac
import logging
import secrets
import smtplib
import ssl

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field, HttpUrl
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from .config import settings
from .db import Base, SessionLocal
from .bonus_credits import BonusCreditGrant, BonusCreditReservation
from .deps import CurrentAdmin, CurrentUser, Db
from .models import User, UserStatus, WalletAccount, QuotaPolicy, new_id, utcnow
from .password_recovery import rate_limit
from .schemas import RegisterRequest
from .services import audit


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["Referral program"])
CODE_TTL_MINUTES = 30


class ReferralProfile(Base):
    __tablename__ = "referral_profiles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReferralAttribution(Base):
    __tablename__ = "referral_attributions"
    __table_args__ = (UniqueConstraint("referred_user_id"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    inviter_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    referred_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    code: Mapped[str] = mapped_column(String(24))
    verified_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    rewarded_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)


class RegistrationEmailCode(Base):
    __tablename__ = "registration_email_codes"
    email_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    nonce: Mapped[str] = mapped_column(String(64))
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))


class PromotionClaim(Base):
    __tablename__ = "referral_promotion_claims"
    __table_args__ = (UniqueConstraint("platform", "account_key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    platform: Mapped[str] = mapped_column(String(24))
    account_name: Mapped[str] = mapped_column(String(120))
    account_key: Mapped[str] = mapped_column(String(120))
    video_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    review_note: Mapped[str | None] = mapped_column(String(500))
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))


def _digest(scope: str, value: str) -> str:
    return hmac.new(settings.jwt_secret.encode(), f"registration:{scope}:{value}".encode(), hashlib.sha256).hexdigest()


def _aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _configured_email() -> bool:
    return bool(settings.smtp_host and settings.smtp_username and settings.smtp_password and settings.smtp_sender)


def _send_registration_code(email: str, code: str) -> None:
    message = EmailMessage()
    message["Subject"] = "One-Click VidGen 邮箱验证码"
    message["From"] = formataddr(("One-Click VidGen", settings.smtp_sender))
    message["To"] = email
    message.set_content(f"您的注册验证码是：{code}\n\n验证码 30 分钟内有效，只能使用一次。请勿向他人提供。\n\nOne-Click VidGen\nhttps://www.oneclickvidgen.com\n")
    with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=ssl.create_default_context()) as smtp:
        smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


def _deliver_code(email: str, email_key: str, nonce: str, code: str) -> None:
    try:
        with SessionLocal() as db:
            row = db.get(RegistrationEmailCode, email_key)
            if not row or row.nonce != nonce or row.used_at or _aware(row.expires_at) <= utcnow():
                return
        _send_registration_code(email, code)
    except Exception:
        logger.error("Registration verification email delivery failed")
        with SessionLocal() as db:
            row = db.get(RegistrationEmailCode, email_key)
            if row and row.nonce == nonce:
                row.used_at = utcnow()
                db.commit()


def _new_invite_code(db) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(10))
        if not db.scalar(select(ReferralProfile.user_id).where(ReferralProfile.code == code)):
            return code


def create_registration(payload: RegisterRequest, request: Request, background: BackgroundTasks, db: Db) -> dict:
    if not settings.allow_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is disabled")
    if not _configured_email():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "邮箱验证服务暂不可用")
    email = str(payload.email).lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    ip = request.client.host if request.client else "unknown"
    rate_limit(db, [("registration-email-hour", email, 3600, 5), ("registration-ip-hour", ip, 3600, 20)])
    inviter = None
    invite_code = (payload.invite_code or "").strip().upper()
    if invite_code:
        inviter = db.scalar(select(ReferralProfile).where(ReferralProfile.code == invite_code))
        if not inviter:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "邀请码无效")
    from .security import hash_password
    try:
        password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    user = User(id=new_id("usr"), email=email, password_hash=password_hash,
                status=UserStatus.PENDING.value, role="user", source=payload.source)
    db.add(user)
    db.add(WalletAccount(user_id=user.id))
    db.add(QuotaPolicy(user_id=user.id, max_concurrent_jobs=settings.default_max_concurrent_jobs,
                       daily_characters_limit=settings.default_daily_characters,
                       max_queue_jobs=settings.default_max_queue_jobs))
    db.add(ReferralProfile(user_id=user.id, code=_new_invite_code(db)))
    if inviter:
        db.add(ReferralAttribution(id=new_id("ref"), inviter_id=inviter.user_id,
                                   referred_user_id=user.id, code=invite_code))
    email_key = _digest("email", email)
    nonce = secrets.token_hex(24)
    code = f"{secrets.randbelow(1000000):06d}"
    row = RegistrationEmailCode(email_key=email_key, user_id=user.id, nonce=nonce,
        code_hash=_digest("code", f"{email_key}:{nonce}:{code}"),
        expires_at=utcnow() + timedelta(minutes=CODE_TTL_MINUTES), attempts=0)
    db.add(row)
    db.commit()
    background.add_task(_deliver_code, email, email_key, nonce, code)
    return {"verification_required": True, "email": email, "expires_in": CODE_TTL_MINUTES * 60,
            "message": "验证码已发送，请完成邮箱验证后登录。"}


class VerifyRegistration(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^[0-9]{6}$")


class ResendRegistrationCode(BaseModel):
    email: EmailStr


@router.post("/auth/register/resend")
def resend_registration_code(payload: ResendRegistrationCode, request: Request,
                             background: BackgroundTasks, db: Db):
    if not _configured_email():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "邮箱验证服务暂不可用")
    email = str(payload.email).lower()
    ip = request.client.host if request.client else "unknown"
    rate_limit(db, [("registration-resend-email-hour", email, 3600, 5), ("registration-resend-ip-hour", ip, 3600, 20)])
    if db.bind.dialect.name == "sqlite":
        from sqlalchemy import text
        db.execute(text("BEGIN IMMEDIATE"))
    user = db.scalar(select(User).where(User.email == email).with_for_update())
    if not user or user.status != UserStatus.PENDING.value:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有待验证的注册账户")
    email_key = _digest("email", email)
    row = db.scalar(select(RegistrationEmailCode).where(RegistrationEmailCode.email_key == email_key).with_for_update())
    if not row or row.user_id != user.id:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码记录不存在，请重新注册")
    nonce = secrets.token_hex(24)
    code = f"{secrets.randbelow(1000000):06d}"
    row.nonce = nonce
    row.code_hash = _digest("code", f"{email_key}:{nonce}:{code}")
    row.expires_at = utcnow() + timedelta(minutes=CODE_TTL_MINUTES)
    row.attempts = 0
    row.used_at = None
    db.commit()
    background.add_task(_deliver_code, email, email_key, nonce, code)
    return {"message": "验证码已重新发送", "expires_in": CODE_TTL_MINUTES * 60}


@router.post("/auth/register/verify")
def verify_registration(payload: VerifyRegistration, request: Request, db: Db):
    from sqlalchemy import text
    email = str(payload.email).lower()
    ip = request.client.host if request.client else "unknown"
    rate_limit(db, [("registration-verify-email", email, 900, 10), ("registration-verify-ip", ip, 900, 30)])
    if db.bind.dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))
    user = db.scalar(select(User).where(User.email == email).with_for_update())
    email_key = _digest("email", email)
    row = db.scalar(select(RegistrationEmailCode).where(RegistrationEmailCode.email_key == email_key).with_for_update())
    if not user or not row or row.user_id != user.id or row.used_at or _aware(row.expires_at) <= utcnow() or row.attempts >= 5:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码无效、已过期或已使用")
    expected = _digest("code", f"{email_key}:{row.nonce}:{payload.code}")
    if not hmac.compare_digest(row.code_hash, expected):
        row.attempts += 1
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "验证码无效、已过期或已使用")
    if user.status != UserStatus.PENDING.value:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "账户已完成验证")
    user.status = UserStatus.ACTIVE.value
    row.used_at = utcnow()
    attribution = db.scalar(select(ReferralAttribution).where(ReferralAttribution.referred_user_id == user.id).with_for_update())
    if attribution:
        from .bonus_credits import grant_credits
        grant_credits(db, user_id=attribution.inviter_id, amount=10,
            source_type="referral_signup", source_id=attribution.id,
            idempotency_key=f"referral:{attribution.id}:verified", days=30)
        attribution.verified_at = utcnow()
        attribution.rewarded_at = utcnow()
    db.commit()
    return {"verified": True, "message": "邮箱验证完成，可以登录了。"}


class PromotionClaimRequest(BaseModel):
    platform: str = Field(pattern=r"^(bilibili|douyin|xiaohongshu|kuaishou|other)$")
    account_name: str = Field(min_length=1, max_length=120)
    video_url: HttpUrl


class ReviewClaimRequest(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject)$")
    note: str | None = Field(default=None, max_length=500)


@router.get("/referrals/me")
def referral_me(user: CurrentUser, db: Db):
    profile = db.scalar(select(ReferralProfile).where(ReferralProfile.user_id == user.id))
    if profile is None:
        profile = ReferralProfile(user_id=user.id, code=_new_invite_code(db))
        db.add(profile)
        db.commit()
    referrals = db.scalars(select(ReferralAttribution).where(ReferralAttribution.inviter_id == user.id).order_by(ReferralAttribution.created_at.desc())).all()
    claims = db.scalars(select(PromotionClaim).where(PromotionClaim.user_id == user.id).order_by(PromotionClaim.submitted_at.desc())).all()
    from .bonus_credits import bonus_summary
    bonus = bonus_summary(db, user.id)
    db.commit()
    return {"code": profile.code, "verified_referrals": sum(1 for row in referrals if row.verified_at),
        "referrals": [{"created_at": row.created_at, "verified_at": row.verified_at} for row in referrals],
        "promotion_claims": [{"id": row.id, "platform": row.platform, "account_name": row.account_name,
            "video_url": row.video_url, "status": row.status, "review_note": row.review_note,
            "submitted_at": row.submitted_at, "reviewed_at": row.reviewed_at} for row in claims],
        "bonus_credits": bonus}


@router.post("/referrals/promotion-claims", status_code=status.HTTP_201_CREATED)
def create_promotion_claim(payload: PromotionClaimRequest, user: CurrentUser, db: Db):
    account = payload.account_name.strip()
    claim = PromotionClaim(id=new_id("prm"), user_id=user.id, platform=payload.platform,
        account_name=account, account_key=account.casefold(), video_url=str(payload.video_url), status="pending")
    db.add(claim)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "该平台账号已提交过申请") from exc
    return {"id": claim.id, "status": claim.status}


@router.get("/admin/referral-promotion-claims")
def admin_promotion_claims(admin: CurrentAdmin, db: Db, status_filter: str = "pending"):
    query = select(PromotionClaim, User.email).join(User, User.id == PromotionClaim.user_id)
    if status_filter in {"pending", "approved", "rejected"}:
        query = query.where(PromotionClaim.status == status_filter)
    rows = db.execute(query.order_by(PromotionClaim.submitted_at.asc()).limit(300)).all()
    return {"items": [{"id": claim.id, "email": email, "platform": claim.platform,
        "account_name": claim.account_name, "video_url": claim.video_url, "status": claim.status,
        "review_note": claim.review_note, "submitted_at": claim.submitted_at} for claim, email in rows]}


@router.post("/admin/referral-promotion-claims/{claim_id}/review")
def review_promotion_claim(claim_id: str, payload: ReviewClaimRequest, admin: CurrentAdmin, db: Db):
    if db.bind.dialect.name == "sqlite":
        from sqlalchemy import text
        db.execute(text("BEGIN IMMEDIATE"))
    claim = db.scalar(select(PromotionClaim).where(PromotionClaim.id == claim_id).with_for_update())
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "申请不存在")
    if claim.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "申请已审核")
    claim.status = "approved" if payload.decision == "approve" else "rejected"
    claim.review_note = (payload.note or "").strip() or None
    claim.reviewed_by = admin.id
    claim.reviewed_at = utcnow()
    if payload.decision == "approve":
        from .bonus_credits import grant_credits
        grant_credits(db, user_id=claim.user_id, amount=30, source_type="referral_promotion",
            source_id=claim.id, idempotency_key=f"promotion:{claim.id}:approved", days=30)
    audit(db, admin, "referral.promotion.review", "referral_promotion_claim", claim.id,
          {"decision": payload.decision, "note": claim.review_note})
    db.commit()
    return {"id": claim.id, "status": claim.status}
