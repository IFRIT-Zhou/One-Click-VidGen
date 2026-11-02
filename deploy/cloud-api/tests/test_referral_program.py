import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["AUTO_CREATE_SCHEMA"] = "false"

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import api
from app.bonus_credits import BonusCreditGrant, grant_credits
from app.db import Base, get_db
from app.models import QuotaPolicy, User, WalletAccount
from app.referral_program import PromotionClaim, ReferralProfile
from app.security import create_access_token, hash_password
from app.services import apply_wallet_entry, wallet_for_update
from app.models import utcnow
from datetime import timedelta


class ReferralProgramTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as db:
            for user_id, email, role in [("inviter", "inviter@example.com", "user"),
                                         ("admin", "admin@example.com", "admin")]:
                db.add(User(id=user_id, email=email, password_hash=hash_password("long-test-password"), role=role))
                db.add(WalletAccount(user_id=user_id))
                db.add(QuotaPolicy(user_id=user_id, max_concurrent_jobs=2, daily_characters_limit=10000, max_queue_jobs=5))
            db.add(ReferralProfile(user_id="inviter", code="INVITE1234"))
            db.commit()
        app = FastAPI()
        app.include_router(api.router)
        from app.referral_program import router
        app.include_router(router)

        def database():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database
        self.client = TestClient(app)
        self.code = None
        self.mail_patch = patch("app.referral_program._deliver_code", side_effect=self.capture_code)
        self.mail_patch.start()

    def tearDown(self):
        self.mail_patch.stop()
        self.client.close()
        self.engine.dispose()

    def capture_code(self, email, email_key, nonce, code):
        self.code = code

    def token(self, user_id, role="user"):
        return {"Authorization": "Bearer " + create_access_token(user_id, role)[0]}

    def test_email_verification_grants_referrer_ten_expiring_credits(self):
        with patch("app.referral_program._configured_email", return_value=True):
            response = self.client.post("/api/v1/auth/register", json={
                "email": "new@example.com", "password": "long-test-password", "inviteCode": "INVITE1234",
            })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(response.json()["verification_required"])
        self.assertEqual(self.client.post("/api/v1/auth/login", json={"email": "new@example.com", "password": "long-test-password"}).status_code, 403)
        verified = self.client.post("/api/v1/auth/register/verify", json={"email": "new@example.com", "code": self.code})
        self.assertEqual(verified.status_code, 200, verified.text)
        with self.sessions() as db:
            wallet = db.get(WalletAccount, "inviter")
            self.assertEqual(float(wallet.available), 10)
            grant = db.scalar(select(BonusCreditGrant))
            self.assertEqual(float(grant.available), 10)
            self.assertTrue(grant.expires_at)

    def test_admin_approval_grants_promotion_reward_once(self):
        with self.sessions() as db:
            user = User(id="creator", email="creator@example.com", password_hash=hash_password("long-test-password"), role="user")
            db.add_all([user, WalletAccount(user_id=user.id), QuotaPolicy(user_id=user.id, max_concurrent_jobs=2, daily_characters_limit=10000, max_queue_jobs=5)])
            db.commit()
        headers = self.token("creator")
        claim = self.client.post("/api/v1/referrals/promotion-claims", headers=headers, json={
            "platform": "bilibili", "account_name": "creator", "video_url": "https://www.bilibili.com/video/BV1234567890",
        })
        self.assertEqual(claim.status_code, 201, claim.text)
        duplicate = self.client.post("/api/v1/referrals/promotion-claims", headers=headers, json={
            "platform": "bilibili", "account_name": "CREATOR", "video_url": "https://www.bilibili.com/video/BV0000000000",
        })
        self.assertEqual(duplicate.status_code, 409)
        reviewed = self.client.post(f"/api/v1/admin/referral-promotion-claims/{claim.json()['id']}/review",
            headers=self.token("admin", "admin"), json={"decision": "approve", "note": "verified"})
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        with self.sessions() as db:
            wallet = db.get(WalletAccount, "creator")
            self.assertEqual(float(wallet.available), 30)
            row = db.scalar(select(PromotionClaim))
            self.assertEqual(row.status, "approved")

    def test_expired_bonus_reservation_is_dropped_without_losing_paid_credits(self):
        with self.sessions() as db:
            wallet = wallet_for_update(db, "inviter")
            wallet.available = 50
            grant = grant_credits(db, user_id="inviter", amount=10, source_type="test",
                source_id="expiry", idempotency_key="expiry-test", days=30)
            apply_wallet_entry(db, wallet=wallet, entry_type="reserve", available_delta=-20,
                reserved_delta=20, consumed_delta=0, reference_type="test_job",
                reference_id="job1", idempotency_key="test-reserve")
            grant.expires_at = utcnow() - timedelta(seconds=1)
            apply_wallet_entry(db, wallet=wallet, entry_type="release", available_delta=20,
                reserved_delta=-20, consumed_delta=0, reference_type="test_job",
                reference_id="job1", idempotency_key="test-release")
            db.commit()
            self.assertEqual(float(wallet.available), 50)
            self.assertEqual(float(wallet.reserved), 0)
            self.assertEqual(float(grant.expired), 10)


if __name__ == "__main__":
    unittest.main()
