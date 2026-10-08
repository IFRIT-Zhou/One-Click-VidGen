"""Isolated file-SQLite concurrency regression checks; no SMTP or production DB."""
import os
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['AUTO_CREATE_SCHEMA'] = 'false'
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException, Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app import password_recovery as recovery
from app.db import Base
from app.models import User, utcnow


class RecoveryConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine('sqlite:///' + str(Path(self.temp.name) / 'isolated.db'),
                                    connect_args={'check_same_thread': False, 'timeout': 10})
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.email = 'member@example.com'
        self.key = recovery.digest('email', self.email)
        with self.sessions() as db:
            db.add(User(id='member', email=self.email, password_hash='old', role='user',
                        status='active', auth_version=0))
            db.flush()
            db.add(recovery.PasswordRecoveryCode(email_key=self.key, user_id='member',
                nonce='test-nonce', code_hash=recovery.digest('code', f'{self.key}:test-nonce:123456'),
                auth_version=0, expires_at=utcnow() + timedelta(minutes=30), attempts=0))
            db.commit()

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def parallel(self, fn, count=2):
        barrier = threading.Barrier(count)
        def invoke(index):
            barrier.wait(timeout=10)
            with self.sessions() as db:
                try:
                    fn(db, index)
                    return 200
                except HTTPException as exc:
                    return exc.status_code
        with ThreadPoolExecutor(max_workers=count) as executor:
            return list(executor.map(invoke, range(count)))

    def confirm(self, db, index, code):
        request = Request({'type': 'http', 'client': (f'192.0.2.{index + 1}', 1234)})
        return recovery.confirm_reset(recovery.ConfirmRequest(email=self.email, code=code,
                                        password=f'new-password-{index}'), request, db)

    def test_only_one_concurrent_confirmation_consumes_code(self):
        def slow_hash(value):
            time.sleep(0.3)  # Widen race after code read but before password update.
            return 'hashed-' + value
        with patch.object(recovery, 'hash_password', side_effect=slow_hash):
            results = self.parallel(lambda db, index: self.confirm(db, index, '123456'))
        self.assertEqual(sorted(results), [200, 400])
        with self.sessions() as db:
            self.assertEqual(db.get(User, 'member').auth_version, 1)
            self.assertIsNotNone(db.get(recovery.PasswordRecoveryCode, self.key).used_at)

    def test_concurrent_incorrect_attempts_are_not_lost(self):
        original = recovery.hmac.compare_digest
        def slow_compare(a, b):
            time.sleep(0.2)
            return original(a, b)
        with patch.object(recovery.hmac, 'compare_digest', side_effect=slow_compare):
            results = self.parallel(lambda db, index: self.confirm(db, index, '654321'))
        self.assertEqual(results, [400, 400])
        with self.sessions() as db:
            self.assertEqual(db.get(recovery.PasswordRecoveryCode, self.key).attempts, 2)

    def test_concurrent_rate_limit_counters_are_shared(self):
        results = self.parallel(lambda db, index: recovery.rate_limit(db,
                                [('isolated-shared-limit', 'same-client', 3600, 3)]), 8)
        self.assertEqual(results.count(200), 3)
        self.assertEqual(results.count(429), 5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
