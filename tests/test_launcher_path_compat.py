"""Windows PATH regression checks; never launch or stop OCV services."""
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
PS = SYSTEM / "WindowsPowerShell/v1.0/powershell.exe"


@unittest.skipUnless(os.name == "nt", "Windows launcher")
class LauncherPathCompatibilityTests(unittest.TestCase):
    def run_ps(self, script):
        env = dict(os.environ, PATH="")
        result = subprocess.run(
            [str(PS), "-NoProfile", "-NonInteractive", "-Command", script],
            env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_update_gate_uses_os_api_without_path(self):
        helper = str(ROOT / "launcher/safe_update_helper.ps1").replace("'", "''")
        # Load only the function AST, not the updater's executable entry point.
        self.run_ps(
            "$ErrorActionPreference='Stop';"
            f"$ast=[System.Management.Automation.Language.Parser]::ParseFile('{helper}',[ref]$null,[ref]$null);"
            "$f=$ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] "
            "-and $n.Name -eq 'Assert-OcvServicesStopped'},$true);"
            "$code=$f.Extent.Text.Replace('@(8010, 5173, 8030)','@($testPort)');"
            ". ([scriptblock]::Create($code));"
            "$listener=[System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback,0);"
            "$listener.Start();$testPort=$listener.LocalEndpoint.Port;"
            "try { $blocked=$false;try {Assert-OcvServicesStopped} catch {"
            "if($_.Exception.Message -notlike '*still listening*'){throw};$blocked=$true};"
            "if(-not $blocked){throw 'Occupied port was not blocked'}"
            "} finally {$listener.Stop()};"
            "Assert-OcvServicesStopped;"
        )

    def run_ffmpeg_check(self, root):
        source = (ROOT / "start_windows.bat").read_text()
        block = source.split("rem Prefer the bundled executable;", 1)[1]
        block = "rem Prefer the bundled executable;" + block.split('if not exist ".env"', 1)[0]
        # PAUSE must not wait in negative-path tests.
        block = block.replace("    pause", "    rem pause")
        with tempfile.TemporaryDirectory(prefix="ocv path test ") as directory:
            script = Path(directory) / "check.bat"
            script.write_text(
                '@echo off\nsetlocal EnableDelayedExpansion\n'
                f'set "ROOT_DIR={root}{os.sep}"\n'
                + block + "\necho CHECK_OK\n", encoding="utf-8",
            )
            return subprocess.run(
                [str(SYSTEM / "cmd.exe"), "/d", "/c", str(script)],
                env=dict(os.environ, PATH=""), capture_output=True, text=True, timeout=30,
            )

    def test_bundled_ffmpeg_with_empty_path(self):
        if not (ROOT / "tools/ffmpeg/bin/ffmpeg.exe").is_file():
            self.skipTest("Bundled FFmpeg not installed in source checkout")
        result = self.run_ffmpeg_check(ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CHECK_OK", result.stdout)

    def test_missing_ffmpeg_reports_actual_dependency(self):
        with tempfile.TemporaryDirectory(prefix="ocv missing ffmpeg ") as directory:
            result = self.run_ffmpeg_check(directory)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FFmpeg was not found in the portable package or PATH", result.stdout)

    def test_broken_ffmpeg_not_reported_missing(self):
        with tempfile.TemporaryDirectory(prefix="ocv broken ffmpeg ") as directory:
            target = Path(directory) / "tools/ffmpeg/bin/ffmpeg.exe"
            target.parent.mkdir(parents=True)
            # A valid Windows executable with incompatible arguments returns a
            # failure without opening Windows' invalid-executable modal dialog.
            shutil.copyfile(SYSTEM / "where.exe", target)
            result = self.run_ffmpeg_check(directory)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FFmpeg exists but could not run", result.stdout)

    def test_batch_restores_system_tools_locally(self):
        for name in ("start_windows.bat", "stop_dev.bat"):
            source = (ROOT / name).read_text()
            self.assertIn("setlocal", source)
            self.assertIn('set "PATH=%SystemRoot%\\System32;', source)
            self.assertNotIn("setx ", source.lower())

    def test_launcher_resolves_system_tools(self):
        source = (ROOT / "launcher/src/LauncherRuntime.cs").read_text()
        self.assertIn("Path.Combine(Environment.SystemDirectory, relative)", source)
        self.assertIn('info.FileName = WindowsToolPath("powershell.exe")', source)
        self.assertIn('info.FileName = WindowsToolPath("cmd.exe")', source)
        self.assertIn("info.FileName = WindowsToolPath(fileName)", source)
        self.assertNotIn("if (exitCode != 0) return result;", source)


if __name__ == "__main__":
    unittest.main()
