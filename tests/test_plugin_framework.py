# SPDX-License-Identifier: AGPL-3.0-only

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import main


class PluginFrameworkTests(unittest.TestCase):
    def test_scanner_reads_manifest_without_executing_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin = root / "safe_demo"
            plugin.mkdir()
            (plugin / "plugin.json").write_text(
                json.dumps(
                    {
                        "manifest_version": 1,
                        "id": "safe_demo",
                        "name": "Safe Demo",
                        "version": "1.0.0",
                        "author": "Tester",
                        "entry": "entry.py",
                        "permissions": [],
                    }
                ),
                encoding="utf-8",
            )
            (plugin / "entry.py").write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
            (plugin / "disabled").write_text("disabled\n", encoding="utf-8")

            with patch.object(main, "PLUGINS_DIR", root):
                items = main._plugin_manifest_items()

            self.assertEqual(len(items), 1)
            self.assertTrue(items[0]["valid"])
            self.assertFalse(items[0]["enabled"])
            self.assertTrue(items[0]["framework_only"])

    def test_scanner_marks_unsupported_manifest_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin = root / "future_plugin"
            plugin.mkdir()
            (plugin / "plugin.json").write_text(
                json.dumps({"manifest_version": 99, "id": "future_plugin"}),
                encoding="utf-8",
            )

            with patch.object(main, "PLUGINS_DIR", root):
                items = main._plugin_manifest_items()

            self.assertEqual(len(items), 1)
            self.assertFalse(items[0]["valid"])
            self.assertIn("manifest_version", items[0]["issue"])


if __name__ == "__main__":
    unittest.main()
