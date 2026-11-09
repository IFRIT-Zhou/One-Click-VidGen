import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from PIL import Image

from tools.rmbg import worker


class StandaloneRmbgWorkerTest(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name).resolve()
        self.job = self.root / "job"
        self.job.mkdir()
        self.manifest = self.job / "manifest.json"

    def items(self, count=1):
        items = []
        for i in range(count):
            src, dest = self.job / f"src{i}.png", self.job / f"mask{i}.png"
            Image.new("RGB", (20 + i, 10), (150, 20, 30)).save(src)
            items.append({"input": str(src), "output": str(dest)})
        return items

    def write(self, items):
        self.manifest.write_text(json.dumps({"items": items}), encoding="utf-8")
        return self.manifest

    def test_renders_individual_native_size_masks_with_one_model(self):
        items = self.items(2)
        loader = Mock(return_value=("cpu", lambda img: img.getchannel("R")))
        receipt = worker.run(self.write(items), self.root, "cpu", loader=loader)
        self.assertEqual(receipt, {"status": "completed", "device": "cpu", "count": 2})
        loader.assert_called_once_with(self.root, "cpu")
        for i, item in enumerate(items):
            with Image.open(item["output"]) as mask:
                self.assertEqual((mask.size, mask.mode, mask.getextrema()), ((20+i, 10), "L", (150, 150)))

    def test_rejects_invalid_mask_before_writing(self):
        items = self.items()
        for infer in (lambda img: img, lambda img: Image.new("L", (1, 1))):
            with self.assertRaisesRegex(ValueError, "灰度蒙版"):
                worker.run(self.write(items), self.root, loader=lambda *_: ("cuda", infer))
            self.assertFalse(Path(items[0]["output"]).exists())

    def test_rejects_invalid_manifest_before_loading_model(self):
        item, = self.items()
        used = self.job / "existing.png"
        used.write_bytes(b"keep")
        loader = Mock()
        for items in ([], [item] * 9, [item] * 2, [dict(item, output=str(used))],
                      [dict(item, output=str(self.root / "outside.png"))]):
            with self.subTest(items=items), self.assertRaises(ValueError):
                worker.run(self.write(items), self.root, loader=loader)
        loader.assert_not_called()
        self.assertEqual(used.read_bytes(), b"keep")

    def test_unknown_python_never_executes(self):
        source = self.job / "birefnet.py"
        source.write_text("raise AssertionError('must not run')", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "校验失败"):
            worker.checked_source(source)
