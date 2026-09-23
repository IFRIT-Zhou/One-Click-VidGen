from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from release_integrity import INTEGRITY_FILES, validate_launcher_integrity_files  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
