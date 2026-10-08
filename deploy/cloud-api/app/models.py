from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    PENDING = "pending"


class UserRole(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"


class JobStatus(str, enum.Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class VideoJobStatus(str, enum.Enum):
    QUEUED = "queued"
    STORYBOARDING = "storyboarding"
    GENERATING_ASSETS = "generating_assets"
    COMPOSING = "composing"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


class VideoStepStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    EXPIRED = "expired"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    auth_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=UserStatus.ACTIVE.value)
    role: Mapped[str] = mapped_column(String(32), default=UserRole.USER.value)
    # Acquisition source: 1 = Bilibili, 2 = QR-code campaign.
    source: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    wallet: Mapped["WalletAccount"] = relationship(back_populates="user")
    quota: Mapped["QuotaPolicy"] = relationship(back_populates="user")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QuotaPolicy(Base):
    __tablename__ = "user_quota_policies"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    max_concurrent_jobs: Mapped[int] = mapped_column(Integer)
    daily_characters_limit: Mapped[int] = mapped_column(Integer)
    max_queue_jobs: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="quota")


class DailyUsage(Base):
    __tablename__ = "daily_usage"
    __table_args__ = (UniqueConstraint("user_id", "usage_date"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    usage_date: Mapped[date] = mapped_column(Date)
    jobs_created: Mapped[int] = mapped_column(Integer, default=0)
    characters: Mapped[int] = mapped_column(Integer, default=0)


class WalletAccount(Base):
    __tablename__ = "wallet_accounts"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    available: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    reserved: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    consumed: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="wallet")


class WalletLedger(Base):
    __tablename__ = "wallet_ledger"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    entry_type: Mapped[str] = mapped_column(String(32), index=True)
    available_delta: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    reserved_delta: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    consumed_delta: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    available_after: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    reserved_after: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    consumed_after: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    reference_type: Mapped[str] = mapped_column(String(32))
    reference_id: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RechargeOrder(Base):
    __tablename__ = "recharge_orders"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        UniqueConstraint("provider", "provider_order_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    product_id: Mapped[str] = mapped_column(String(64))
    amount_fen: Mapped[int] = mapped_column(Integer)
    credits: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default=OrderStatus.PENDING.value)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    provider_order_id: Mapped[str] = mapped_column(String(160))
    payment_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    __table_args__ = (UniqueConstraint("provider", "provider_event_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    provider_event_id: Mapped[str] = mapped_column(String(160))
    order_id: Mapped[str] = mapped_column(ForeignKey("recharge_orders.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserVoice(Base):
    __tablename__ = "user_voices"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        Index("ix_user_voices_user_status_created", "user_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    audio_format: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(180))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CloudJob(Base):
    __tablename__ = "cloud_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "client_job_id"),
        Index("ix_cloud_jobs_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    parent_video_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("video_jobs.id", ondelete="CASCADE"), index=True
    )
    client_job_id: Mapped[str] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(32), index=True)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ray_job_id: Mapped[str | None] = mapped_column(String(80), index=True)
    total_characters: Mapped[int] = mapped_column(Integer)
    total_chunks: Mapped[int] = mapped_column(Integer)
    completed_chunks: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    reserved_credits: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    consumed_credits: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    released_credits: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    message: Mapped[str] = mapped_column(String(500), default="")
    error_message: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    submission_attempts: Mapped[int] = mapped_column(Integer, default=0)
    poll_attempts: Mapped[int] = mapped_column(Integer, default=0)
    poll_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderAccountState(Base):
    __tablename__ = "provider_account_states"
    __table_args__ = (UniqueConstraint("provider", "slot"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    slot: Mapped[int] = mapped_column(Integer)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    disabled_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    permanently_disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ModelPoolJob(Base):
    __tablename__ = "model_pool_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        Index("ix_model_pool_jobs_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    parent_video_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("video_jobs.id", ondelete="CASCADE"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(32), index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    account_slot: Mapped[int | None] = mapped_column(Integer)
    reserved_credits: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    consumed_credits: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    released_credits: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    upstream_usage: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImagePoolJob(Base):
    __tablename__ = "image_pool_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        Index("ix_image_pool_jobs_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), default="runninghub", server_default="runninghub", index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    parent_video_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("video_jobs.id", ondelete="CASCADE"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(32), index=True)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    request_hash: Mapped[str] = mapped_column(String(64))
    upstream_task_id: Mapped[str | None] = mapped_column(String(160), index=True)
    account_slot: Mapped[int | None] = mapped_column(Integer)
    reserved_credits: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    actual_cost_credits: Mapped[str | None] = mapped_column(String(64))
    consumed_credits: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    released_credits: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    result_url: Mapped[str | None] = mapped_column(Text)
    result_content_type: Mapped[str | None] = mapped_column(String(120))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(500))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    poll_attempts: Mapped[int] = mapped_column(Integer, default=0)
    poll_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ImagePoolMedia(Base):
    __tablename__ = "image_pool_media"

    # Provider media IDs use the ``imgasset_`` prefix plus a 32-character
    # UUID; keep enough room for the full stable identifier.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), default="runninghub", server_default="runninghub")
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    upstream_url: Mapped[str] = mapped_column(Text)
    account_slot: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VideoJob(Base):
    __tablename__ = "video_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "client_job_id"),
        Index("ix_video_jobs_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    client_job_id: Mapped[str] = mapped_column(String(180))
    request_hash: Mapped[str] = mapped_column(String(64))
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), index=True)
    current_step: Mapped[str | None] = mapped_column(String(64))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    reserved_credits: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    consumed_credits: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    released_credits: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    steps: Mapped[list["VideoJobStep"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="VideoJobStep.ordinal"
    )
    assets: Mapped[list["VideoAsset"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class VideoJobStep(Base):
    __tablename__ = "video_job_steps"
    __table_args__ = (
        UniqueConstraint("video_job_id", "step_key"),
        UniqueConstraint("video_job_id", "ordinal"),
        Index("ix_video_job_steps_job_status", "video_job_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    video_job_id: Mapped[str] = mapped_column(ForeignKey("video_jobs.id", ondelete="CASCADE"), index=True)
    step_key: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    job: Mapped[VideoJob] = relationship(back_populates="steps")


class VideoAsset(Base):
    __tablename__ = "video_assets"
    __table_args__ = (
        UniqueConstraint("video_job_id", "asset_key"),
        Index("ix_video_assets_job_kind", "video_job_id", "kind"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    video_job_id: Mapped[str] = mapped_column(ForeignKey("video_jobs.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[str | None] = mapped_column(ForeignKey("video_job_steps.id", ondelete="SET NULL"), index=True)
    asset_key: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    provider: Mapped[str | None] = mapped_column(String(64))
    upstream_id: Mapped[str | None] = mapped_column(String(180), index=True)
    uri: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    job: Mapped[VideoJob] = relationship(back_populates="assets")


class CreativeProject(Base):
    __tablename__ = "creative_projects"
    __table_args__ = (
        Index("ix_creative_projects_user_updated", "user_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    document_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    admin_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
