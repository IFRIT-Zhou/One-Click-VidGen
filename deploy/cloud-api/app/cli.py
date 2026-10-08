from __future__ import annotations

import argparse

from .db import Base, SessionLocal, engine
from .models import QuotaPolicy, User, UserRole, UserStatus, WalletAccount, new_id
from .security import hash_password
from .config import settings


def init_db() -> None:
    from . import password_recovery  # Register recovery tables for CLI initialization.
    Base.metadata.create_all(engine)


def create_admin(email: str, password: str) -> None:
    with SessionLocal.begin() as db:
        user = db.query(User).filter_by(email=email.lower()).one_or_none()
        if user is None:
            user = User(id=new_id("usr"), email=email.lower(), password_hash=hash_password(password), status=UserStatus.ACTIVE.value, role=UserRole.ADMIN.value)
            db.add(user); db.add(WalletAccount(user_id=user.id)); db.add(QuotaPolicy(user_id=user.id, max_concurrent_jobs=settings.default_max_concurrent_jobs, daily_characters_limit=settings.default_daily_characters, max_queue_jobs=settings.default_max_queue_jobs))
        else:
            user.role = UserRole.ADMIN.value; user.status = UserStatus.ACTIVE.value
    print(f"admin ready: {email.lower()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    admin = sub.add_parser("create-admin"); admin.add_argument("email"); admin.add_argument("password")
    args = parser.parse_args()
    if args.cmd == "init-db": init_db(); print("database initialized")
    elif args.cmd == "create-admin": create_admin(args.email, args.password)


if __name__ == "__main__":
    main()
