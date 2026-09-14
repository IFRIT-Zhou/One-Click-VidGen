import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app import local_tts_component


class LocalTtsComponentTest(unittest.TestCase):
    def tearDown(self) -> None:
        local_tts_component._PROCESS = None
        if local_tts_component._LOG_HANDLE is not None:
            local_tts_component._LOG_HANDLE.close()
        local_tts_component._LOG_HANDLE = None
        local_tts_component._LAST_EXIT_CODE = None

    def test_missing_models_are_optional_when_runtime_is_complete(self) -> None:
        config = Mock()
        config.ready = False
        config.missing_runtime_resources.return_value = []
        config.missing_model_resources.return_value = ["gpt.pth"]
        with (
            patch.object(local_tts_component, "load_indextts25_config", return_value=config),
            patch.object(local_tts_component, "_model_bytes", return_value=0),
        ):
            status = local_tts_component.component_status()
        self.assertEqual(status["state"], "not_installed")
        self.assertTrue(status["optional"])

    def test_start_install_uses_fixed_script_without_shell(self) -> None:
        config = Mock()
        config.ready = False
        config.missing_runtime_resources.return_value = []
        config.missing_model_resources.return_value = ["gpt.pth"]
        process = Mock()
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(local_tts_component, "PROJECT_ROOT", Path(directory)),
                patch.object(local_tts_component, "INSTALL_SCRIPT", Path(directory) / "install.ps1"),
                patch.object(local_tts_component, "LOG_PATH", Path(directory) / "install.log"),
                patch.object(local_tts_component, "load_indextts25_config", return_value=config),
                patch.object(local_tts_component, "_model_bytes", return_value=0),
                patch.object(local_tts_component.os, "name", "nt"),
                patch.object(local_tts_component.subprocess, "Popen", return_value=process) as popen,
            ):
                local_tts_component.INSTALL_SCRIPT.write_text("test", encoding="utf-8")
                status = local_tts_component.start_install()
                local_tts_component._PROCESS = None
                local_tts_component._LOG_HANDLE.close()
                local_tts_component._LOG_HANDLE = None
        self.assertTrue(status["installing"])
        self.assertNotIn("shell", popen.call_args.kwargs)
        self.assertEqual(popen.call_args.args[0][0], "powershell.exe")


if __name__ == "__main__":
    unittest.main()
