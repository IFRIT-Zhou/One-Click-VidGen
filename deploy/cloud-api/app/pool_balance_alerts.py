"""Durable low-balance alerts; independent of the application billing database.

Transport contract: return ``accepted`` only after the gateway confirms acceptance;
``failed`` means it definitely did NOT accept a message. Timeouts, exceptions and
ambiguous replies are ``unknown`` and are never automatically resubmitted. A
process crash leaves ``pending`` with the same protection. Gateway reconciliation
may call resolve_delivery() after checking its persisted delivery ID.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import math
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable, Literal
import uuid


PROVIDERS = {"runninghub": (Decimal("30"), "USD", "RunningHub", "美元"),
             "ican": (Decimal("30"), "USD", "ICAN", "美元")}
DeliveryStatus = Literal["accepted", "failed", "unknown"]


@dataclass(frozen=True)
class DeliveryResult:
    status: DeliveryStatus


def account_fingerprint(provider: str, api_key: str) -> str:
    if provider not in PROVIDERS or not api_key:
        raise ValueError("Provider and credential required")
    return hashlib.sha256((provider + "\0" + api_key).encode()).hexdigest()


def valid_email(value: str) -> bool:
    """Accept ordinary ASCII mailbox addresses, excluding headers/display names."""
    if not isinstance(value, str) or len(value) > 254 or value.count("@") != 1:
        return False
    local, domain = value.split("@")
    if (not local or len(local) > 64 or local.startswith(".") or local.endswith(".")
            or ".." in local or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)):
        return False
    labels = domain.split(".")
    return len(labels) >= 2 and all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in labels
    )


class BalanceAlerts:
    def __init__(self, path: str | Path, *, cooldown_seconds: float = 86400,
                 retry_seconds: float = 3600, clock: Callable[[], float] = time.time):
        for duration in (cooldown_seconds, retry_seconds):
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("Intervals must be finite and positive")
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.cooldown_seconds, self.retry_seconds, self.clock = cooldown_seconds, retry_seconds, clock
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS balance_accounts (
                    provider TEXT NOT NULL, account_id TEXT NOT NULL,
                    episode INTEGER NOT NULL DEFAULT 0, low INTEGER NOT NULL DEFAULT 0,
                    balance TEXT, checked_at REAL,
                    PRIMARY KEY(provider, account_id));
                CREATE TABLE IF NOT EXISTS balance_deliveries (
                    id TEXT PRIMARY KEY, provider TEXT NOT NULL, account_id TEXT NOT NULL,
                    episode INTEGER NOT NULL, recipient TEXT NOT NULL, message TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','accepted','failed','unknown')),
                    attempted_at REAL NOT NULL, finished_at REAL);
                CREATE INDEX IF NOT EXISTS ix_balance_delivery_account
                    ON balance_deliveries(provider,account_id,episode,recipient,attempted_at);
            ''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()

    def _prepare(self, provider, account_id, balance, currency, recipients):
        if provider not in PROVIDERS or not re.fullmatch(r"[a-f0-9]{64}", account_id):
            raise ValueError("Use a supported provider and SHA-256 account fingerprint")
        threshold, expected_currency, label, unit = PROVIDERS[provider]
        if balance is None:
            return []  # Unknown is neither zero balance nor recovery.
        if currency != expected_currency:
            raise ValueError("Balance currency does not match provider threshold")
        try:
            amount = Decimal(str(balance))
        except InvalidOperation:
            raise ValueError("Invalid balance") from None
        if not amount.is_finite():
            raise ValueError("Invalid balance")
        recipients = list(dict.fromkeys(recipients))
        if any(not valid_email(value) for value in recipients):
            raise ValueError("Invalid recipient email address")
        now = self.clock()
        claims = []
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO balance_accounts(provider,account_id) VALUES (?,?)", (provider, account_id))
            account = db.execute("SELECT * FROM balance_accounts WHERE provider=? AND account_id=?", (provider, account_id)).fetchone()
            low = amount < threshold
            episode = account["episode"] + (1 if low and not account["low"] else 0)
            db.execute("UPDATE balance_accounts SET episode=?,low=?,balance=?,checked_at=? WHERE provider=? AND account_id=?",
                       (episode, int(low), str(amount), now, provider, account_id))
            if low:
                message = f"【One-Click VidGen】{label}账号 {account_id[:8]} 额度不足，余额 {amount:f} {unit}，低于 {threshold:f} {unit}，需要充值。"
                for recipient in recipients:
                    previous = db.execute("SELECT * FROM balance_deliveries WHERE provider=? AND account_id=? AND episode=? AND recipient=? ORDER BY attempted_at DESC,rowid DESC LIMIT 1",
                                          (provider, account_id, episode, recipient)).fetchone()
                    if previous:
                        if previous["status"] in ("pending", "unknown"):
                            continue
                        interval = self.cooldown_seconds if previous["status"] == "accepted" else self.retry_seconds
                        if now - (previous["finished_at"] or previous["attempted_at"]) < interval:
                            continue
                    delivery_id = "bal_" + uuid.uuid4().hex
                    db.execute("INSERT INTO balance_deliveries VALUES (?,?,?,?,?,?, 'pending', ?,NULL)",
                               (delivery_id, provider, account_id, episode, recipient, message, now))
                    claims.append((delivery_id, recipient, message))
            db.commit()
        return claims

    def check(self, *, provider: str, account_id: str, balance, currency: str,
              recipients: list[str], send: Callable[[str, str, str], DeliveryResult]) -> list[dict]:
        """Record a trusted observation; call send(recipient, text, delivery_id).

        Provider network requests happen before this method. Transport calls happen after
        the atomic claims commit, without holding a database lock. Return values
        describe transport acceptance, not final delivery to the recipient inbox.
        """
        claims = self._prepare(provider, account_id, balance, currency, recipients)
        results = []
        for delivery_id, recipient, message in claims:
            try:
                result = send(recipient, message, delivery_id)
                status = result.status if isinstance(result, DeliveryResult) else "unknown"
                if status not in ("accepted", "failed", "unknown"):
                    status = "unknown"
            except Exception:
                status = "unknown"  # Do not persist exceptions which could contain credentials.
            self.resolve_delivery(delivery_id, status)
            results.append({"id": delivery_id, "recipient": recipient, "status": status})
        return results

    def resolve_delivery(self, delivery_id: str, status: DeliveryStatus) -> bool:
        """Reconcile pending/unknown using gateway evidence; never guess failure."""
        if status not in ("accepted", "failed", "unknown"):
            raise ValueError("Invalid status")
        with self._db() as db:
            result = db.execute("UPDATE balance_deliveries SET status=?,finished_at=? WHERE id=? AND status IN ('pending','unknown')",
                                (status, self.clock(), delivery_id))
            return result.rowcount == 1

    def unresolved(self) -> list[dict]:
        """Operational queue to reconcile against the gateway; no provider secrets."""
        with self._db() as db:
            return [dict(row) for row in db.execute("SELECT id,provider,account_id,recipient,status,attempted_at FROM balance_deliveries WHERE status IN ('pending','unknown') ORDER BY attempted_at")]
