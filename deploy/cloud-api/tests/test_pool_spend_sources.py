import os
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
from decimal import Decimal
import unittest
from unittest.mock import patch
from app.pool_spend_sources import ican_total_usd, SpendSourceError
from app.adapters.runninghub_image import RunningHubImageAdapter


class SpendSourcesTests(unittest.TestCase):
    def test_ican_uses_actual_cost_not_wallet_or_internal_cost(self):
        data={"isValid":True,"unit":"USD","balance":"500","usage":{"total":{"actual_cost":"2.123456789012","cost":"6","account_cost":"99"}}}
        with patch("app.pool_spend_sources.request_json",return_value=data):
            self.assertEqual(ican_total_usd("key"),Decimal("2.123456789012"))

    def test_ican_missing_or_wrong_currency_does_not_count(self):
        for data in ({"unit":"USD","isValid":True,"balance":"99"},
                     {"unit":"CNY","isValid":True,"usage":{"total":{"actual_cost":"20"}}}):
            with patch("app.pool_spend_sources.request_json",return_value=data),self.assertRaises(SpendSourceError):
                ican_total_usd("key")

    def test_runninghub_captures_point_units_without_inventing_usd(self):
        response={"status":"SUCCESS","imageUrl":"https://example.test/a.png",
                  "usage":{"settleMode":"POINT","chargedPoint":"1.28","consumeCoins":None,"secret":"excluded"}}
        with patch.object(RunningHubImageAdapter,"_json_request",return_value=response):
            result=RunningHubImageAdapter().query(slot=0,task_id="task")
        self.assertEqual(result["upstream_usage"],{"settleMode":"POINT","chargedPoint":"1.28"})
        self.assertNotIn("upstream_actual_cost_usd",result)
