"""Durable prepare/submit receipts. Unknown submissions are NEVER resubmitted."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class State:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.db = self.folder / 'receipts.sqlite3'
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, operation TEXT NOT NULL, arguments TEXT NOT NULL,
                guard TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL,
                result TEXT, UNIQUE(owner, request_id))''')
            db.execute('''CREATE TABLE IF NOT EXISTS results (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, filename TEXT NOT NULL,
                mime TEXT NOT NULL, size INTEGER NOT NULL)''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def prepare(self, owner, request_id, fingerprint, operation, arguments, guard):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM plans WHERE owner=? AND request_id=?', (owner, request_id)).fetchone()
            if row:
                if row['fingerprint'] != fingerprint:
                    raise ValueError('request_id 已用于另一组参数；不可覆盖或重复提交')
                return dict(row)
            ident = uuid.uuid4().hex
            db.execute('INSERT INTO plans VALUES (?,?,?,?,?,?,?,?,?,NULL)',
                       (ident, owner, request_id, fingerprint, operation,
                        json.dumps(arguments, ensure_ascii=False), json.dumps(guard), time.time(), 'prepared'))
            return dict(db.execute('SELECT * FROM plans WHERE id=?', (ident,)).fetchone())

    def get(self, owner, ident):
        with self.connect() as db:
            row = db.execute('SELECT * FROM plans WHERE owner=? AND id=?', (owner, ident)).fetchone()
        if not row:
            raise ValueError('找不到当前用户的执行计划')
        return dict(row)

    def claim(self, owner, ident):
        with self.connect() as db:
            return db.execute("UPDATE plans SET status='submitting' WHERE owner=? AND id=? AND status='prepared'",
                              (owner, ident)).rowcount == 1

    def finish(self, owner, ident, status, result):
        with self.connect() as db:
            db.execute('UPDATE plans SET status=?, result=? WHERE owner=? AND id=?',
                       (status, json.dumps(result, ensure_ascii=False), owner, ident))

    def discard(self, owner, ident):
        with self.connect() as db:
            return db.execute("UPDATE plans SET status='discarded' WHERE owner=? AND id=? AND status='prepared'",
                              (owner, ident)).rowcount == 1

    def put_result(self, owner, result):
        ident = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO results VALUES (?,?,?)', (ident, owner, json.dumps(result, ensure_ascii=False)))
        return ident

    def get_result(self, owner, ident):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM results WHERE owner=? AND id=?', (owner, ident)).fetchone()
        if not row:
            raise ValueError('找不到当前用户的结果')
        return json.loads(row['payload'])

    def add_asset(self, owner, path, mime):
        ident = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO assets VALUES (?,?,?,?,?)', (ident, owner, str(path), mime, path.stat().st_size))
        return {'asset_id': ident, 'uri': f'ocv://assets/{ident}', 'path': str(path),
                'mime_type': mime, 'size': path.stat().st_size}

    def asset(self, owner, ident):
        with self.connect() as db:
            row = db.execute('SELECT * FROM assets WHERE owner=? AND id=?', (owner, ident)).fetchone()
        if not row:
            raise ValueError('找不到当前用户的素材')
        return dict(row)
