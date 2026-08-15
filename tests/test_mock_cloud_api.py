import time
import unittest
import io
import wave

from fastapi.testclient import TestClient

from dev.mock_cloud_api import app, reset_mock_state


class MockCloudApiTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_mock_state()
        self.client = TestClient(app)

    def login(self, email="demo@example.com") -> dict[str, str]:
        response = self.client.post("/api/v1/auth/login", json={"email": email, "password": "demo12345"})
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def test_login_account_and_voice_contract(self) -> None:
        headers = self.login()
        account = self.client.get("/api/v1/account/summary", headers=headers)
        voices = self.client.get("/api/v1/cloud/voices", headers=headers)
        self.assertEqual(account.json()["credits"]["available"], 5000)
        self.assertEqual(voices.json()["items"][0]["type"], "preset")

    def test_tts_quote_is_point_one_credit_per_started_200_characters(self) -> None:
        headers = self.login()
        for characters, expected in ((1, 0.1), (200, 0.1), (201, 0.2), (1000, 0.5)):
            response = self.client.post(
                "/api/v1/cloud/quotes",
                headers=headers,
                json={"chunks": [{"index": 0, "text": "字" * characters}]},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["estimated_credits"], expected)

    def test_job_completes_and_audio_is_valid_wav(self) -> None:
        headers = {**self.login(), "Idempotency-Key": "unit-job-1"}
        payload = {
            "client_job_id": "local-test",
            "chunks": [{"index": 0, "text": "第一句模拟配音。"}, {"index": 1, "text": "第二句模拟配音。"}],
            "voice": {"type": "preset", "id": "mock_voice_sample_a"},
        }
        created = self.client.post("/api/v1/cloud/jobs", headers=headers, json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        job_id = created.json()["job_id"]
        time.sleep(2.7)
        completed = self.client.get(f"/api/v1/cloud/jobs/{job_id}", headers=headers)
        self.assertEqual(completed.json()["status"], "completed")
        audio = self.client.get(f"/api/v1/cloud/jobs/{job_id}/chunks/0/audio", headers=headers)
        self.assertEqual(audio.status_code, 200)
        self.assertTrue(audio.content.startswith(b"RIFF"))

    def test_insufficient_credits_and_failed_job_release_reservation(self) -> None:
        low_headers = {**self.login("low@example.com"), "Idempotency-Key": "low-job"}
        payload = {"chunks": [{"index": 0, "text": "需要积分的文案"}], "voice": {"type": "preset", "id": "mock_voice_sample_a"}}
        denied = self.client.post("/api/v1/cloud/jobs", headers=low_headers, json=payload)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["code"], "INSUFFICIENT_CREDITS")

        fail_headers = {**self.login("fail@example.com"), "Idempotency-Key": "fail-job"}
        created = self.client.post("/api/v1/cloud/jobs", headers=fail_headers, json=payload)
        job_id = created.json()["job_id"]
        time.sleep(1.9)
        failed = self.client.get(f"/api/v1/cloud/jobs/{job_id}", headers=fail_headers)
        account = self.client.get("/api/v1/account/summary", headers=fail_headers)
        self.assertEqual(failed.json()["status"], "failed")
        self.assertEqual(account.json()["credits"]["available"], 5000)

    def test_slow_job_can_be_cancelled_and_refunded(self) -> None:
        headers = {**self.login("slow@example.com"), "Idempotency-Key": "slow-job"}
        payload = {"chunks": [{"index": 0, "text": "用于取消测试的模拟文案"}], "voice": {"type": "preset", "id": "mock_voice_sample_b"}}
        created = self.client.post("/api/v1/cloud/jobs", headers=headers, json=payload)
        job_id = created.json()["job_id"]
        cancelled = self.client.post(f"/api/v1/cloud/jobs/{job_id}/cancel", headers=headers)
        account = self.client.get("/api/v1/account/summary", headers=headers)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertEqual(account.json()["credits"]["available"], 5000)

    def test_uploaded_voice_contract_and_job_audio_use_uploaded_file(self) -> None:
        headers = self.login()
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes((800).to_bytes(2, "little", signed=True) * 12000)
        uploaded = self.client.post(
            "/api/v1/cloud/voices",
            headers={**headers, "Idempotency-Key": "voice-unit-1"},
            files={"file": ("reference.wav", buffer.getvalue(), "audio/wav")},
            data={"display_name": "我的测试音色"},
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        self.assertIn("voice", uploaded.json())
        voice_id = uploaded.json()["voice"]["id"]
        payload = {"chunks": [{"index": 0, "text": "使用上传音色的任务"}], "voice": {"type": "uploaded", "id": voice_id}}
        created = self.client.post(
            "/api/v1/cloud/jobs",
            headers={**headers, "Idempotency-Key": "uploaded-job-1"},
            json=payload,
        )
        time.sleep(2.7)
        job_id = created.json()["job_id"]
        completed = self.client.get(f"/api/v1/cloud/jobs/{job_id}", headers=headers).json()
        audio = self.client.get(completed["result"]["chunks"][0]["audio_url"], headers=headers)
        self.assertEqual(audio.status_code, 200)
        self.assertTrue(audio.content.startswith(b"RIFF"))

    def test_image_pool_consumes_credit_and_returns_valid_image(self) -> None:
        headers = self.login()
        before = self.client.get("/api/v1/account/summary", headers=headers).json()["credits"]["available"]
        created = self.client.post(
            "/api/v1/image-pool/generate",
            headers=headers,
            json={"prompt": "一张用于测试号池的横版插画", "aspectRatio": "2:1", "resolution": "1k"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        task_id = created.json()["data"]["taskId"]
        queried = self.client.post("/api/v1/image-pool/query", headers=headers, json={"taskId": task_id})
        self.assertEqual(queried.status_code, 200, queried.text)
        image = self.client.get(queried.json()["data"]["imageUrl"], headers=headers)
        after = self.client.get("/api/v1/account/summary", headers=headers).json()["credits"]["available"]
        self.assertEqual(image.status_code, 200)
        self.assertTrue(image.content.startswith(b"\x89PNG"))
        self.assertEqual(after, before - 1)

    def test_model_pool_uses_openai_contract_and_consumes_credit(self) -> None:
        headers = self.login()
        before = self.client.get("/api/v1/account/summary", headers=headers).json()["credits"]["available"]
        status = self.client.post("/api/v1/model-pool/status", headers=headers, json={})
        response = self.client.post(
            "/api/v1/model-pool/v1/chat/completions",
            headers=headers,
            json={
                "model": "auto",
                "messages": [
                    {"role": "system", "content": "Return JSON."},
                    {"role": "user", "content": "Plan one scene."},
                ],
            },
        )
        after = self.client.get("/api/v1/account/summary", headers=headers).json()["credits"]["available"]
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["choices"][0]["finish_reason"], "stop")
        self.assertEqual(after, before - 1)


if __name__ == "__main__":
    unittest.main()
