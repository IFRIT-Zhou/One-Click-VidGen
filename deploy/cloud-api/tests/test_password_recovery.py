import os
os.environ['DATABASE_URL']='sqlite:///:memory:'
os.environ['AUTO_CREATE_SCHEMA']='false'
import unittest
from datetime import timedelta
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app import api,password_recovery as recovery
from app.db import Base,get_db
from app.models import User,UserSession,utcnow
from app.security import hash_password,create_access_token

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions=sessionmaker(self.engine,expire_on_commit=False)
        with self.sessions() as db:
            db.add_all([User(id=i,email=f'{i}@example.com',password_hash=hash_password('old-password-123'),role=role) for i,role in [('admin','admin'),('member','user'),('other','user')]])
            db.commit()
        app=FastAPI();app.include_router(api.router);app.include_router(recovery.router)
        def database():
            with self.sessions() as db:yield db
        app.dependency_overrides[get_db]=database
        self.client=TestClient(app)
        self.patches=[patch.object(recovery,'SessionLocal',self.sessions),patch.object(recovery,'configured',return_value=True),patch.object(recovery,'send_code')]
        self.mocks=[p.start() for p in self.patches];self.mail=self.mocks[-1]
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.client.close();self.engine.dispose()
    def request(self,email='member@example.com'):
        return self.client.post('/api/v1/auth/password-reset/request',json={'email':email})
    def confirm(self,code,email='member@example.com',password='new-password-456'):
        return self.client.post('/api/v1/auth/password-reset/confirm',json={'email':email,'code':code,'password':password})
    def code(self):return self.mail.call_args.args[1]
    def login(self,password):return self.client.post('/api/v1/auth/login',json={'email':'member@example.com','password':password})
    def clear_limits(self):
        with self.sessions() as db:db.query(recovery.PasswordRecoveryLimit).delete();db.commit()
    def test_success_single_use_revokes_sessions(self):
        old=self.login('old-password-123').json()
        response=self.request();self.assertEqual(response.status_code,200);self.assertEqual(response.json()['expires_in'],1800)
        code=self.code();self.assertRegex(code,r'^\d{6}$')
        with self.sessions() as db:
            row=db.scalar(select(recovery.PasswordRecoveryCode));self.assertNotEqual(row.code_hash,code);self.assertEqual(row.attempts,0)
            self.assertGreater((recovery.aware(row.expires_at)-utcnow()).total_seconds(),1790)
        self.assertEqual(self.confirm(code).status_code,200)
        self.assertEqual(self.confirm(code).status_code,400)
        self.assertEqual(self.login('old-password-123').status_code,401)
        self.assertEqual(self.login('new-password-456').status_code,200)
        self.assertEqual(self.client.get('/api/v1/users/me',headers={'Authorization':'Bearer '+old['access_token']}).status_code,401)
        self.assertEqual(self.client.post('/api/v1/auth/refresh',json={'refresh_token':old['refresh_token']}).status_code,401)
    def test_expired_code_rejected_at_exact_boundary(self):
        self.request();code=self.code()
        with self.sessions() as db:
            row=db.scalar(select(recovery.PasswordRecoveryCode));expiry=utcnow();row.expires_at=expiry;db.commit()
        with patch.object(recovery,'utcnow',return_value=expiry):self.assertEqual(self.confirm(code).status_code,400)
        self.assertEqual(self.login('old-password-123').status_code,200)
    def test_five_wrong_codes_disable_challenge(self):
        self.request();code=self.code();wrong='000000' if code!='000000' else '111111'
        for _ in range(5):self.assertEqual(self.confirm(wrong).status_code,400)
        self.assertEqual(self.confirm(code).status_code,400)
    def test_resend_invalidates_previous_code(self):
        with patch.object(recovery.secrets,'randbelow',return_value=123456):self.request()
        self.clear_limits()
        with patch.object(recovery.secrets,'randbelow',return_value=654321):self.request()
        self.assertEqual(self.confirm('123456').status_code,400);self.assertEqual(self.confirm('654321').status_code,200)
    def test_unknown_and_disabled_have_same_response_no_email(self):
        expected=self.request().json();self.mail.reset_mock()
        self.assertEqual(self.request('missing@example.com').json(),expected);self.mail.assert_not_called()
        with self.sessions() as db:db.get(User,'other').status='disabled';db.commit()
        self.assertEqual(self.request('other@example.com').json(),expected);self.mail.assert_not_called()
    def test_rate_limits_send_and_confirm_persist(self):
        self.request();r=self.request();self.assertEqual(r.status_code,429);self.assertIn('Retry-After',r.headers);self.assertEqual(self.mail.call_count,1)
        for _ in range(10):self.confirm('000000',email='missing@example.com')
        self.assertEqual(self.confirm('000000',email='missing@example.com').status_code,429)
    def test_code_cannot_reset_another_email_and_admin_reset_invalidates_code(self):
        self.request();code=self.code();self.assertEqual(self.confirm(code,email='other@example.com').status_code,400)
        admin={'Authorization':'Bearer '+create_access_token('admin','admin')[0]}
        self.assertEqual(self.client.post('/api/v1/admin/users/member/reset-password',headers=admin).status_code,200)
        self.assertEqual(self.confirm(code).status_code,400)
    def test_password_policy_validation_does_not_consume_good_code(self):
        self.request();code=self.code();self.assertEqual(self.confirm(code,password='short').status_code,422)
        self.assertEqual(self.confirm(code).status_code,200)
    def test_send_failure_does_not_leave_usable_challenge(self):
        self.mail.side_effect=RuntimeError('private SMTP detail')
        self.assertEqual(self.request().status_code,200)
        code=self.code();self.assertEqual(self.confirm(code).status_code,400)
    def test_missing_smtp_returns_service_error(self):
        with patch.object(recovery,'configured',return_value=False):self.assertEqual(self.request().status_code,503)
        self.mail.assert_not_called()
    def test_old_delivery_failure_cannot_invalidate_new_code(self):
        self.request()
        with self.sessions() as db:old_nonce=db.scalar(select(recovery.PasswordRecoveryCode)).nonce
        self.clear_limits();self.request();code=self.code()
        self.mail.reset_mock();recovery.deliver_code('member@example.com',recovery.digest('email','member@example.com'),old_nonce,'111111')
        self.mail.assert_not_called();self.assertEqual(self.confirm(code).status_code,200)

if __name__=='__main__':unittest.main(verbosity=2)
