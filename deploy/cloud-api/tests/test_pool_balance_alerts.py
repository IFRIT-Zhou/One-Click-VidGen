from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from app.pool_balance_alerts import BalanceAlerts, DeliveryResult, account_fingerprint, valid_email


class BalanceAlertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "alerts.sqlite"
        self.now = 1000.0
        self.store = self.new_store()
        self.sent = []
        self.account = account_fingerprint("runninghub", "test-secret-key")
        self.receivers = ["reader1@example.com", "reader2@example.com"]

    def tearDown(self):
        self.temp.cleanup()

    def new_store(self):
        return BalanceAlerts(self.path, cooldown_seconds=86400, retry_seconds=60, clock=lambda: self.now)

    def send(self, recipient, message, delivery_id):
        self.sent.append((recipient, message, delivery_id))
        return DeliveryResult("accepted")

    def check(self, balance="29", **kwargs):
        return self.store.check(provider="runninghub", account_id=self.account, balance=balance,
                                currency="USD", recipients=self.receivers, send=kwargs.get("send", self.send))

    def test_strict_threshold_and_two_independent_recipients(self):
        self.assertEqual(self.check("30"), [])
        self.assertEqual(self.check("31"), [])
        result = self.check("29.999")
        self.assertEqual(len(result), 2)
        self.assertEqual({r["recipient"] for r in result}, set(self.receivers))
        self.assertIn("需要充值", self.sent[0][1])
        self.assertIn("美元", self.sent[0][1])
        self.assertNotIn("test-secret-key", self.sent[0][1])
        self.assertNotIn(b"test-secret-key", self.path.read_bytes())

    def test_restarts_cooldown_unknown_and_recovery(self):
        self.check()
        self.store = self.new_store()
        self.now += 100
        self.assertEqual(self.check(), [])
        self.assertEqual(self.check(None), [])
        self.assertEqual(self.check(), [])
        self.now += 86400
        self.assertEqual(len(self.check()), 2)
        self.check("30")
        self.assertEqual(len(self.check("29")), 2)

    def test_failed_recipient_only_retried_after_interval(self):
        def sender(recipient, message, delivery_id):
            return DeliveryResult("failed" if recipient == self.receivers[1] else "accepted")
        self.check(send=sender)
        self.now += 59
        self.assertEqual(self.check(), [])
        self.now += 1
        retried = self.check()
        self.assertEqual(len(retried), 1)
        self.assertEqual(retried[0]["recipient"], self.receivers[1])

    def test_timeout_unknown_is_not_blindly_retried_and_can_reconcile(self):
        def ambiguous(*args):
            raise TimeoutError("secret should never be stored")
        results = self.check(send=ambiguous)
        self.assertTrue(all(r["status"] == "unknown" for r in results))
        self.store = self.new_store()
        self.now += 86400 * 10
        self.assertEqual(self.check(), [])
        self.assertEqual(len(self.store.unresolved()), 2)
        for row in results:
            self.store.resolve_delivery(row["id"], "failed")
        self.now += 60
        self.assertEqual(len(self.check()), 2)
        self.assertEqual(self.store.unresolved(), [])

    def test_process_crash_pending_blocks_duplicates(self):
        claims = self.store._prepare("runninghub", self.account, "20", "USD", self.receivers)
        self.assertEqual(len(claims), 2)
        self.store = self.new_store()
        self.now += 864000
        self.assertEqual(self.check(), [])
        self.assertTrue(all(r["status"] == "pending" for r in self.store.unresolved()))

    def test_concurrent_claims_send_once_without_transaction_during_network(self):
        stores = [self.new_store(), self.new_store()]
        barrier = threading.Barrier(2)
        def sender(recipient, message, delivery_id):
            with closing(sqlite3.connect(self.path, timeout=.1)) as db:
                db.execute("BEGIN IMMEDIATE")  # No writer lock remains during send.
                db.rollback()
            return self.send(recipient, message, delivery_id)
        def run(store):
            barrier.wait()
            return store.check(provider="runninghub", account_id=self.account, balance="29", currency="USD",
                               recipients=self.receivers, send=sender)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, stores))
        self.assertEqual(sum(map(len, results)), 2)
        self.assertEqual(len(self.sent), 2)

    def test_currency_validation_and_ican_threshold(self):
        key = account_fingerprint("ican", "other-secret")
        for amount, currency in [("NaN", "CNY"), ("Infinity", "CNY"), ("29", "USD")]:
            with self.assertRaises(ValueError):
                self.store.check(provider="ican", account_id=key, balance=amount, currency=currency,
                                 recipients=self.receivers, send=self.send)
        results = self.store.check(provider="ican", account_id=key, balance=Decimal("29.90"), currency="CNY",
                                   recipients=self.receivers, send=self.send)
        self.assertEqual(len(results), 2)
        self.assertIn("元人民币", self.sent[0][1])
        self.assertNotEqual(key, self.account)

    def test_invalid_sender_reply_is_unknown(self):
        results = self.check(send=lambda *args: None)
        self.assertTrue(all(r["status"] == "unknown" for r in results))
        self.assertEqual(len(self.store.unresolved()), 2)

    def test_email_validation_prevents_headers_or_phone_recipients(self):
        self.assertTrue(valid_email("942700596@qq.com"))
        self.assertTrue(valid_email("alerts+dev@example.co.uk"))
        for value in ("18041105066", "Name <a@example.com>", "a@example.com\r\nBcc:x@example.com",
                      "a..b@example.com", ".a@example.com", "a@example..com", "a@-example.com",
                      "a@localhost", "a" * 65 + "@example.com"):
            self.assertFalse(valid_email(value))
            self.receivers = [value]
            with self.assertRaises(ValueError):
                self.check()
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
