from pathlib import Path
import re
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from release_integrity import (  # noqa: E402
    INTEGRITY_FILES, TEXT_EXTENSIONS, release_file_bytes, validate_launcher_integrity_files,
)


class ReleaseIntegrityTests(unittest.TestCase):
    def test_launcher_and_release_tool_hash_the_same_files_in_the_same_order(self) -> None:
        validate_launcher_integrity_files(ROOT)

    def test_mismatched_launcher_list_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "launcher" / "src" / "LauncherRuntime.cs"
            source.parent.mkdir(parents=True)
            source.write_text(
                'private static readonly string[] ReleaseIntegrityFiles = { "OCV_Launcher.exe" };',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "清单不一致"):
                validate_launcher_integrity_files(root)

    def test_first_file_is_launcher_executable(self) -> None:
        self.assertEqual(INTEGRITY_FILES[0], "OCV_Launcher.exe")

    def test_launcher_and_packager_normalize_the_same_text_extensions(self) -> None:
        source = (ROOT / "launcher/src/LauncherRuntime.cs").read_text(encoding="utf-8-sig")
        launcher_extensions = set(re.findall(r'extension == "([^"]+)"', source))
        self.assertEqual(launcher_extensions, TEXT_EXTENSIONS)

    def test_requirements_fingerprint_ignores_git_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mcp-requirements.txt"
            expected = b"mcp>=1.0\nhttpx>=0.27\n"
            for data in (expected, expected.replace(b"\n", b"\r\n"), b"\xef\xbb\xbf" + expected):
                with self.subTest(data=data):
                    path.write_bytes(data)
                    self.assertEqual(release_file_bytes(path), expected)


if __name__ == "__main__":
    unittest.main()
