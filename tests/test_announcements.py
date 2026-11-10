import unittest
from unittest.mock import MagicMock, patch

import requests
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app import announcements
from backend.app.cloud_client import CloudConfig


class AnnouncementProxyTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(announcements.router)
        self.client = TestClient(app)
        self.auth = patch.object(announcements, "require_user", return_value={"id": 1}).start()
        patch.object(announcements, "load_cloud_config", return_value=CloudConfig("https://configured.example/api/v1")).start()
        self.send = patch.object(announcements.requests, "get").start()
        self.addCleanup(patch.stopall)
        self.response = MagicMock(status_code=200)
        self.send.return_value.__enter__.return_value = self.response

    def test_public_feed_uses_configured_gateway_without_forwarding_credentials(self):
        self.response.json.return_value = {"items": [{"id": "a1", "title": "Update", "body": "<script>alert(1)</script>", "pinned": True, "revision": 2}], "total": 101}
        result = self.client.get("/api/announcements?limit=20&offset=20", headers={"Authorization": "Bearer local-secret"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["items"][0]["body"], "<script>alert(1)</script>")
        self.send.assert_called_once_with("https://configured.example/api/v1/announcements", params={"limit": 20, "offset": 20}, headers={"Accept": "application/json"}, timeout=(5, 10), allow_redirects=False)

    def test_local_auth_still_required(self):
        self.auth.side_effect = HTTPException(status_code=401, detail="Login required")
        self.assertEqual(self.client.get("/api/announcements").status_code, 401)
        self.send.assert_not_called()

    def test_pagination_is_bounded(self):
        for query in ("limit=0", "limit=101", "offset=-1"):
            self.assertEqual(self.client.get(f"/api/announcements?{query}").status_code, 422)
        self.send.assert_not_called()

    def test_upstream_failure_does_not_relay_internal_details(self):
        self.response.status_code = 500
        self.response.json.return_value = {"secret": "internal-failure"}
        result = self.client.get("/api/announcements")
        self.assertEqual(result.status_code, 502)
        self.assertNotIn("internal-failure", result.text)

    def test_malformed_payload_and_timeout_are_contained(self):
        for payload in ({"items": [{"id": "a"}], "total": 1}, {"items": [], "total": -1}, []):
            self.response.json.return_value = payload
            self.assertEqual(self.client.get("/api/announcements").status_code, 502)
        self.send.side_effect = requests.Timeout("sensitive connection details")
        result = self.client.get("/api/announcements")
        self.assertEqual(result.status_code, 502)
        self.assertNotIn("sensitive", result.text)


if __name__ == "__main__":
    unittest.main()
