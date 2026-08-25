import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.app import editor


class EditorUploadTests(unittest.TestCase):
    def test_upload_rejects_oversized_image_and_removes_partial_file(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                stream = AsyncMock()
                stream.filename = "reference.png"
                stream.read = AsyncMock(side_effect=[b"x" * (30 * 1024 * 1024), b"y"])
                with patch.object(editor, "EDITOR_DIR", root):
                    with self.assertRaisesRegex(ValueError, "image文件不能超过 30 MB"):
                        await editor.save_upload(1, stream)
                    self.assertEqual(list((root / "user_1" / "uploads").iterdir()), [])

        asyncio.run(run())

    def test_upload_limit_can_be_raised_for_local_video_workflow(self) -> None:
        with patch.dict(editor.os.environ, {"EDITOR_UPLOAD_MAX_BYTES": str(1024 * 1024 * 1024)}):
            self.assertEqual(editor.editor_upload_limit("video"), 1024 * 1024 * 1024)
            self.assertEqual(editor.editor_upload_limit("image"), 30 * 1024 * 1024)
