import os
os.environ['DATABASE_URL']='sqlite:///:memory:'
os.environ['AUTO_CREATE_SCHEMA']='false'
import unittest
import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app import api
from app.db import Base,get_db
from app.models import User,UserSession,AdminAuditLog
from app.security import hash_password,create_access_token,decode_access_token,verify_password
from app.config import settings

class PasswordResetTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions=sessionmaker(self.engine,expire_on_commit=False)
        with self.sessions() as db:
            db.add_all([User(id=i,email=f'{i}@example.com',password_hash=hash_password('old-password-123'),role=role) for i,role in [('admin','admin'),('member','user'),('other','user')]])
            db.commit()
        app=FastAPI();app.include_router(api.router)
        def database():
            with self.sessions() as db: yield db
        app.dependency_overrides[get_db]=database
        self.client=TestClient(app)
        self.admin={'Authorization':'Bearer '+create_access_token('admin','admin')[0]}
    def tearDown(self):
        self.client.close();self.engine.dispose()
    def login(self,user='member',password='old-password-123'):
        return self.client.post('/api/v1/auth/login',json={'email':f'{user}@example.com','password':password})
    def reset(self,headers=None,user='member'):
        return self.client.post(f'/api/v1/admin/users/{user}/reset-password',headers=self.admin if headers is None else headers)
    def test_reset_login_and_revoke_all_old_tokens(self):
        before=self.login().json();other=self.login('other').json()
        # Legacy access tokens have no auth_version claim.
        claims=decode_access_token(before['access_token']);claims.pop('auth_version')
        legacy=jwt.encode(claims,settings.jwt_secret,algorithm='HS256')
        self.assertEqual(self.reset().status_code,200)
        self.assertEqual(self.login().status_code,401)
        for token in [before['access_token'],legacy]:
            self.assertEqual(self.client.get('/api/v1/users/me',headers={'Authorization':'Bearer '+token}).status_code,401)
        self.assertEqual(self.client.post('/api/v1/auth/refresh',json={'refresh_token':before['refresh_token']}).status_code,401)
        after=self.login(password='123456789');self.assertEqual(after.status_code,200)
        self.assertEqual(self.client.get('/api/v1/users/me',headers={'Authorization':'Bearer '+after.json()['access_token']}).status_code,200)
        self.assertEqual(self.client.post('/api/v1/auth/refresh',json={'refresh_token':after.json()['refresh_token']}).status_code,200)
        self.assertEqual(self.client.get('/api/v1/users/me',headers={'Authorization':'Bearer '+other['access_token']}).status_code,200)
        with self.sessions() as db:
            u=db.get(User,'member');self.assertTrue(verify_password('123456789',u.password_hash));self.assertTrue(u.password_hash.startswith('scrypt$'))
            log=db.scalar(select(AdminAuditLog));self.assertEqual(log.action,'user.password_reset');self.assertNotIn('123456789',str(log.details));self.assertEqual(log.admin_user_id,'admin')
    def test_authorization_and_missing_target(self):
        self.assertEqual(self.reset(headers={}).status_code,401)
        member={'Authorization':'Bearer '+self.login().json()['access_token']}
        self.assertEqual(self.reset(headers=member,user='other').status_code,403)
        self.assertEqual(self.reset(user='missing').status_code,404)
        self.assertEqual(self.reset(user='admin').status_code,409)
        self.assertEqual(self.login().status_code,200)
    def test_reset_does_not_enable_disabled_users(self):
        with self.sessions() as db:
            db.get(User,'member').status='disabled';db.commit()
        self.assertEqual(self.reset().status_code,200)
        self.assertEqual(self.login(password='123456789').status_code,403)
    def test_repeated_reset_revokes_previous_new_login(self):
        self.reset();token=self.login(password='123456789').json()['access_token'];self.reset()
        self.assertEqual(self.client.get('/api/v1/users/me',headers={'Authorization':'Bearer '+token}).status_code,401)
        self.assertEqual(self.login(password='123456789').status_code,200)
    def test_registration_policy_unchanged(self):
        with self.assertRaises(ValueError):hash_password('123456789')
        self.assertEqual(self.client.post('/api/v1/auth/register',json={'email':'new@example.com','password':'123456789'}).status_code,422)

if __name__=='__main__':unittest.main(verbosity=2)
