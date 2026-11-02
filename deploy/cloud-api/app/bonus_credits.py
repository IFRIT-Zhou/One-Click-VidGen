"""Expiring promotional credit grants and their wallet reservations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base
from .models import WalletAccount, WalletLedger, new_id, utcnow


ZERO = Decimal("0")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class BonusCreditGrant(Base):
    __tablename__ = "bonus_credit_grants"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        Index("ix_bonus_credit_user_expiry", "user_id", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    available: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=ZERO)
    reserved: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=ZERO)
    consumed: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=ZERO)
    expired: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=ZERO)
    source_type: Mapped[str] = mapped_column(String(40), index=True)
    source_id: Mapped[str] = mapped_column(String(100))
    idempotency_key: Mapped[str] = mapped_column(String(180))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BonusCreditReservation(Base):
    __tablename__ = "bonus_credit_reservations"
    __table_args__ = (
        UniqueConstraint("grant_id", "reference_type", "reference_id"),
        Index("ix_bonus_credit_reservation_ref", "reference_type", "reference_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    grant_id: Mapped[str] = mapped_column(ForeignKey("bonus_credit_grants.id"), index=True)
    reference_type: Mapped[str] = mapped_column(String(32))
    reference_id: Mapped[str] = mapped_column(String(64))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    remaining: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    consumed: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=ZERO)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def expire_available(db: Session, wallet: WalletAccount, *, now: datetime | None = None) -> Decimal:
    """Remove only unreserved expired promotional balance from the aggregate wallet."""
    now = _aware(now or utcnow())
    grants = db.scalars(select(BonusCreditGrant).where(
        BonusCreditGrant.user_id == wallet.user_id,
        BonusCreditGrant.expires_at <= now,
        BonusCreditGrant.available > ZERO,
    ).order_by(BonusCreditGrant.expires_at, BonusCreditGrant.id).with_for_update()).all()
    expired_total = ZERO
    for grant in grants:
        amount = grant.available
        if amount <= ZERO:
            continue
        wallet.available -= amount
        grant.available = ZERO
        grant.expired += amount
        expired_total += amount
        db.add(WalletLedger(
            id=new_id("led"), user_id=wallet.user_id, entry_type="bonus_expire",
            available_delta=-amount, reserved_delta=ZERO, consumed_delta=ZERO,
            available_after=wallet.available, reserved_after=wallet.reserved,
            consumed_after=wallet.consumed, reference_type="bonus_grant",
            reference_id=grant.id, idempotency_key=f"bonus:{grant.id}:expire-available",
            note="奖励积分到期",
        ))
    return expired_total


def grant_credits(
    db: Session,
    *,
    user_id: str,
    amount: Decimal | int | str,
    source_type: str,
    source_id: str,
    idempotency_key: str,
    days: int = 30,
    now: datetime | None = None,
) -> BonusCreditGrant:
    """Create one expiring grant. The caller owns the transaction commit."""
    from .services import apply_wallet_entry, wallet_for_update

    amount = Decimal(str(amount))
    if amount <= ZERO or days <= 0 or not idempotency_key:
        raise ValueError("A positive grant, expiry, and idempotency key are required")
    now = _aware(now or utcnow())
    wallet = wallet_for_update(db, user_id)
    expire_available(db, wallet, now=now)
    existing = db.scalar(select(BonusCreditGrant).where(
        BonusCreditGrant.idempotency_key == idempotency_key,
    ).with_for_update())
    if existing:
        if (existing.user_id != user_id or existing.amount != amount
                or existing.source_type != source_type or existing.source_id != source_id):
            from fastapi import HTTPException, status
            raise HTTPException(status.HTTP_409_CONFLICT, "奖励幂等键已用于其他申请")
        return existing
    grant = BonusCreditGrant(
        id=new_id("bcr"), user_id=user_id, amount=amount,
        available=amount, reserved=ZERO, consumed=ZERO, expired=ZERO,
        source_type=source_type, source_id=source_id,
        idempotency_key=idempotency_key, expires_at=now + timedelta(days=days),
        created_at=now,
    )
    db.add(grant)
    apply_wallet_entry(
        db, wallet=wallet, entry_type="bonus_grant", available_delta=amount,
        reserved_delta=ZERO, consumed_delta=ZERO, reference_type=source_type,
        reference_id=source_id, idempotency_key=f"bonus:{idempotency_key}",
        note=f"限期奖励积分，有效期 {days} 天",
    )
    return grant


def bonus_summary(db: Session, user_id: str, *, now: datetime | None = None) -> dict:
    from .services import wallet_for_update

    now = _aware(now or utcnow())
    wallet = wallet_for_update(db, user_id)
    expire_available(db, wallet, now=now)
    grants = db.scalars(select(BonusCreditGrant).where(
        BonusCreditGrant.user_id == user_id,
        BonusCreditGrant.expires_at > now,
        BonusCreditGrant.available > ZERO,
    ).order_by(BonusCreditGrant.expires_at, BonusCreditGrant.created_at)).all()
    total = sum((grant.available for grant in grants), ZERO)
    return {
        "available": total,
        "expiring": [{"amount": grant.available, "expires_at": grant.expires_at}
                     for grant in grants],
    }


def reserve_bonus(db: Session, wallet: WalletAccount, *, amount: Decimal,
                  reference_type: str, reference_id: str, now: datetime | None = None) -> Decimal:
    now = _aware(now or utcnow())
    left = amount
    grants = db.scalars(select(BonusCreditGrant).where(
        BonusCreditGrant.user_id == wallet.user_id,
        BonusCreditGrant.expires_at > now,
        BonusCreditGrant.available > ZERO,
    ).order_by(BonusCreditGrant.expires_at, BonusCreditGrant.created_at, BonusCreditGrant.id)
      .with_for_update()).all()
    reserved = ZERO
    for grant in grants:
        portion = min(grant.available, left)
        if portion <= ZERO:
            continue
        grant.available -= portion
        grant.reserved += portion
        row = BonusCreditReservation(
            id=new_id("bcrs"), grant_id=grant.id,
            reference_type=reference_type, reference_id=reference_id,
            amount=portion, remaining=portion, consumed=ZERO,
            expires_at=grant.expires_at,
        )
        db.add(row)
        reserved += portion
        left -= portion
        if left == ZERO:
            break
    return reserved


def settle_bonus_reservation(db: Session, wallet: WalletAccount, *, amount: Decimal,
                             reference_type: str, reference_id: str, consume: bool,
                             now: datetime | None = None) -> tuple[Decimal, Decimal]:
    """Settle promotional reservations; return (promotional part, refund-to-wallet)."""
    now = _aware(now or utcnow())
    rows = db.scalars(select(BonusCreditReservation).where(
        BonusCreditReservation.reference_type == reference_type,
        BonusCreditReservation.reference_id == reference_id,
        BonusCreditReservation.remaining > ZERO,
    ).order_by(BonusCreditReservation.expires_at, BonusCreditReservation.created_at,
               BonusCreditReservation.id).with_for_update()).all()
    left = amount
    bonus_total = ZERO
    refund_total = ZERO
    for row in rows:
        portion = min(row.remaining, left)
        if portion <= ZERO:
            continue
        grant = db.scalar(select(BonusCreditGrant).where(
            BonusCreditGrant.id == row.grant_id,
        ).with_for_update())
        if grant is None:
            continue
        row.remaining -= portion
        grant.reserved -= portion
        bonus_total += portion
        if consume:
            row.consumed += portion
            grant.consumed += portion
        elif _aware(grant.expires_at) > now:
            grant.available += portion
            refund_total += portion
        else:
            grant.expired += portion
        left -= portion
        if left == ZERO:
            break
    return bonus_total, refund_total
