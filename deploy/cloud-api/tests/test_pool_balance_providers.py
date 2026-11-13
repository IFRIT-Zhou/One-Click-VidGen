from decimal import Decimal
import unittest
from unittest.mock import patch

import httpx
from app.pool_balance_providers import BalanceQueryError, query_ican, query_runninghub


class BalanceProviderTests(unittest.TestCase):
    def response(self, value, status=200):
        return httpx.Response(status, json=value)

    def test_runninghub_wallet_and_currency(self):
        with patch("app.pool_balance_providers.httpx.request", return_value=self.response(
                {"code": 0, "data": {"remainMoney": "29.99", "currency": "USD", "remainCoins": "99999"}})) as request:
            self.assertEqual(query_runninghub("secret"), (Decimal("29.99"), "USD"))
            self.assertEqual(request.call_args.args, ("POST", "https://www.runninghub.ai/uc/openapi/accountStatus"))
            self.assertFalse(request.call_args.kwargs["follow_redirects"])

    def test_shared_coins_are_not_a_wallet_balance(self):
        with patch("app.pool_balance_providers.httpx.request", return_value=self.response(
                {"code": 0, "data": {"remainMoney": None, "currency": None, "remainCoins": "448000", "apiType": "SHARED"}})):
            with self.assertRaisesRegex(BalanceQueryError, "wallet_balance_unavailable"):
                query_runninghub("secret")

    def test_runninghub_806_does_not_leak_message(self):
        with patch("app.pool_balance_providers.httpx.request", return_value=self.response({"code": 806, "msg": "secret"})):
            with self.assertRaisesRegex(BalanceQueryError, "^runninghub_account_not_found$"):
                query_runninghub("secret")

    def test_ican_uses_wallet_not_remaining_quota(self):
        with patch("app.pool_balance_providers.httpx.request", return_value=self.response(
                {"isValid": True, "balance": 109.8965422, "remaining": 1, "unit": "USD", "mode": "quota_limited"})):
            self.assertEqual(query_ican("secret"), (Decimal("109.8965422"), "USD"))
        with patch("app.pool_balance_providers.httpx.request", return_value=self.response({"remaining": 1, "unit": "USD"})):
            with self.assertRaisesRegex(BalanceQueryError, "wallet_balance_unavailable"):
                query_ican("secret")

    def test_invalid_values_unknown_currency_and_errors(self):
        for amount, currency in [(True, "USD"), ("NaN", "USD"), ("Infinity", "USD"), ("junk", "USD"), (1, "COIN")]:
            with patch("app.pool_balance_providers.httpx.request", return_value=self.response({"balance": amount, "unit": currency})):
                with self.assertRaises(BalanceQueryError): query_ican("secret")
        with patch("app.pool_balance_providers.httpx.request", side_effect=httpx.ConnectError("secret")):
            with self.assertRaisesRegex(BalanceQueryError, "^balance_request_failed$"):
                query_ican("secret")


if __name__ == "__main__":
    unittest.main()
