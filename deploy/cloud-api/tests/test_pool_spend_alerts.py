from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from app.pool_balance_alerts import DeliveryResult
from app.pool_spend_alerts import SpendAlerts


class SpendAlertTests(unittest.TestCase):
    baseline = "2026-11-13T10:00:00Z"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "spend.sqlite"
        self.now = 1000
        self.store = self.new_store()
        self.messages = []

    def tearDown(self):
        self.temp.cleanup()

    def new_store(self):
        return SpendAlerts(self.path, baseline_utc=self.baseline, recipient="reader@example.com", clock=lambda: self.now)

    def add(self, event, amount, **kwargs):
        return self.store.ingest(event_id=event, provider=kwargs.get("provider", "runninghub"),
                                 amount_usd=amount, observed_at=kwargs.get("observed_at", self.baseline))

    def send(self, recipient, message, delivery_id):
        self.messages.append((recipient, message, delivery_id))
        return DeliveryResult("accepted")

    def test_baseline_duplicate_and_conflict(self):
        self.assertFalse(self.add("old", "100", observed_at="2026-11-13T09:59:59.999999Z"))
        self.assertTrue(self.add("new", "10"))
        self.assertFalse(self.add("new", "10.0"))
        with self.assertRaises(ValueError):
            self.add("new", "11")
        self.assertEqual(Decimal(self.store.snapshot()["total_usd"]), Decimal("10"))

    def test_exact_decimal_threshold_and_restart(self):
        self.add("a", "19.999999999999")
        self.assertIsNone(self.store.check(self.send))
        self.add("b", "0.000000000001", provider="ican")
        self.assertEqual(self.store.check(self.send)["status"], "accepted")
        self.store = self.new_store()
        self.assertIsNone(self.store.check(self.send))
        self.assertEqual(Decimal(self.store.snapshot()["notified_usd"]), Decimal("20"))

    def test_coalesce_45_and_preserve_remainder(self):
        self.add("a", "45")
        self.store.check(self.send)
        self.assertEqual(len(self.messages), 1)
        self.assertIn("达到 40 美元", self.messages[0][1])
        self.assertIn("余下 5 美元", self.messages[0][1])
        self.add("b", "14")
        self.assertIsNone(self.store.check(self.send))
        self.add("c", "1")
        self.store.check(self.send)
        self.assertEqual(Decimal(self.store.snapshot()["notified_usd"]), Decimal("60"))

    def test_failed_retry_coalesces_new_thresholds(self):
        self.add("a", "20")
        self.store.check(lambda *args: DeliveryResult("failed"))
        self.add("b", "29")
        self.now += 3599
        self.assertIsNone(self.store.check(self.send))
        self.now += 1
        self.store.check(self.send)
        self.assertEqual(Decimal(self.store.snapshot()["notified_usd"]), Decimal("40"))
        self.assertEqual(Decimal(self.store.snapshot()["total_usd"]), Decimal("49"))

    def test_unknown_blocks_all_future_thresholds_until_reconciled(self):
        self.add("a", "20")
        def ambiguous(*args):
            raise TimeoutError()
        result = self.store.check(ambiguous)
        self.assertEqual(result["status"], "unknown")
        self.store = self.new_store()
        self.add("b", "60")
        self.now += 86400
        self.assertIsNone(self.store.check(self.send))
        self.assertEqual(Decimal(self.store.snapshot()["notified_usd"]), Decimal("0"))
        self.assertTrue(self.store.resolve_delivery(result["id"], "accepted"))
        self.store.check(self.send)
        self.assertEqual(len(self.messages), 1)
        self.assertIn("新增 60 美元", self.messages[0][1])
        self.assertEqual(Decimal(self.store.snapshot()["notified_usd"]), Decimal("80"))

    def test_crash_pending_blocks_future_notifications(self):
        self.add("a", "21")
        self.store._claim()
        self.store = self.new_store()
        self.add("b", "40")
        self.assertIsNone(self.store.check(self.send))
        self.assertEqual(len(self.store.unresolved()), 1)

    def test_configuration_and_input_validation(self):
        with self.assertRaises(ValueError):
            SpendAlerts(self.path, baseline_utc="2026-11-14T00:00:00Z", recipient="reader@example.com")
        for amount in (0.1, True, "NaN", "Infinity", "-1", "0.0000000000001"):
            with self.assertRaises(ValueError):
                self.add("invalid", amount)
        with self.assertRaises(ValueError):
            self.add("naive", "1", observed_at="2026-11-13T10:00:00")

    def test_concurrent_ingest_and_claim(self):
        stores = [self.new_store(), self.new_store()]
        barrier = threading.Barrier(2)
        def run(store):
            barrier.wait()
            added = store.ingest(event_id="same", provider="ican", amount_usd="20", observed_at=self.baseline)
            return added, store.check(self.send)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, stores))
        self.assertEqual(sum(added for added, _ in results), 1)
        self.assertEqual(sum(result is not None for _, result in results), 1)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(Decimal(self.store.snapshot()["total_usd"]), Decimal("20"))

    def counter(self, amount, timestamp="2026-11-13T10:01:00Z", store=None):
        return (store or self.store).ingest_counter(account_id="a" * 64, provider="ican", total_usd=amount, observed_at=timestamp)

    def test_counter_baseline_restart_and_duplicate(self):
        self.assertEqual(self.counter("1000", self.baseline)["status"], "baseline")
        self.assertEqual(Decimal(self.store.snapshot()["total_usd"]), 0)
        self.assertEqual(self.counter("1020")["status"], "added")
        self.store = self.new_store()
        self.assertEqual(self.counter("1020")["status"], "unchanged")
        self.assertEqual(Decimal(self.store.snapshot()["total_usd"]), 20)
        self.assertEqual(Decimal(self.store.snapshot()["counters"][0]["baseline_usd"]), 1000)
        self.assertEqual(self.counter("1001", self.baseline)["status"], "stale")

    def test_counter_decrease_latches_unknown_without_reset(self):
        self.counter("1000", self.baseline)
        self.counter("1020")
        self.assertEqual(self.counter("5", "2026-11-13T10:02:00Z")["status"], "unknown")
        self.assertEqual(self.counter("1030", "2026-11-13T10:03:00Z")["status"], "unknown")
        self.assertEqual(Decimal(self.store.snapshot()["total_usd"]), 20)
        self.assertEqual(Decimal(self.store.snapshot()["counters"][0]["highwater_usd"]), 1020)

    def test_counter_atomic_failure_rolls_back_ledger_and_highwater(self):
        self.counter("1000", self.baseline)
        with self.store._db() as db:
            db.execute("CREATE TRIGGER simulate_crash BEFORE UPDATE ON spend_state BEGIN SELECT RAISE(ABORT, 'simulated'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.counter("1020")
        self.assertEqual(Decimal(self.store.snapshot()["total_usd"]), 0)
        self.assertEqual(Decimal(self.store.snapshot()["counters"][0]["highwater_usd"]), 1000)
        with self.store._db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM spend_events").fetchone()[0], 0)
            db.execute("DROP TRIGGER simulate_crash")
        self.assertEqual(self.counter("1020")["status"], "added")
        self.assertEqual(Decimal(self.store.snapshot()["total_usd"]), 20)

    def test_concurrent_counter_delta_counted_once(self):
        self.counter("1000", self.baseline)
        stores = [self.new_store(), self.new_store()]
        barrier = threading.Barrier(2)
        def run(store):
            barrier.wait()
            return self.counter("1020", store=store)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, stores))
        self.assertEqual(sorted(row["status"] for row in results), ["added", "unchanged"])
        self.assertEqual(Decimal(self.store.snapshot()["total_usd"]), 20)


if __name__ == "__main__":
    unittest.main()
