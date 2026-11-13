"""Record provider costs since activation, and notify every additional USD 20."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path

from sqlalchemy import select
from .config import settings
from .db import SessionLocal
from .models import ImagePoolJob
from .pool_balance_alerts import account_fingerprint
from .pool_spend_alerts import SpendAlerts
from .pool_spend_sources import ican_total_usd, amount_usd, SpendSourceError
from . import pool_spend_email


def run():
    import fcntl
    folder = Path(os.getenv("POOL_SPEND_STATE_DIR", "/var/lib/cloud-api/pool-spend"))
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "monitor.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        baseline_path = folder / "baseline.json"
        if not baseline_path.exists():
            temp = folder / "baseline.tmp"
            temp.write_text(json.dumps({"started_at":datetime.now(timezone.utc).isoformat()}))
            temp.replace(baseline_path)
        baseline = json.loads(baseline_path.read_text())["started_at"]
        ledger = SpendAlerts(folder / "spend.sqlite3", baseline_utc=baseline,
                             recipient=os.getenv("POOL_SPEND_RECIPIENT", "942700596@qq.com"))
        sources = []
        for key in dict.fromkeys(settings.ican_image_api_keys):
            account = account_fingerprint("ican", key)
            try:
                total = ican_total_usd(key)
                result = ledger.ingest_counter(provider="ican", account_id=account, total_usd=total,
                                               observed_at=datetime.now(timezone.utc))
                sources.append({"provider":"ican","account":account[:8],**result})
            except Exception:
                sources.append({"provider":"ican","account":account[:8],"status":"query_failed"})
        missing = 0
        ingested = 0
        with SessionLocal() as db:
            jobs = db.scalars(select(ImagePoolJob).where(
                ImagePoolJob.provider == "runninghub", ImagePoolJob.finished_at >= datetime.fromisoformat(baseline),
                ImagePoolJob.status == "completed",
            ).order_by(ImagePoolJob.finished_at).execution_options(yield_per=200))
            for job in jobs:
                cost = (job.request_json or {}).get("upstream_actual_cost_usd")
                if cost is None:
                    missing += 1
                    continue
                try:
                    finished = job.finished_at if job.finished_at.tzinfo else job.finished_at.replace(tzinfo=timezone.utc)
                    ingested += int(ledger.ingest(event_id="runninghub:"+job.id, provider="runninghub",
                                                 amount_usd=amount_usd(cost), observed_at=finished))
                except (SpendSourceError, ValueError):
                    missing += 1
        sources.append({"provider":"runninghub","new_events":ingested,"missing_cost_jobs":missing,
                        "status":"incomplete" if missing else "awaiting_currency_verification"})
        enabled = os.getenv("POOL_SPEND_EMAIL_ENABLED", "false").lower() == "true"
        def send(recipient, text, delivery_id):
            if any(row["status"] in ("query_failed", "unknown", "incomplete", "awaiting_currency_verification") for row in sources):
                text += "\n部分费用来源尚待核对，本次金额仅包括已确认的上游实际消费，后续补齐后继续累计。"
            return pool_spend_email.send(recipient, text, delivery_id)
        notification = ledger.check(send) if enabled and pool_spend_email.configured() else None
        report = {**ledger.snapshot(), "checked_at":datetime.now(timezone.utc).isoformat(),
                  "email_enabled":enabled, "smtp_configured":pool_spend_email.configured(),
                  "sources":sources,"notification":notification,"unresolved":ledger.unresolved()}
        temp = folder / "status.tmp"
        temp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        temp.replace(folder / "status.json")
        print(json.dumps(report,ensure_ascii=False))


if __name__ == "__main__":
    run()
