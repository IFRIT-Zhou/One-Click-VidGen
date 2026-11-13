"""Isolated oneshot account monitor; run via the five-minute systemd timer."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from decimal import Decimal

from .config import settings
from .pool_balance_alerts import BalanceAlerts, DeliveryResult, account_fingerprint
from .pool_balance_providers import query_runninghub, query_ican, BalanceQueryError
from . import pool_balance_email


def run():
    folder = Path(os.getenv("POOL_BALANCE_STATE_DIR", "/var/lib/cloud-api/pool-balance"))
    folder.mkdir(parents=True, exist_ok=True)
    # systemd oneshot is serialized; also guard manual invocations on Linux.
    import fcntl
    with (folder / "monitor.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        enabled = os.getenv("POOL_BALANCE_EMAIL_ENABLED", "false").lower() == "true"
        recipients = [s.strip() for s in os.getenv("POOL_BALANCE_RECIPIENTS", "942700596@qq.com").split(",") if s.strip()]
        alerts = BalanceAlerts(folder / "alerts.sqlite3")
        ready = enabled and pool_balance_email.configured()
        delivery_state = "enabled" if ready else "disabled" if not enabled else "smtp_unconfigured"
        observations = []
        pending = {}
        def queue_email(recipient, message, delivery_id):
            pending.setdefault(recipient, []).append((delivery_id, message))
            # Persist an uncertain outcome before the combined SMTP submission.
            # A crash cannot trigger automatic duplicate delivery.
            return DeliveryResult("unknown")
        for provider, keys, query, expected in (
            ("runninghub", settings.runninghub_image_api_keys, query_runninghub, "USD"),
            ("ican", settings.ican_image_api_keys, query_ican, "USD"),
        ):
            for key in dict.fromkeys(keys):
                account = account_fingerprint(provider, key)
                row = {"provider": provider, "account": account[:8], "checked_at": int(time.time()), "status": "unknown"}
                try:
                    balance, currency = query(key)
                    if currency != expected:
                        row.update(status="currency_mismatch", currency=currency, balance=str(balance))
                    else:
                        amount = Decimal(str(balance))
                        if not amount.is_finite():
                            raise ValueError("Invalid balance")
                        row.update(status="low" if amount < 30 else "ok", balance=str(amount), currency=currency)
                        results = alerts.check(provider=provider, account_id=account, balance=amount, currency=currency,
                                               recipients=recipients if ready else [], send=queue_email)
                        row["notifications"] = [{"id": v["id"], "status": v["status"]} for v in results]
                except BalanceQueryError as exc:
                    row.update(status="query_failed", reason=str(exc))
                except Exception:
                    row["status"] = "query_failed"
                observations.append(row)
        outcomes = {}
        for recipient, messages in pending.items():
            body = "号池低余额汇总（同一账号的多个密钥可能重复展示，但本轮合并为一封邮件）：\n\n" + "\n".join(text for _, text in messages)
            result = pool_balance_email.send(recipient, body, messages[0][0])
            for delivery_id, _ in messages:
                alerts.resolve_delivery(delivery_id, result.status)
                outcomes[delivery_id] = result.status
        for row in observations:
            for notification in row.get("notifications", []):
                notification["status"] = outcomes.get(notification["id"], notification["status"])
        report = {"checked_at": int(time.time()), "email": delivery_state, "accounts": observations,
                  "unresolved_count": len(alerts.unresolved())}
        target = folder / "status.json"
        temporary = folder / "status.tmp"
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    run()
