"""Standalone integration tests: real SQLAlchemy + FastAPI, isolated SQLite/auth."""
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from datetime import timedelta
from typing import Annotated

sys.path.insert(0, str(Path(__file__).parent / "test-deps"))
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, String, select, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)


engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


def get_db():
    with Session(engine, expire_on_commit=False) as db:
        yield db


def current_user(authorization: str = Header(default="")):
    if authorization not in {"Bearer a", "Bearer b"}:
        raise HTTPException(401, "Authentication required")
    return User(id=authorization[-1])


package = types.ModuleType("h3test")
package.__path__ = []
sys.modules["h3test"] = package
for name, values in {
    "db": {"Base": Base, "SessionLocal": sessionmaker(engine, expire_on_commit=False)}, "models": {"User": User},
    "deps": {"Db": Annotated[Session, Depends(get_db)], "CurrentUser": Annotated[User, Depends(current_user)]},
}.items():
    module = types.ModuleType("h3test." + name)
    module.__dict__.update(values)
    sys.modules[module.__name__] = module
spec = importlib.util.spec_from_file_location("h3test.cloud_video_api", Path(__file__).parents[2] / "app/cloud_video_api.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)
app = FastAPI()
app.include_router(api.router)
client = TestClient(app)
PREFIX = "/api/v1/cloud/video"


class ApiTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add_all([User(id="a"), User(id="b")])
            db.commit()
        self.temp = tempfile.TemporaryDirectory()
        os.environ.update(CLOUD_VIDEO_ENABLED="1", CLOUD_VIDEO_STORAGE_DIR=self.temp.name, CLOUD_VIDEO_TEST_USER_IDS="")
        self.headers = {"Authorization": "Bearer a"}

    def tearDown(self):
        self.temp.cleanup()

    def upload(self):
        image = io.BytesIO()
        Image.new("RGB", (64, 64)).save(image, format="PNG")
        result = client.post(PREFIX + "/assets", headers=self.headers, data={"kind": "image"}, files={"file": ("x.png", image.getvalue(), "image/png")})
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()["asset_id"]

    def quote(self, **overrides):
        spec = {"prompt": "tree", "reference_image_asset_id": self.upload(), "width": 640, "height": 384, "duration_seconds": 2}
        spec.update(overrides)
        return client.post(PREFIX + "/quotes", headers=self.headers, json={"spec": spec})

    def job(self):
        quote = self.quote().json()
        result = client.post(PREFIX + "/jobs", headers=self.headers, json={"quote_id": quote["quote_id"], "client_job_id": "stable-id"})
        self.assertEqual(result.status_code, 201, result.text)
        return result.json(), quote

    def test_auth_feature_and_allowlist(self):
        self.assertEqual(client.get(PREFIX + "/capabilities").status_code, 401)
        os.environ["CLOUD_VIDEO_TEST_USER_IDS"] = "b"
        caps = client.get(PREFIX + "/capabilities", headers=self.headers).json()
        self.assertFalse(caps["enabled"])
        self.assertTrue(caps["test_mode"])
        image = io.BytesIO(b"x")
        self.assertEqual(client.post(PREFIX + "/assets", headers=self.headers, data={"kind": "image"}, files={"file": ("x", image)}).status_code, 503)

    def test_normalization_free_seed_snapshot(self):
        job, quote = self.job()
        self.assertEqual(quote["credits"], "0")
        self.assertEqual(job["spec"], quote["spec"])
        self.assertEqual(job["spec"]["num_frames"], 56)
        self.assertEqual(job["spec"]["num_frames"] % 17, 5)
        self.assertTrue(0 <= job["spec"]["seed"] < 2**32)
        self.assertEqual(job["status"], "queued")

    def test_capabilities_declare_supported_2k_defaults_and_preserve_quote_pixels(self):
        caps=client.get(PREFIX + "/capabilities", headers=self.headers).json()
        self.assertEqual(caps['default_resolutions'],{
            'landscape':{'width':1664,'height':936},'portrait':{'width':936,'height':1664}})
        self.assertEqual(caps['duration_seconds'],{'min':2,'max':15})
        self.assertEqual(caps['duration_policy'],'client')
        self.assertIs(caps['duration_limit_enforced'],False)
        for dimensions in list(caps['default_resolutions'].values())+[{'width':640,'height':384}]:
            with self.subTest(dimensions=dimensions):
                self.assertIn(dimensions,caps['resolutions'])
                response=self.quote(**dimensions,duration_seconds=15)
                self.assertEqual(response.status_code,201,response.text)
                spec=response.json()['spec']
                self.assertEqual({key:spec[key] for key in ('width','height')},dimensions)
                self.assertEqual(spec['duration_seconds'],15)
                self.assertGreaterEqual(spec['actual_duration_seconds'],15)

    def test_client_duration_is_preserved_without_a_server_ceiling(self):
        for seconds in (0.25,1.9,10.48,10.68,11,15,15.01,16,30):
            with self.subTest(seconds=seconds):
                response=self.quote(duration_seconds=seconds)
                self.assertEqual(response.status_code,201,response.text)
                quote=response.json()
                spec=quote['spec']
                self.assertEqual(spec['duration_seconds'],seconds)
                self.assertGreaterEqual(spec['actual_duration_seconds'],seconds)
                self.assertLess(spec['actual_duration_seconds']-seconds,18/24)
                self.assertEqual(spec['num_frames']%17,5)
                job=client.post(PREFIX+'/jobs',headers=self.headers,json={'quote_id':quote['quote_id']})
                self.assertEqual(job.status_code,201,job.text)
                self.assertEqual(job.json()['spec'],spec)
                # Keep each case independent of the per-user queue quota.
                self.assertEqual(client.post(PREFIX+'/jobs/'+job.json()['job_id']+'/cancel',headers=self.headers).status_code,200)

    def test_invalid_duration_is_rejected_before_creating_a_quote(self):
        for seconds in (0,-1,'NaN','Infinity','-Infinity'):
            with self.subTest(seconds=seconds):
                self.assertEqual(self.quote(duration_seconds=seconds).status_code,422)
        with Session(engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(api.CloudVideoQuote)),0)

    def test_idempotency_and_payload_conflict(self):
        job, quote = self.job()
        for payload in [{"quote_id": quote["quote_id"], "client_job_id": "stable-id"}, {"quote_id": quote["quote_id"]}]:
            response = client.post(PREFIX + "/jobs", headers=self.headers, json=payload)
            self.assertEqual(response.json()["job_id"], job["job_id"])
        other = self.quote().json()
        response = client.post(PREFIX + "/jobs", headers=self.headers, json={"quote_id": other["quote_id"], "client_job_id": "stable-id"})
        self.assertEqual(response.status_code, 409)
        with Session(engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(api.CloudVideoJob)), 1)

    def test_cross_user_isolation(self):
        job, quote = self.job()
        other = {"Authorization": "Bearer b"}
        for suffix in ["/jobs/" + job["job_id"], "/jobs/by-client-id/stable-id", "/jobs/" + job["job_id"] + "/result"]:
            self.assertEqual(client.get(PREFIX + suffix, headers=other).status_code, 404)
        self.assertEqual(client.post(PREFIX + "/jobs/" + job["job_id"] + "/cancel", headers=other).status_code, 404)
        self.assertEqual(client.post(PREFIX + "/jobs", headers=other, json={"quote_id": quote["quote_id"]}).status_code, 404)
        self.assertEqual(client.post(PREFIX + "/quotes", headers=other, json={"spec": {k: v for k, v in quote["spec"].items() if k in api.VideoSpec.model_fields}}).status_code, 404)

    def test_cancel_queued_and_running(self):
        job, _ = self.job()
        path = PREFIX + "/jobs/" + job["job_id"] + "/cancel"
        self.assertEqual(client.post(path, headers=self.headers).json()["status"], "cancelled")
        with Session(engine) as db:
            record = db.get(api.CloudVideoJob, job["job_id"])
            record.status = "running"
            db.commit()
        self.assertEqual(client.post(path, headers=self.headers).json()["status"], "cancelling")

    def test_expired_quote(self):
        quote = self.quote().json()
        with Session(engine) as db:
            db.get(api.CloudVideoQuote, quote["quote_id"]).expires_at = api.utcnow() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(client.post(PREFIX + "/jobs", headers=self.headers, json={"quote_id": quote["quote_id"]}).status_code, 409)

    def test_reject_unsupported_spec_and_fake_image(self):
        self.assertEqual(self.quote(width=999).status_code, 422)
        self.assertEqual(self.quote(prompt="  ").status_code, 422)
        self.assertEqual(self.quote(graph={"1": {"class_type": "Execute"}}).status_code, 422)
        self.assertEqual(client.post(PREFIX + "/assets", headers=self.headers, data={"kind": "image"}, files={"file": ("x.png", b"not an image", "image/png")}).status_code, 422)
        with self.assertRaises(ValueError):
            api.storage_path("../../secret")

    def test_private_result(self):
        job, _ = self.job()
        path = PREFIX + "/jobs/" + job["job_id"] + "/result"
        self.assertEqual(client.get(path, headers=self.headers).status_code, 409)
        key = "results/" + job["job_id"] + ".mp4"
        api.save_bytes(key, b"test-mp4")
        with Session(engine) as db:
            record = db.get(api.CloudVideoJob, job["job_id"])
            record.status, record.result_path = "succeeded", key
            db.commit()
        result = client.get(path, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.content, b"test-mp4")
        self.assertEqual(result.headers["cache-control"], "private, no-store")


if __name__ == "__main__":
    unittest.main(verbosity=2)
