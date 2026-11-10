import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["AUTO_CREATE_SCHEMA"] = "false"

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.announcements import Announcement, create_schema, router
from app.db import Base, get_db
from app.models import AdminAuditLog, User
from app.security import create_access_token


class AnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + str(Path(self.temp.name) / "test.db"),
                                    connect_args={"check_same_thread": False, "timeout": 10})
        Base.metadata.create_all(self.engine, tables=[User.__table__, AdminAuditLog.__table__])
        create_schema(self.engine)
        create_schema(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as db:
            for user_id, role in [("admin", "admin"), ("reader", "user"), ("second", "admin")]:
                db.add(User(id=user_id, email=f"{user_id}@example.com", password_hash="unused", role=role))
            db.commit()
        app = FastAPI()
        app.include_router(router)
        def database():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = database
        self.client = TestClient(app)
        self.headers = self.auth("admin", "admin")

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.temp.cleanup()

    def auth(self, user_id, role):
        return {"Authorization": "Bearer " + create_access_token(user_id, role)[0]}

    def create(self, client_id="test", **changes):
        payload = {"title": " Update ", "body": " Details\nsecond line ", "client_id": client_id, **changes}
        return self.client.post("/api/v1/admin/announcements", headers=self.headers, json=payload)

    def edit(self, row, **changes):
        payload = {key: row[key] for key in ("title", "body", "pinned", "status", "revision")}
        payload.update(changes)
        return self.client.put("/api/v1/admin/announcements/" + row["id"], headers=self.headers, json=payload)

    def test_authentication_and_public_allowlist(self):
        self.assertEqual(self.client.get("/api/v1/admin/announcements").status_code, 401)
        user_headers = self.auth("reader", "user")
        self.assertEqual(self.client.get("/api/v1/admin/announcements", headers=user_headers).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/admin/announcements", headers=user_headers,
                                        json={"title": "x", "body": "y", "client_id": "z"}).status_code, 403)
        draft = self.create("draft")
        self.assertEqual(draft.status_code, 201, draft.text)
        self.assertEqual(self.client.put("/api/v1/admin/announcements/" + draft.json()["id"],
             headers=user_headers, json={"title": "x", "body": "y", "revision": 1}).status_code, 403)
        self.create("old", status="archived")
        pub = self.create("published", status="published").json()
        response = self.client.get("/api/v1/announcements")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["total"], 1)
        item = response.json()["items"][0]
        self.assertEqual(item["id"], pub["id"])
        self.assertEqual(set(item), {"id", "title", "body", "pinned", "revision", "updated_at", "published_at"})
        self.assertEqual(item["title"], "Update")
        self.assertEqual(item["body"], "Details\nsecond line")
        self.assertTrue(item["published_at"].endswith("+00:00"))
        self.assertEqual(self.client.get("/api/v1/admin/announcements", headers=self.headers).json()["total"], 3)

    def test_pagination_pin_and_archive(self):
        pinned = self.create("pinned", status="published", pinned=True).json()
        self.create("ordinary", status="published")
        page = self.client.get("/api/v1/announcements?limit=1&offset=0").json()
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["items"][0]["id"], pinned["id"])
        self.assertEqual(len(self.client.get("/api/v1/announcements?limit=1&offset=1").json()["items"]), 1)
        archived = self.edit(pinned, status="archived")
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertEqual(archived.json()["revision"], 2)
        self.assertEqual(self.client.get("/api/v1/announcements").json()["total"], 1)
        republished = self.edit(archived.json(), status="published")
        self.assertEqual(republished.status_code, 200)
        self.assertGreaterEqual(republished.json()["published_at"], pinned["published_at"])

    def test_admin_status_filter_does_not_change_public_visibility(self):
        for state in ["draft", "published", "archived"]:
            self.create(state, status=state)
        self.create("another-draft", status="draft")
        for state, count in [("draft", 2), ("published", 1), ("archived", 1)]:
            response = self.client.get("/api/v1/admin/announcements?status=" + state, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["total"], count)
            self.assertTrue(all(item["status"] == state for item in response.json()["items"]))
        page = self.client.get("/api/v1/admin/announcements?status=draft&limit=1&offset=1", headers=self.headers).json()
        self.assertEqual(page["total"], 2)
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(self.client.get("/api/v1/admin/announcements?status=invalid", headers=self.headers).status_code, 422)
        public = self.client.get("/api/v1/announcements?status=draft").json()
        self.assertEqual(public["total"], 1)
        self.assertEqual(public["items"][0]["id"], self.client.get("/api/v1/announcements").json()["items"][0]["id"])

    def test_idempotent_create_and_payload_conflict(self):
        row = self.create().json()
        replay = self.create()
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["id"], row["id"])
        self.assertEqual(self.create(body="different").status_code, 409)
        self.edit(row, title="Edited later")
        self.assertEqual(self.create().json()["title"], "Edited later")
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Announcement)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(AdminAuditLog)), 2)
            self.assertNotIn("body", db.scalar(select(AdminAuditLog)).details)
        self.assertEqual(self.client.post("/api/v1/admin/announcements", headers=self.auth("second", "admin"),
            json={"title": "other", "body": "admin", "client_id": "test"}).status_code, 201)

    def test_simultaneous_create_and_revision_update(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.create("concurrent"), range(2)))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 201])
        self.assertEqual(responses[0].json()["id"], responses[1].json()["id"])
        row = responses[0].json()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda title: self.edit(row, title=title), ["First", "Second"]))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        with self.sessions() as db:
            saved = db.get(Announcement, row["id"])
            self.assertEqual(saved.revision, 2)
            self.assertEqual(db.scalar(select(func.count()).select_from(AdminAuditLog)), 2)
        self.assertEqual(self.client.put("/api/v1/admin/announcements/missing", headers=self.headers,
             json={"title": "x", "body": "y", "revision": 1}).status_code, 404)

    def test_validation_and_plaintext_roundtrip(self):
        for changes in [{"title": "  "}, {"body": "\n\t"}, {"title": "x" * 161},
                        {"body": "x" * 10001}, {"body": "bad\u0000text"},
                        {"status": "unknown"}, {"client_id": " "}, {"unexpected": True}]:
            with self.subTest(changes=list(changes)):
                self.assertEqual(self.create(**changes).status_code, 422)
        for query in ["limit=101", "limit=0", "offset=-1"]:
            self.assertEqual(self.client.get("/api/v1/announcements?" + query).status_code, 422)
        text = '<script>alert("literal text")</script>'
        row = self.create(body=text).json()
        self.assertEqual(row["body"], text)
        self.assertEqual(self.edit(row, revision=True).status_code, 422)
        self.assertEqual(self.edit(row, revision=0).status_code, 422)


if __name__ == "__main__":
    unittest.main()
