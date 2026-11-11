import os
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import services


class RunningHubFixedPriceTests(unittest.TestCase):
    def test_quote_is_fixed_and_old_job_price_survives_future_change(self):
        with patch.object(services, "settings", SimpleNamespace(runninghub_image_price_cny=Decimal("0.12"))):
            frozen = str(services.image_pool_reserve_credits())
            self.assertEqual(Decimal(frozen), Decimal("0.12"))
        with patch.object(services, "settings", SimpleNamespace(runninghub_image_price_cny=Decimal("0.20"))):
            self.assertEqual(services.image_pool_reserve_credits(), Decimal("0.20"))
            for cost in (None, "0", "0.08", "0.40"):
                self.assertEqual(services.image_pool_actual_credits(cost, fixed_credits=frozen), Decimal("0.12"))

    def test_legacy_jobs_keep_cost_plus_rule(self):
        config = SimpleNamespace(image_pool_markup_percent=5, billing_usd_to_cny_rate=Decimal("6.75"),
                                 image_pool_default_upstream_cost_usd=Decimal("0.015"))
        with patch.object(services, "settings", config):
            self.assertEqual(services.image_pool_actual_credits("0.10"), Decimal("0.105000"))
            self.assertEqual(services.image_pool_actual_credits(None), Decimal("0.106313"))

    def test_success_consumes_exact_price_and_failure_refunds(self):
        for actual, consumed, released in [(Decimal("0.12"), Decimal("0.12"), 0), (0, 0, Decimal("0.12"))]:
            job = SimpleNamespace(id="img_test", user_id="test", reserved_credits=Decimal("0.12"),
                                  consumed_credits=0, released_credits=0)
            with patch.object(services, "wallet_for_update"), patch.object(services, "apply_wallet_entry") as apply:
                services.settle_image_pool_job(None, job=job, actual_credits=actual)
                self.assertEqual(job.consumed_credits, consumed)
                self.assertEqual(job.released_credits, released)
                self.assertEqual(apply.call_count, 1)
                services.settle_image_pool_job(None, job=job, actual_credits=actual)
                self.assertEqual(apply.call_count, 1)

    def test_invalid_frozen_price_rejected(self):
        for value in ("NaN", "Infinity", "-1", "0"):
            with self.assertRaises(ValueError):
                services.image_pool_actual_credits("0.10", fixed_credits=value)


if __name__ == "__main__":
    unittest.main()
