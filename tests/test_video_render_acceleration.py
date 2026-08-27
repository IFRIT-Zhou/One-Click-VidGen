import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import module5_video_render


class VideoRenderAccelerationTest(unittest.TestCase):
    def test_configured_render_workspace_accepts_output_scoped_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configured = Path(temporary) / "output" / "project" / "other" / ".render_runtime"
            with patch.dict(os.environ, {"TEST_RENDER_PATH": str(configured)}):
                self.assertEqual(
                    module5_video_render.configured_path("TEST_RENDER_PATH", Path("unused")),
                    configured.resolve(),
                )

    def test_windows_prefers_portable_ffmpeg_exe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            portable_dir = Path(temporary)
            portable = portable_dir / "ffmpeg.exe"
            portable.touch()
            with (
                patch.object(module5_video_render, "IS_WINDOWS", True),
                patch.object(module5_video_render, "PORTABLE_FFMPEG_DIR", portable_dir),
                patch.object(module5_video_render.shutil, "which", return_value="C:/system/ffmpeg.exe"),
            ):
                self.assertEqual(module5_video_render.find_ffmpeg_binary(), portable.resolve())

    def test_linux_uses_system_ffmpeg_when_portable_native_binary_is_missing(self) -> None:
        with (
            patch.object(module5_video_render, "IS_WINDOWS", False),
            patch.object(Path, "is_file", return_value=False),
            patch.object(module5_video_render.shutil, "which", return_value="/usr/bin/ffmpeg"),
        ):
            self.assertEqual(
                module5_video_render.find_ffmpeg_binary(),
                Path("/usr/bin/ffmpeg").resolve(),
            )

    def test_linux_hyperframes_can_use_system_chrome(self) -> None:
        with (
            patch.object(module5_video_render, "IS_WINDOWS", False),
            patch.object(Path, "is_file", return_value=False),
            patch.object(Path, "is_dir", return_value=False),
            patch.object(
                module5_video_render.shutil,
                "which",
                side_effect=lambda name: "/usr/bin/google-chrome" if name == "google-chrome" else None,
            ),
        ):
            self.assertEqual(
                module5_video_render.find_portable_hyperframes_browser(),
                Path("/usr/bin/google-chrome").resolve(),
            )

    def test_portable_subprocess_env_replaces_case_variant_path(self) -> None:
        ffmpeg = Path("C:/OCV/tools/ffmpeg/bin/ffmpeg.exe")
        environment = module5_video_render.portable_subprocess_env(
            ffmpeg,
            {"Path": "C:/Windows/System32", "OTHER": "kept"},
        )
        path_keys = [key for key in environment if key.lower() == "path"]
        self.assertEqual(path_keys, ["PATH"])
        self.assertTrue(environment["PATH"].startswith(str(ffmpeg.parent)))
        self.assertIn("C:/Windows/System32", environment["PATH"])
        self.assertEqual(environment["FFMPEG_BINARY"], str(ffmpeg))
        self.assertEqual(environment["FFMPEG_PATH"], str(ffmpeg))
        self.assertEqual(environment["OTHER"], "kept")

    def test_render_temp_directory_falls_back_to_system_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            expected = Path(temporary)
            real_mkdtemp = tempfile.mkdtemp
            failed_workspace = expected / "blocked-workspace"
            calls = []

            def make_temp(prefix, dir=None):
                calls.append(dir)
                if dir is not None:
                    raise PermissionError("workspace ACL denied")
                return real_mkdtemp(prefix=prefix, dir=str(expected))

            with (
                patch.dict(os.environ, {"OCV_RENDER_TEMP_DIR": ""}, clear=False),
                patch.object(module5_video_render, "WORKSPACE_DIR", failed_workspace),
                patch.object(
                    module5_video_render.tempfile,
                    "mkdtemp",
                    side_effect=make_temp,
                ),
            ):
                with module5_video_render.writable_render_temp_directory() as directory:
                    self.assertTrue(directory.is_dir())
                    self.assertEqual(directory.parent, expected)
                    (directory / "staged.jpg").write_bytes(b"image")
                self.assertFalse(directory.exists())
                self.assertEqual(calls, [str(failed_workspace), None])

    def test_windows_source_checkout_can_use_installed_edge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            program_files = Path(temporary)
            edge = program_files / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            edge.parent.mkdir(parents=True)
            edge.touch()
            with (
                patch.object(module5_video_render, "IS_WINDOWS", True),
                patch.object(Path, "is_dir", return_value=False),
                patch.object(module5_video_render.shutil, "which", return_value=None),
                patch.dict(
                    os.environ,
                    {
                        "PROGRAMFILES": str(program_files),
                        "PROGRAMFILES(X86)": "",
                        "LOCALAPPDATA": "",
                    },
                ),
            ):
                self.assertEqual(
                    module5_video_render.find_portable_hyperframes_browser(),
                    edge.resolve(),
                )

    def build(self) -> list[str]:
        return module5_video_render.build_render_command(
            "node",
            Path("hyperframes-cli.js"),
            Path("index.raw.html"),
            Path("output.mp4"),
        )

    def test_default_uses_auto_workers_gpu_encoding_and_auto_browser_gpu(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            command = self.build()
        self.assertEqual(command[command.index("--workers") + 1], "auto")
        self.assertIn("--gpu", command)
        self.assertNotIn("--browser-gpu", command)
        self.assertNotIn("--no-browser-gpu", command)

    def test_render_acceleration_can_be_overridden_for_compatibility(self) -> None:
        env = {
            "VIDEO_RENDER_WORKERS": "4",
            "VIDEO_RENDER_GPU_ENCODING": "0",
            "VIDEO_RENDER_BROWSER_GPU": "software",
        }
        with patch.dict(os.environ, env, clear=True):
            command = self.build()
        self.assertEqual(command[command.index("--workers") + 1], "4")
        self.assertNotIn("--gpu", command)
        self.assertIn("--no-browser-gpu", command)

    def test_worker_count_is_bounded(self) -> None:
        with patch.dict(os.environ, {"VIDEO_RENDER_WORKERS": "99"}, clear=True):
            self.assertEqual(module5_video_render.render_workers(), "16")

    def test_dual_version_subtitle_pass_uses_ffmpeg_and_optional_nvenc(self) -> None:
        command = module5_video_render.build_subtitle_burn_command(
            Path("raw.mp4"),
            Path("final_short.srt"),
            Path("subtitle.mp4"),
            use_nvenc=True,
        )
        self.assertIn("subtitles=filename=", command[command.index("-vf") + 1])
        self.assertIn("FontSize=12", command[command.index("-vf") + 1])
        self.assertEqual(command[command.index("-c:v") + 1], "h264_nvenc")
        self.assertEqual(command[command.index("-pix_fmt") + 1], "yuv420p")
        self.assertIn("-c:a", command)

    def test_dual_version_subtitle_pass_has_x264_fallback_command(self) -> None:
        command = module5_video_render.build_subtitle_burn_command(
            Path("raw.mp4"),
            Path("final_short.srt"),
            Path("subtitle.mp4"),
            use_nvenc=False,
        )
        self.assertEqual(command[command.index("-c:v") + 1], "libx264")
        self.assertEqual(command[command.index("-pix_fmt") + 1], "yuv420p")

    def test_direct_filter_preserves_timeline_starts_and_crossfade(self) -> None:
        timeline = [
            {"start": 0.0, "path": Path("one.jpg")},
            {"start": 7.0, "path": Path("two.jpg")},
            {"start": 15.0, "path": Path("three.jpg")},
        ]
        script, output_label = module5_video_render.build_direct_filter_script(
            timeline,
            24.0,
            fps=30,
            fade_duration=0.8,
        )
        self.assertIn("offset=7.000000", script)
        self.assertIn("offset=15.000000", script)
        self.assertIn("pad=1920:1080:42:54", script)
        self.assertEqual(output_label, "x2")


if __name__ == "__main__":
    unittest.main()
