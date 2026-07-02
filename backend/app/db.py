import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from datetime import datetime
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from .config import load_project_env

load_project_env()


MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "voice_over_video")

_db_ready = False
_last_error: str | None = None


def root_connection():
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=DictCursor,
    )


def connection():
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=DictCursor,
    )


def init_database() -> None:
    global _db_ready, _last_error
    try:
        with root_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DATABASE}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
        with connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                      email VARCHAR(255) NOT NULL UNIQUE,
                      name VARCHAR(120) NOT NULL,
                      password_hash VARCHAR(255) NOT NULL,
                      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS generation_jobs (
                      id VARCHAR(32) NOT NULL PRIMARY KEY,
                      user_id BIGINT UNSIGNED NULL,
                      status VARCHAR(32) NOT NULL,
                      step VARCHAR(64) NOT NULL,
                      progress INT NOT NULL DEFAULT 0,
                      message VARCHAR(500) NOT NULL,
                      request_json LONGTEXT NOT NULL,
                      artifacts_json LONGTEXT NOT NULL,
                      error LONGTEXT NULL,
                      created_at DOUBLE NOT NULL,
                      updated_at DOUBLE NOT NULL,
                      INDEX idx_generation_jobs_user_created (user_id, created_at),
                      CONSTRAINT fk_generation_jobs_user
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS generation_job_logs (
                      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                      job_id VARCHAR(32) NOT NULL,
                      line TEXT NOT NULL,
                      created_at DOUBLE NOT NULL,
                      INDEX idx_generation_logs_job_id (job_id, id),
                      CONSTRAINT fk_generation_logs_job
                        FOREIGN KEY (job_id) REFERENCES generation_jobs(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS editor_jobs (
                      id VARCHAR(32) NOT NULL PRIMARY KEY,
                      user_id BIGINT UNSIGNED NOT NULL,
                      status VARCHAR(32) NOT NULL,
                      progress INT NOT NULL DEFAULT 0,
                      message VARCHAR(500) NOT NULL,
                      request_json LONGTEXT NOT NULL,
                      artifacts_json LONGTEXT NOT NULL,
                      error LONGTEXT NULL,
                      created_at DOUBLE NOT NULL,
                      updated_at DOUBLE NOT NULL,
                      INDEX idx_editor_jobs_user_created (user_id, created_at),
                      CONSTRAINT fk_editor_jobs_user
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS editor_job_logs (
                      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                      job_id VARCHAR(32) NOT NULL,
                      line TEXT NOT NULL,
                      created_at DOUBLE NOT NULL,
                      INDEX idx_editor_logs_job_id (job_id, id),
                      CONSTRAINT fk_editor_logs_job
                        FOREIGN KEY (job_id) REFERENCES editor_jobs(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS media_assets (
                      id CHAR(32) NOT NULL PRIMARY KEY,
                      asset_key CHAR(64) NOT NULL UNIQUE,
                      user_id BIGINT UNSIGNED NULL,
                      generation_job_id VARCHAR(32) NULL,
                      editor_job_id VARCHAR(32) NULL,
                      kind VARCHAR(32) NOT NULL,
                      role VARCHAR(80) NOT NULL,
                      storage_backend VARCHAR(32) NOT NULL,
                      storage_path TEXT NULL,
                      remote_id VARCHAR(255) NULL,
                      original_name VARCHAR(255) NULL,
                      mime_type VARCHAR(120) NULL,
                      size_bytes BIGINT NULL,
                      duration_seconds DOUBLE NULL,
                      sequence_index INT NULL,
                      metadata_json LONGTEXT NOT NULL,
                      created_at DOUBLE NOT NULL,
                      updated_at DOUBLE NOT NULL,
                      INDEX idx_media_user_role (user_id, role),
                      INDEX idx_media_generation_job (generation_job_id, role),
                      INDEX idx_media_editor_job (editor_job_id, role),
                      INDEX idx_media_remote_id (remote_id),
                      CONSTRAINT fk_media_user
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
        _db_ready = True
        _last_error = None
    except Exception as exc:
        _db_ready = False
        _last_error = str(exc)


def db_status() -> dict[str, Any]:
    return {
        "ready": _db_ready,
        "host": MYSQL_HOST,
        "port": MYSQL_PORT,
        "database": MYSQL_DATABASE,
        "last_error": _last_error,
    }


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 240_000)
    return f"pbkdf2_sha256$240000${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, rounds_text, salt, expected = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            int(rounds_text),
        ).hex()
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


def public_user(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    created_at = row.get("created_at")
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "created_at": created_at,
    }


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, email, name, created_at FROM users WHERE id=%s", (user_id,))
            return public_user(cursor.fetchone())


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE email=%s", (email.lower().strip(),))
            return cursor.fetchone()


def create_user(email: str, password: str, name: str | None = None) -> dict[str, Any]:
    normalized_email = email.lower().strip()
    display_name = (name or normalized_email.split("@")[0]).strip()[:120]
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (email, name, password_hash) VALUES (%s, %s, %s)",
                (normalized_email, display_name, hash_password(password)),
            )
            user_id = cursor.lastrowid
    user = get_user_by_id(int(user_id))
    if not user:
        raise RuntimeError("用户创建后读取失败")
    return user


def authenticate_user(email: str, password: str) -> dict[str, Any] | None:
    row = get_user_by_email(email)
    if not row or not verify_password(password, row["password_hash"]):
        return None
    return public_user(row)


def sole_user_id() -> int | None:
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM users ORDER BY id LIMIT 2")
            rows = list(cursor.fetchall())
    if len(rows) == 1:
        return int(rows[0]["id"])
    return None


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _json_load(value: str | bytes | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def upsert_generation_job(snapshot: dict[str, Any]) -> None:
    request = {key: value for key, value in snapshot.get("request", {}).items() if key != "api_key"}
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO generation_jobs (
                  id, user_id, status, step, progress, message, request_json,
                  artifacts_json, error, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  user_id=VALUES(user_id),
                  status=VALUES(status),
                  step=VALUES(step),
                  progress=VALUES(progress),
                  message=VALUES(message),
                  request_json=VALUES(request_json),
                  artifacts_json=VALUES(artifacts_json),
                  error=VALUES(error),
                  updated_at=VALUES(updated_at)
                """,
                (
                    snapshot["id"],
                    snapshot.get("user_id"),
                    snapshot.get("status", "queued"),
                    snapshot.get("step", "queued"),
                    int(snapshot.get("progress", 0)),
                    str(snapshot.get("message", ""))[:500],
                    _json_dump(request),
                    _json_dump(snapshot.get("artifacts", {})),
                    snapshot.get("error"),
                    float(snapshot.get("created_at", time.time())),
                    float(snapshot.get("updated_at", time.time())),
                ),
            )


def append_generation_job_log(job_id: str, line: str, created_at: float | None = None) -> None:
    timestamp = float(created_at or time.time())
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO generation_job_logs (job_id, line, created_at) VALUES (%s, %s, %s)",
                (job_id, line, timestamp),
            )
            cursor.execute(
                "UPDATE generation_jobs SET updated_at=%s WHERE id=%s",
                (timestamp, job_id),
            )


def load_generation_jobs(limit: int = 100) -> list[dict[str, Any]]:
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM generation_jobs ORDER BY created_at DESC LIMIT %s",
                (int(limit),),
            )
            rows = list(cursor.fetchall())
            for row in rows:
                cursor.execute(
                    "SELECT line FROM generation_job_logs WHERE job_id=%s ORDER BY id ASC",
                    (row["id"],),
                )
                row["logs"] = [item["line"] for item in cursor.fetchall()]
    for row in rows:
        row["request"] = _json_load(row.pop("request_json", None), {})
        row["artifacts"] = _json_load(row.pop("artifacts_json", None), {})
    return rows


def upsert_editor_job(snapshot: dict[str, Any]) -> None:
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO editor_jobs (
                  id, user_id, status, progress, message, request_json,
                  artifacts_json, error, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  user_id=VALUES(user_id),
                  status=VALUES(status),
                  progress=VALUES(progress),
                  message=VALUES(message),
                  request_json=VALUES(request_json),
                  artifacts_json=VALUES(artifacts_json),
                  error=VALUES(error),
                  updated_at=VALUES(updated_at)
                """,
                (
                    snapshot["id"],
                    snapshot["user_id"],
                    snapshot.get("status", "queued"),
                    int(snapshot.get("progress", 0)),
                    str(snapshot.get("message", ""))[:500],
                    _json_dump(snapshot.get("request", {})),
                    _json_dump(snapshot.get("artifacts", {})),
                    snapshot.get("error"),
                    float(snapshot.get("created_at", time.time())),
                    float(snapshot.get("updated_at", time.time())),
                ),
            )


def append_editor_job_log(job_id: str, line: str, created_at: float | None = None) -> None:
    timestamp = float(created_at or time.time())
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO editor_job_logs (job_id, line, created_at) VALUES (%s, %s, %s)",
                (job_id, line, timestamp),
            )
            cursor.execute(
                "UPDATE editor_jobs SET updated_at=%s WHERE id=%s",
                (timestamp, job_id),
            )


def load_editor_jobs(limit: int = 100) -> list[dict[str, Any]]:
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM editor_jobs ORDER BY created_at DESC LIMIT %s",
                (int(limit),),
            )
            rows = list(cursor.fetchall())
            for row in rows:
                cursor.execute(
                    "SELECT line FROM editor_job_logs WHERE job_id=%s ORDER BY id ASC",
                    (row["id"],),
                )
                row["logs"] = [item["line"] for item in cursor.fetchall()]
    for row in rows:
        row["request"] = _json_load(row.pop("request_json", None), {})
        row["artifacts"] = _json_load(row.pop("artifacts_json", None), {})
    return rows


def record_media_asset(
    *,
    user_id: int | None,
    kind: str,
    role: str,
    storage_backend: str = "local",
    storage_path: str | None = None,
    generation_job_id: str | None = None,
    editor_job_id: str | None = None,
    remote_id: str | None = None,
    original_name: str | None = None,
    mime_type: str | None = None,
    size_bytes: int | None = None,
    duration_seconds: float | None = None,
    sequence_index: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    identity = "\x1f".join(
        str(value or "")
        for value in (
            user_id,
            generation_job_id,
            editor_job_id,
            role,
            storage_backend,
            storage_path,
            remote_id,
            sequence_index,
        )
    )
    asset_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    asset_id = uuid.uuid4().hex
    now = time.time()
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO media_assets (
                  id, asset_key, user_id, generation_job_id, editor_job_id,
                  kind, role, storage_backend, storage_path, remote_id,
                  original_name, mime_type, size_bytes, duration_seconds,
                  sequence_index, metadata_json, created_at, updated_at
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON DUPLICATE KEY UPDATE
                  user_id=VALUES(user_id),
                  generation_job_id=VALUES(generation_job_id),
                  editor_job_id=VALUES(editor_job_id),
                  kind=VALUES(kind),
                  role=VALUES(role),
                  storage_backend=VALUES(storage_backend),
                  storage_path=VALUES(storage_path),
                  remote_id=VALUES(remote_id),
                  original_name=VALUES(original_name),
                  mime_type=VALUES(mime_type),
                  size_bytes=VALUES(size_bytes),
                  duration_seconds=VALUES(duration_seconds),
                  sequence_index=VALUES(sequence_index),
                  metadata_json=VALUES(metadata_json),
                  updated_at=VALUES(updated_at)
                """,
                (
                    asset_id,
                    asset_key,
                    user_id,
                    generation_job_id,
                    editor_job_id,
                    kind,
                    role,
                    storage_backend,
                    storage_path,
                    remote_id,
                    original_name,
                    mime_type,
                    size_bytes,
                    duration_seconds,
                    sequence_index,
                    _json_dump(metadata or {}),
                    now,
                    now,
                ),
            )
            cursor.execute("SELECT id FROM media_assets WHERE asset_key=%s", (asset_key,))
            row = cursor.fetchone()
    return str(row["id"] if row else asset_id)


def list_media_assets(
    *,
    user_id: int | None = None,
    role: str | None = None,
    generation_job_id: str | None = None,
    editor_job_id: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    for column, value in (
        ("user_id", user_id),
        ("role", role),
        ("generation_job_id", generation_job_id),
        ("editor_job_id", editor_job_id),
    ):
        if value is not None:
            clauses.append(f"{column}=%s")
            values.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM media_assets{where} ORDER BY created_at DESC",
                tuple(values),
            )
            rows = list(cursor.fetchall())
    for row in rows:
        row["metadata"] = _json_load(row.pop("metadata_json", None), {})
    return rows
