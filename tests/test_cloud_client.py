import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app.cloud_client import (
    CloudApiError,
    CloudAuthSession,
    CloudClient,
    CloudConfig,
    CloudSessionStore,
)


def json_response(payload, status_code=200):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload
    response.text = ""
    return response


class CloudClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = CloudSessionStore()
        self.client = CloudClient(
            7,
            config=CloudConfig(base_url="https://cluster.example/api/v1"),
            session_store=self.store,
        )

    def test_login_keeps_tokens_out_of_public_snapshot(self) -> None:
        response = json_response({
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "expires_in": 900,
            "user": {"id": "usr_7", "email": "user@example.com"},
        })
        with patch("backend.app.cloud_client.requests.request", return_value=response) as request:
            snapshot = self.client.login("user@example.com", "password")

        self.assertTrue(snapshot["authenticated"])
        self.assertEqual(snapshot["user"]["id"], "usr_7")
        self.assertNotIn("access_token", snapshot)
        self.assertNotIn("refresh_token", snapshot)
        self.assertEqual(self.store.get(7).refresh_token, "refresh-secret")
        self.assertEqual(request.call_args.args[1], "https://cluster.example/api/v1/auth/login")

    def test_expired_access_token_refreshes_before_account_request(self) -> None:
        self.store.set(7, CloudAuthSession("old-access", "old-refresh", time.time() - 1, {"id": "usr_7"}))
        refresh = json_response({
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 900,
        })
        account = json_response({"credits": {"available": 100}, "quota": {}})
        with patch("backend.app.cloud_client.requests.request", side_effect=[refresh, account]) as request:
            payload = self.client.account_summary()

        self.assertEqual(payload["credits"]["available"], 100)
        first_call, second_call = request.call_args_list
        self.assertNotIn("Authorization", first_call.kwargs["headers"])
        self.assertEqual(second_call.kwargs["headers"]["Authorization"], "Bearer new-access")
        self.assertEqual(self.store.get(7).refresh_token, "new-refresh")

    def test_download_rejects_cross_origin_url_without_leaking_token(self) -> None:
        self.store.set(7, CloudAuthSession("access", "refresh", time.time() + 900, {}))
        with tempfile.TemporaryDirectory() as directory, patch("backend.app.cloud_client.requests.request") as request:
            with self.assertRaises(CloudApiError):
                self.client.download_to("https://attacker.invalid/audio.wav", Path(directory) / "audio.wav")
        request.assert_not_called()

    def test_voice_list_queries_cluster_supported_presets(self) -> None:
        self.store.set(7, CloudAuthSession("access", "refresh", time.time() + 900, {}))
        response = json_response({
            "items": [{"id": "voice_01.wav", "type": "preset", "status": "active"}],
            "capabilities": {"preset": True, "upload": False},
        })
        with patch("backend.app.cloud_client.requests.request", return_value=response) as request:
            payload = self.client.list_voices(voice_type="preset")

        self.assertEqual(payload["items"][0]["id"], "voice_01.wav")
        self.assertFalse(payload["capabilities"]["upload"])
        self.assertEqual(request.call_args.args[1], "https://cluster.example/api/v1/cloud/voices")
        self.assertEqual(request.call_args.kwargs["params"]["type"], "preset")

    def test_voice_audio_is_streamed_from_authenticated_same_origin_endpoint(self) -> None:
        self.store.set(7, CloudAuthSession("access", "refresh", time.time() + 900, {}))
        response = json_response(None)
        response.headers = {"Content-Type": "audio/wav", "Content-Length": "478050"}
        with patch("backend.app.cloud_client.requests.request", return_value=response) as request:
            streamed = self.client.stream_voice_audio("voice_01.wav")

        self.assertIs(streamed, response)
        self.assertEqual(
            request.call_args.args[1],
            "https://cluster.example/api/v1/cloud/voices/voice_01.wav/audio",
        )
        self.assertTrue(request.call_args.kwargs["stream"])
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer access")

    def test_recharge_products_and_order_use_authenticated_cloud_session(self) -> None:
        self.store.set(7, CloudAuthSession("access", "refresh", time.time() + 900, {}))
        products = json_response({
            "items": [{"product_id": "credits_100", "amount_fen": 100, "credits": 100}],
            "test_product_enabled": False,
        })
        order = json_response({
            "order_id": "ord_1",
            "status": "pending",
            "amount_fen": 100,
            "credits": 100,
            "payment": {"provider": "alipay", "payment_url": "https://openapi.alipay.com/gateway.do"},
        }, status_code=201)
        with patch("backend.app.cloud_client.requests.request", side_effect=[products, order]) as request:
            catalog = self.client.list_recharge_products()
            created = self.client.create_recharge_order(
                {"product_id": "credits_100", "payment_provider": "alipay"},
                idempotency_key="client-order-1",
            )

        self.assertEqual(catalog["items"][0]["amount_fen"], 100)
        self.assertEqual(created["payment"]["provider"], "alipay")
        product_call, order_call = request.call_args_list
        self.assertEqual(product_call.args[1], "https://cluster.example/api/v1/recharge/products")
        self.assertEqual(order_call.args[1], "https://cluster.example/api/v1/recharge/orders")
        self.assertEqual(order_call.kwargs["headers"]["Idempotency-Key"], "client-order-1")
        self.assertEqual(order_call.kwargs["headers"]["Authorization"], "Bearer access")

    def test_alipay_order_rejects_untrusted_checkout_url(self) -> None:
        self.store.set(7, CloudAuthSession("access", "refresh", time.time() + 900, {}))
        response = json_response({
            "order_id": "ord_bad",
            "status": "pending",
            "payment": {"provider": "alipay", "payment_url": "https://attacker.invalid/pay"},
        }, status_code=201)
        with patch("backend.app.cloud_client.requests.request", return_value=response):
            with self.assertRaisesRegex(CloudApiError, "收银台地址不受信任"):
                self.client.create_recharge_order(
                    {"product_id": "credits_100", "payment_provider": "alipay"},
                    idempotency_key="client-order-bad",
                )


if __name__ == "__main__":
    unittest.main()
