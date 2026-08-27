import sqlite3
import unittest

from backend.app.db import SQLiteCursor


class SQLiteCursorTest(unittest.TestCase):
    def test_rowcount_is_forwarded_for_delete_operations(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            cursor = SQLiteCursor(connection.cursor())
            cursor.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY)")
            cursor.execute("INSERT INTO jobs (id) VALUES (%s)", ("job-1",))
            self.assertEqual(cursor.rowcount, 1)

            cursor.execute("DELETE FROM jobs WHERE id=%s", ("job-1",))
            self.assertEqual(cursor.rowcount, 1)

            cursor.execute("DELETE FROM jobs WHERE id=%s", ("missing",))
            self.assertEqual(cursor.rowcount, 0)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
