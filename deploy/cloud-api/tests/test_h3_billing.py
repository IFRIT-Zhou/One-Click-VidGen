import io
import os
import tempfile
import unittest
import json
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch, Mock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from PIL import Image
from app import cloud_video_api as api, h3_billing as billing
from app.cloud_video_worker import H3Queue
from app.db import Base, get_db
from app.models import User, WalletAccount, WalletLedger
from app.bonus_credits import grant_credits
from app.security import create_access_token


class BillingTests(unittest.TestCase):
    def test_public_pricing_is_anonymous_and_uncached(self):
        response = self.client.get('/api/v1/cloud/video/pricing')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual([e['credits'] for e in response.json()['examples']], ['0.70', '0.88', '1.06'])

    def test_persisted_batch_rate_does_not_change_while_queued(self):
        rates = billing.pricing()
        with patch.object(billing, 'pricing', side_effect=RuntimeError('current rates unavailable')):
            self.assertEqual(billing.price(12, rates=rates)['credits'], '0.88')

    def test_live_rate_reload_and_frozen_quotes(self):
        config=Path(self.temp.name)/'prices.json'
        config.write_text(json.dumps({'first_10_seconds_rate':'0.07','additional_seconds_rate':'0.09'}))
        with patch.dict(os.environ,H3_PRICING_FILE=str(config)):
            before=self.quote(12)
            config.write_text(json.dumps({'first_10_seconds_rate':'0.08','additional_seconds_rate':'0.12'}))
            after=self.quote(12)
            caps=self.client.get('/api/v1/cloud/video/capabilities',headers=self.headers).json()
            self.assertEqual(before['credits'],'0.88')
            self.assertEqual(after['credits'],'1.04')
            self.assertEqual(caps['pricing']['first_10_seconds_rate'],'0.08')
            self.assertNotEqual(before['pricing']['version'],after['pricing']['version'])
            self.assertEqual(self.submit(before).json()['credits'],'0.88')

    def test_invalid_config_rejects_quotes_but_keeps_frozen_jobs(self):
        q=self.quote(12)
        config=Path(self.temp.name)/'bad.json';config.write_text('{invalid')
        with patch.dict(os.environ,H3_PRICING_FILE=str(config)):
            response=self.client.get('/api/v1/cloud/video/capabilities',headers=self.headers)
            self.assertEqual(response.status_code,503)
            response=self.submit(q);self.assertEqual(response.status_code,201,response.text)
            self.assertEqual(response.json()['credits'],'0.88')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, CLOUD_VIDEO_ENABLED='1', CLOUD_VIDEO_TEST_USER_IDS='', CLOUD_VIDEO_STORAGE_DIR=self.temp.name)
        self.env.start()
        self.engine = create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as db:
            db.add(User(id='member',email='member@example.com',password_hash='unused',status='active',role='user'))
            db.add(WalletAccount(user_id='member',available=10))
            db.commit()
        app = FastAPI(); app.include_router(api.router)
        def database():
            with self.sessions() as db: yield db
        app.dependency_overrides[get_db] = database
        self.client = TestClient(app)
        self.headers = {'Authorization':'Bearer '+create_access_token('member','user')[0]}

    def tearDown(self):
        self.client.close(); self.engine.dispose(); self.env.stop(); self.temp.cleanup()

    def quote(self,seconds=12):
        image=io.BytesIO();Image.new('RGB',(64,64)).save(image,format='PNG')
        upload=self.client.post('/api/v1/cloud/video/assets',headers=self.headers,data={'kind':'image'},files={'file':('a.png',image.getvalue(),'image/png')})
        self.assertEqual(upload.status_code,201,upload.text)
        q=self.client.post('/api/v1/cloud/video/quotes',headers=self.headers,json={'spec':{'prompt':'tree','reference_image_asset_id':upload.json()['asset_id'],'width':640,'height':384,'duration_seconds':seconds}})
        self.assertEqual(q.status_code,201,q.text)
        return q.json()

    def submit(self,q):
        return self.client.post('/api/v1/cloud/video/jobs',headers=self.headers,json={'quote_id':q['quote_id'],'client_job_id':'stable'})

    def balance(self):
        with self.sessions() as db:
            w=db.get(WalletAccount,'member');return tuple(Decimal(x) for x in (w.available,w.reserved,w.consumed))

    def test_tiers_and_fraction(self):
        for seconds,expected in [(2,'0.14'),(9,'0.63'),(10,'0.70'),(10.01,'0.71'),(12,'0.88'),(14,'1.06')]:
            self.assertEqual(billing.price(seconds)['credits'],expected)

    def test_quote_no_charge_and_duplicate_submit(self):
        q=self.quote(10);self.assertEqual(q['credits'],'0.70')
        self.assertGreater(q['spec']['actual_duration_seconds'],10)
        self.assertEqual(self.balance(),(10,0,0))
        a=self.submit(q);b=self.submit(q)
        self.assertEqual(a.status_code,201,a.text)
        self.assertEqual(a.json()['job_id'],b.json()['job_id'])
        self.assertEqual(self.balance(),(Decimal('9.30'),Decimal('.70'),0))

    def test_insufficient_balance_creates_no_job(self):
        q=self.quote()
        with self.sessions() as db: db.get(WalletAccount,'member').available=0;db.commit()
        self.assertEqual(self.submit(q).status_code,409)
        with self.sessions() as db:self.assertEqual(db.scalar(select(func.count()).select_from(api.CloudVideoJob)),0)

    def test_queued_cancel_refunds_once(self):
        job=self.submit(self.quote()).json()
        for _ in range(2):
            result=self.client.post('/api/v1/cloud/video/jobs/'+job['job_id']+'/cancel',headers=self.headers)
            self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.balance(),(10,0,0))

    def test_worker_success_and_failure_settle(self):
        for state in ['succeeded','failed','cancelled']:
            with self.subTest(state=state):
                # Each iteration gets a distinct idempotency identity.
                q=self.quote()
                response=self.client.post('/api/v1/cloud/video/jobs',headers=self.headers,json={'quote_id':q['quote_id'],'client_job_id':state})
                job_id=response.json()['job_id']
                with self.sessions() as db:
                    job=db.get(api.CloudVideoJob,job_id);job.status='running';job.attempt_token='attempt';job.lease_owner='owner';db.commit()
                worker=H3Queue(Mock(call=Mock(return_value={'busy':False})),self.sessions);worker.owner='owner'
                worker._finish({'id':job_id,'token':'attempt'},state)
                with self.sessions() as db:
                    job=db.get(api.CloudVideoJob,job_id);billing.settle(db,job);db.commit()
        self.assertEqual(self.balance(),(Decimal('9.12'),0,Decimal('.88')))

    def test_legacy_quote_stays_free(self):
        q=self.quote()
        with self.sessions() as db:
            record=db.get(api.CloudVideoQuote,q['quote_id']);record.spec={k:v for k,v in record.spec.items() if k!='_billing'};db.commit()
        response=self.submit(q);self.assertEqual(response.status_code,201,response.text)
        self.assertTrue(response.json()['test_mode']);self.assertEqual(self.balance(),(10,0,0))

    def test_bonus_credits_used_and_restored(self):
        with self.sessions() as db:
            grant_credits(db,user_id='member',amount=1,source_type='test',source_id='one',idempotency_key='bonus-one');db.commit()
        job=self.submit(self.quote()).json()
        self.client.post('/api/v1/cloud/video/jobs/'+job['job_id']+'/cancel',headers=self.headers)
        self.assertEqual(self.balance(),(11,0,0))
