"""MCP cache retention: old/oversized results and downloaded assets are pruned; receipts stay.
Offline: temporary folders only. Run with runtime/ocv_mcp Python (adds plugins/codex_bridge to sys.path)."""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins' / 'codex_bridge'))
from ocv_mcp import state as state_module  # noqa: E402
from ocv_mcp.state import State  # noqa: E402


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(self.tmp.name)
        self.state.last_prune = time.time()  # keep automatic pruning out of the setup

    def tearDown(self):
        self.tmp.cleanup()

    def asset(self, name, age_days, size=10):
        folder = Path(self.tmp.name) / 'assets' / 'owner'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(b'x' * size)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return self.state.add_asset('owner', path, 'image/png')

    def test_old_assets_removed_with_their_rows_recent_kept(self):
        old = self.asset('old.png', 20)
        new = self.asset('new.png', 1)
        removed = self.state.prune()
        self.assertEqual(removed['files'], 1)
        self.assertFalse(Path(old['path']).exists())
        self.assertTrue(Path(new['path']).exists())
        with self.assertRaises(ValueError):
            self.state.asset('owner', old['asset_id'])
        self.assertEqual(self.state.asset('owner', new['asset_id'])['id'], new['asset_id'])

    def test_asset_rows_kept_when_file_exists_even_if_path_spelled_differently(self):
        item = self.asset('keep.png', 1)
        with sqlite3.connect(self.state.db) as db:  # e.g. a mapped / differently spelled path to the same file
            db.execute('UPDATE assets SET filename=? WHERE id=?', (item['path'].replace(os.sep, '/'), item['asset_id']))
        self.state.prune()
        self.assertEqual(self.state.asset('owner', item['asset_id'])['id'], item['asset_id'])

    def test_asset_total_size_cap_removes_oldest_first(self):
        with mock.patch.object(state_module, 'ASSET_MAX_BYTES', 25):
            first, second, third = self.asset('a.png', 3), self.asset('b.png', 2), self.asset('c.png', 1)
            self.state.prune()
        self.assertFalse(Path(first['path']).exists())
        self.assertTrue(Path(second['path']).exists() and Path(third['path']).exists())

    def test_results_pruned_by_age_and_size_receipts_kept(self):
        keep = self.state.put_result('owner', {'v': 'recent'})
        old = self.state.put_result('owner', {'v': 'old'})
        with sqlite3.connect(self.state.db) as db:
            db.execute('UPDATE results SET created=? WHERE id=?', (time.time() - 30 * 86400, old))
        plan = self.state.prepare('owner', 'request-0001', 'f', 'op', {}, {})
        self.state.prune()
        self.assertEqual(self.state.get_result('owner', keep), {'v': 'recent'})
        with self.assertRaises(ValueError):
            self.state.get_result('owner', old)
        self.assertEqual(self.state.get('owner', plan['id'])['status'], 'prepared')
        with mock.patch.object(state_module, 'RESULT_MAX_BYTES', 1):
            self.state.prune()
        with self.assertRaises(ValueError):
            self.state.get_result('owner', keep)

    def test_finished_old_plans_pruned_pending_plans_never(self):
        done = self.state.prepare('owner', 'request-done1', 'f1', 'op', {}, {})
        waiting = self.state.prepare('owner', 'request-wait1', 'f2', 'op', {}, {})
        self.state.finish('owner', done['id'], 'submitted', {})
        with sqlite3.connect(self.state.db) as db:
            db.execute('UPDATE plans SET created=?', (time.time() - 90 * 86400,))
        self.state.prune()
        with self.assertRaises(ValueError):
            self.state.get('owner', done['id'])
        self.assertEqual(self.state.get('owner', waiting['id'])['status'], 'prepared')

    def test_legacy_database_without_result_timestamps_is_migrated(self):
        folder = Path(self.tmp.name) / 'legacy'
        folder.mkdir()
        with sqlite3.connect(folder / 'receipts.sqlite3') as db:
            db.execute('CREATE TABLE results (id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL)')
            db.execute("INSERT INTO results VALUES ('r1', 'owner', '{}')")
        legacy = State(folder)
        self.assertEqual(legacy.get_result('owner', 'r1'), {})
        legacy.prune()
        self.assertEqual(legacy.get_result('owner', 'r1'), {})  # migrated rows count as recent


if __name__ == '__main__':
    unittest.main()
