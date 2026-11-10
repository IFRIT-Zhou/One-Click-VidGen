"""Public announcements and administrator publication controls (plain text only)."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .deps import CurrentAdmin, Db
from .models import new_id, utcnow
from .services import audit

router = APIRouter(prefix="/api/v1", tags=["Announcements"])


class Announcement(Base):
    __tablename__ = "announcements"
    __table_args__ = (
        UniqueConstraint("created_by", "client_id", name="uq_announcements_admin_client"),
        CheckConstraint("status IN ('draft', 'published', 'archived')", name="ck_announcements_status"),
        CheckConstraint("revision >= 1", name="ck_announcements_revision"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    client_id: Mapped[str] = mapped_column(String(80))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def create_schema(engine) -> None:
    """Explicit, repeatable migration; does not create or publish any announcement."""
    Base.metadata.create_all(engine, tables=[Announcement.__table__])


class AnnouncementFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=10000)
    pinned: bool = False
    status: Literal["draft", "published", "archived"] = "draft"

    @field_validator("title", "body", mode="before")
    @classmethod
    def trim_text(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
                raise ValueError("Text contains unsupported control characters")
        return value


class AnnouncementCreate(AnnouncementFields):
    client_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$")


class AnnouncementUpdate(AnnouncementFields):
    revision: int = Field(ge=1, strict=True)


def _iso(value):
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _item(row, admin=False):
    result = {"id": row.id, "title": row.title, "body": row.body, "pinned": row.pinned,
              "revision": row.revision, "updated_at": _iso(row.updated_at),
              "published_at": _iso(row.published_at)}
    if admin:
        result.update(status=row.status, created_at=_iso(row.created_at), client_id=row.client_id)
    return result


def _list(db, limit, offset, admin=False, status=None):
    conditions = ([Announcement.status == status] if status else []) if admin else [Announcement.status == "published"]
    total = db.scalar(select(func.count()).select_from(Announcement).where(*conditions))
    rows = db.scalars(select(Announcement).where(*conditions).order_by(
        Announcement.pinned.desc(), Announcement.published_at.desc(),
        Announcement.updated_at.desc(), Announcement.id.desc(),
    ).limit(limit).offset(offset)).all()
    return {"items": [_item(row, admin) for row in rows], "total": total}


@router.get("/announcements")
def public_announcements(db: Db, response: Response, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    response.headers["Cache-Control"] = "no-store"
    return _list(db, limit, offset)


@router.get("/admin/announcements")
def admin_announcements(admin: CurrentAdmin, db: Db, response: Response,
                        limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                        status: Literal["draft", "published", "archived"] | None = None):
    response.headers["Cache-Control"] = "no-store"
    return _list(db, limit, offset, admin=True, status=status)


def _existing(db, admin_id, client_id):
    return db.scalar(select(Announcement).where(Announcement.created_by == admin_id,
                                               Announcement.client_id == client_id))


def _replay(row, fingerprint, response):
    if row.request_hash != fingerprint:
        raise HTTPException(409, "client_id already used for a different announcement")
    response.status_code = 200
    return _item(row, admin=True)


@router.post("/admin/announcements", status_code=201)
def create_announcement(payload: AnnouncementCreate, admin: CurrentAdmin, db: Db, response: Response):
    response.headers["Cache-Control"] = "no-store"
    fingerprint = hashlib.sha256(json.dumps(payload.model_dump(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    admin_id = admin.id
    row = _existing(db, admin_id, payload.client_id)
    if row:
        return _replay(row, fingerprint, response)
    now = utcnow()
    row = Announcement(id=new_id("ann"), **payload.model_dump(), revision=1, created_by=admin_id,
                       request_hash=fingerprint, created_at=now, updated_at=now,
                       published_at=now if payload.status == "published" else None)
    db.add(row)
    audit(db, admin, "announcement.create", "announcement", row.id,
          {"status": row.status, "pinned": row.pinned, "revision": 1})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # The unique constraint also covers two simultaneous retries.
        existing = _existing(db, admin_id, payload.client_id)
        if existing is None:
            raise
        return _replay(existing, fingerprint, response)
    db.refresh(row)
    return _item(row, admin=True)


@router.put("/admin/announcements/{announcement_id}")
def update_announcement(announcement_id: str, payload: AnnouncementUpdate, admin: CurrentAdmin, db: Db, response: Response):
    response.headers["Cache-Control"] = "no-store"
    row = db.get(Announcement, announcement_id)
    if row is None:
        raise HTTPException(404, "Announcement not found")
    previous_status = row.status
    now = utcnow()
    values = payload.model_dump(exclude={"revision"})
    values.update(revision=payload.revision + 1, updated_at=now)
    if payload.status == "published" and previous_status != "published":
        values["published_at"] = now
    result = db.execute(update(Announcement).where(
        Announcement.id == announcement_id, Announcement.revision == payload.revision,
    ).values(**values).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "Announcement has changed; reload before saving")
    audit(db, admin, "announcement.update", "announcement", announcement_id,
          {"previous_status": previous_status, "status": payload.status,
           "pinned": payload.pinned, "revision": payload.revision + 1})
    db.commit()
    db.refresh(row)
    return _item(row, admin=True)
