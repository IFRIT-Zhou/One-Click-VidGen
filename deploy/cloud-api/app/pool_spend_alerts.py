"""Exact USD upstream-spend ledger with durable, coalesced email notifications.

Sources must supply actual USD charges and a trustworthy event timestamp. This
module never estimates upstream spend from retail charges or currency conversion.
Unknown transport outcomes block all subsequent notifications until reconciled.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid

from .pool_balance_alerts import DeliveryResult, valid_email

SCALE = 10 ** 12


def _units(value):
    if isinstance(value, (float, bool)):
        raise ValueError("Use Decimal or a decimal string, never float")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Invalid USD amount") from None
    if not amount.is_finite() or amount < 0:
        raise ValueError("USD amount must be finite and nonnegative")
    digits = amount.as_tuple()
    coefficient = int("".join(map(str, digits.digits)))
    power = digits.exponent + 12
    if abs(power) > 100 or len(digits.digits) > 100:
        raise ValueError("USD amount exceeds supported precision")
    if power >= 0:
        return coefficient * (10 ** power)
    divisor = 10 ** (-power)
    if coefficient % divisor:
        raise ValueError("USD amount supports up to twelve decimal places")
    return coefficient // divisor


def _money(units):
    return Decimal(f"{units // SCALE}.{units % SCALE:012d}")


def _display(units):
    return format(_money(units), "f").rstrip("0").rstrip(".") or "0"


def _utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must explicitly include its timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


class SpendAlerts:
    def __init__(self, path, *, baseline_utc, recipient, threshold_usd=Decimal("20"),
                 retry_seconds=3600, clock=time.time):
        baseline = _utc(baseline_utc)
        threshold = _units(threshold_usd)
        if not valid_email(recipient) or threshold <= 0 or not math.isfinite(retry_seconds) or retry_seconds <= 0:
            raise ValueError("Invalid recipient, threshold or retry interval")
        self.path, self.clock, self.retry_seconds = str(path), clock, retry_seconds
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS spend_state (
                    id INTEGER PRIMARY KEY CHECK(id=1), baseline TEXT NOT NULL,
                    recipient TEXT NOT NULL, threshold_units TEXT NOT NULL,
                    total_units TEXT NOT NULL, notified_units TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS spend_events (
                    event_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
                    amount_units TEXT NOT NULL, observed_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS spend_deliveries (
                    id TEXT PRIMARY KEY, target_units TEXT NOT NULL, total_units TEXT NOT NULL,
                    message TEXT NOT NULL, status TEXT NOT NULL,
                    attempted_at REAL NOT NULL, finished_at REAL);
                CREATE TABLE IF NOT EXISTS spend_counters (
                    provider TEXT NOT NULL, account_id TEXT NOT NULL,
                    baseline_units TEXT NOT NULL, highwater_units TEXT NOT NULL,
                    baseline_at TEXT NOT NULL, observed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY(provider,account_id));
            ''')
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO spend_state VALUES(1,?,?,?,'0','0')", (baseline, recipient, str(threshold)))
            state = db.execute("SELECT * FROM spend_state WHERE id=1").fetchone()
            if (state["baseline"], state["recipient"], state["threshold_units"]) != (baseline, recipient, str(threshold)):
                raise ValueError("Existing spend ledger configuration differs; do not reset it implicitly")
            db.commit()

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

    def ingest(self, *, event_id, provider, amount_usd, observed_at):
        if provider not in ("runninghub", "ican") or not isinstance(event_id, str) or not 1 <= len(event_id) <= 200:
            raise ValueError("Invalid provider or event ID")
        units, observed = _units(amount_usd), _utc(observed_at)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT * FROM spend_state WHERE id=1").fetchone()
            existing = db.execute("SELECT * FROM spend_events WHERE event_id=?", (event_id,)).fetchone()
            if existing:
                if (existing["provider"], existing["amount_units"], existing["observed_at"]) != (provider, str(units), observed):
                    raise ValueError("Conflicting upstream spend event")
                db.commit()
                return False
            if observed < state["baseline"]:
                db.commit()
                return False
            db.execute("INSERT INTO spend_events VALUES (?,?,?,?)", (event_id, provider, str(units), observed))
            db.execute("UPDATE spend_state SET total_units=? WHERE id=1", (str(int(state["total_units"]) + units),))
            db.commit()
            return True

    def ingest_counter(self, *, account_id, provider, total_usd, observed_at):
        """Track an upstream cumulative actual-cost counter, not wallet balance.

        The first observation establishes its baseline. A decrease locks this
        counter as unknown for operator review; it never silently resets. Choose
        either events or cumulative counters per upstream account, never both.
        """
        if provider not in ("runninghub", "ican") or not isinstance(account_id, str) or not re.fullmatch(r"[a-f0-9]{64}", account_id):
            raise ValueError("Invalid provider or account fingerprint")
        units, observed = _units(total_usd), _utc(observed_at)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT * FROM spend_state WHERE id=1").fetchone()
            counter = db.execute("SELECT * FROM spend_counters WHERE provider=? AND account_id=?", (provider, account_id)).fetchone()
            result, delta = "unchanged", 0
            if observed < state["baseline"] or (counter and observed < counter["observed_at"]):
                result = "stale"
            elif counter is None:
                db.execute("INSERT INTO spend_counters VALUES (?,?,?,?,?,?, 'ok')",
                           (provider, account_id, str(units), str(units), observed, observed))
                result = "baseline"
            elif counter["status"] == "unknown":
                result = "unknown"
            elif units < int(counter["highwater_units"]):
                db.execute("UPDATE spend_counters SET status='unknown',observed_at=? WHERE provider=? AND account_id=?",
                           (observed, provider, account_id))
                result = "unknown"
            else:
                delta = units - int(counter["highwater_units"])
                if delta:
                    result = "added"
                    db.execute("INSERT INTO spend_events VALUES (?,?,?,?)",
                               ("counter_" + uuid.uuid4().hex, provider, str(delta), observed))
                    db.execute("UPDATE spend_state SET total_units=? WHERE id=1", (str(int(state["total_units"]) + delta),))
                db.execute("UPDATE spend_counters SET highwater_units=?,observed_at=? WHERE provider=? AND account_id=?",
                           (str(units), observed, provider, account_id))
            db.commit()
            return {"status": result, "delta_usd": str(_money(delta))}

    def _claim(self):
        now = self.clock()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM spend_deliveries WHERE status IN ('pending','unknown') LIMIT 1").fetchone():
                db.commit()
                return None
            last = db.execute("SELECT * FROM spend_deliveries ORDER BY rowid DESC LIMIT 1").fetchone()
            if last and last["status"] == "failed" and now - last["finished_at"] < self.retry_seconds:
                db.commit()
                return None
            state = db.execute("SELECT * FROM spend_state WHERE id=1").fetchone()
            total, notified, threshold = map(int, (state["total_units"], state["notified_units"], state["threshold_units"]))
            target = total // threshold * threshold
            if target <= notified:
                db.commit()
                return None
            message = (f"【One-Click VidGen】号池消耗提醒：自 {state['baseline']} 起，RunningHub 与 ICAN "
                       f"上游实际费用累计 {_display(total)} 美元。本次达到 {_display(target)} 美元提醒线，"
                       f"较上次提醒线新增 {_display(target-notified)} 美元；余下 {_display(total-target)} 美元计入下一轮。"
                       "请核对平台账户，按需充值。")
            delivery_id = "spend_" + uuid.uuid4().hex
            db.execute("INSERT INTO spend_deliveries VALUES (?,?,?,?, 'pending', ?,NULL)",
                       (delivery_id, str(target), str(total), message, now))
            db.commit()
            return delivery_id, state["recipient"], message

    def check(self, send):
        claim = self._claim()
        if claim is None:
            return None
        delivery_id, recipient, message = claim
        try:
            result = send(recipient, message, delivery_id)
            status = result.status if isinstance(result, DeliveryResult) else "unknown"
            if status not in ("accepted", "failed", "unknown"):
                status = "unknown"
        except Exception:
            status = "unknown"
        self.resolve_delivery(delivery_id, status)
        return {"id": delivery_id, "status": status}

    def resolve_delivery(self, delivery_id, status):
        """Use transport evidence; accepted is server acceptance, not inbox delivery."""
        if status not in ("accepted", "failed", "unknown"):
            raise ValueError("Invalid delivery status")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM spend_deliveries WHERE id=?", (delivery_id,)).fetchone()
            if row is None or row["status"] not in ("pending", "unknown"):
                db.commit()
                return False
            db.execute("UPDATE spend_deliveries SET status=?,finished_at=? WHERE id=?", (status, self.clock(), delivery_id))
            if status == "accepted":
                db.execute("UPDATE spend_state SET notified_units=? WHERE id=1", (row["target_units"],))
            db.commit()
            return True

    def snapshot(self):
        with self._db() as db:
            row = db.execute("SELECT * FROM spend_state WHERE id=1").fetchone()
            counters = [{"provider": value["provider"], "account_id": value["account_id"],
                         "baseline_usd": str(_money(int(value["baseline_units"]))),
                         "highwater_usd": str(_money(int(value["highwater_units"]))),
                         "baseline_at": value["baseline_at"], "observed_at": value["observed_at"],
                         "status": value["status"]}
                        for value in db.execute("SELECT * FROM spend_counters ORDER BY provider,account_id")]
            return {"baseline_utc": row["baseline"], "recipient": row["recipient"],
                    "total_usd": str(_money(int(row["total_units"]))),
                    "notified_usd": str(_money(int(row["notified_units"]))),
                    "threshold_usd": str(_money(int(row["threshold_units"]))), "counters": counters}

    def unresolved(self):
        with self._db() as db:
            return [dict(row) for row in db.execute("SELECT id,status,attempted_at FROM spend_deliveries WHERE status IN ('pending','unknown') ORDER BY rowid")]
