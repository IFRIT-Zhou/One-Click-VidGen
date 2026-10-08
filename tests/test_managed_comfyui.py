import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app import managed_comfyui as engine
from backend.app import comfyui_bridge as bridge


class ManagedComfyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for module, key, value in (
            (engine, "PROJECT", self.root), (engine, "LOADED_NODES", None),
            (engine, "RUNTIME", self.root / "runtime"), (engine, "DATA", self.root / "data"),
            (bridge, "ROOT", self.root / "bridge"), (engine, "PROCESS", None),
            (engine, "LOG_HANDLE", None), (engine, "STATE", "stopped"),
            (engine, "PORT", 0), (engine, "LEASES", 0), (engine, "ERROR", ""),
        ):
            patcher = patch.object(module, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.release = engine.RUNTIME / "releases" / "test1"
        for path in (self.release / "python" / "python.exe", self.release / "ComfyUI" / "main.py"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        engine.write_json(engine.RUNTIME / "active.json", {"version": "test1"})
        engine.write_json(self.release / "component.json", {"version": "test1", "models": [{"path": "vae/test.bin", "bytes": 3}]})

    def test_user_nodes_registered_as_extra_path(self):
        engine._extra_paths()
        config = engine.read_json(engine.DATA / 'model_paths.yaml', {})
        self.assertEqual(config['ocv_user_nodes']['custom_nodes'], str((self.root / 'comfyui_plugins').resolve()))
        self.assertTrue((self.root / 'comfyui_plugins').is_dir())

    def test_missing_optional_node_only_blocks_its_workflow(self):
        with patch.object(engine, 'ensure_ready', return_value='http://127.0.0.1:8199'), patch.object(engine, 'LOADED_NODES', {'FastSampler'}):
            with engine.execution_lease(required_nodes={'FastSampler'}) as url:
                self.assertEqual(url, 'http://127.0.0.1:8199')
            with self.assertRaisesRegex(RuntimeError, 'SelfLiftH3Sampler'):
                with engine.execution_lease(required_nodes={'SelfLiftH3Sampler'}):
                    self.fail('missing nodes must fail before submitting')
        self.assertEqual(engine.LEASES, 0)

    def test_absent_component_does_not_start(self):
        (engine.RUNTIME / "active.json").unlink()
        self.assertFalse(engine.status()["installed"])
        with self.assertRaisesRegex(RuntimeError, "尚未安装"):
            engine.ensure_ready()

    def test_reject_path_escape(self):
        engine.write_json(engine.RUNTIME / "active.json", {"version": "../../external"})
        self.assertEqual(engine.runtime_info(), (None, {}))

    def test_override_hash_prevents_silent_loss_of_memory_patch(self):
        import hashlib
        target = self.release / "ComfyUI/model.py"
        target.write_bytes(b"reviewed patch")
        manifest = {"overrides": [{"path": "model.py", "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}]}
        engine.verify_core_overrides(self.release, manifest)
        target.write_bytes(b"upstream overwrote patch")
        with self.assertRaisesRegex(RuntimeError, "被修改或覆盖"):
            engine.verify_core_overrides(self.release, manifest)
        manifest["overrides"][0]["path"] = "../component.json"
        with self.assertRaisesRegex(RuntimeError, "路径异常"):
            engine.verify_core_overrides(self.release, manifest)

    def test_selected_workflow_does_not_require_other_workflow_models(self):
        manifest = engine.read_json(self.release / "component.json", {})
        manifest["models"].append({"path": "vae/other.bin", "bytes": 4})
        engine.write_json(self.release / "component.json", manifest)
        model = engine.RUNTIME / "models/vae/test.bin"
        model.parent.mkdir(parents=True)
        model.write_bytes(b"123")
        with patch.object(engine, "STATE", "ready"), patch.object(engine, "PORT", 8199):
            self.assertEqual(engine.ensure_ready(required_models=["vae/test.bin"]), "http://127.0.0.1:8199")
            with self.assertRaisesRegex(RuntimeError, "other.bin"):
                engine.ensure_ready(required_models=["vae/other.bin"])

    def test_model_reuse_and_size_check(self):
        models = self.root / "external_models"
        (models / "vae").mkdir(parents=True)
        model = models / "vae" / "test.bin"
        model.write_bytes(b"123")
        engine.write_json(engine.DATA / "settings.json", {"model_directory": str(models)})
        self.assertTrue(engine.status()["models_ready"])
        model.write_bytes(b"12")
        self.assertFalse(engine.status()["models_ready"])
        with self.assertRaisesRegex(RuntimeError, "模型缺失"):
            engine.ensure_ready()

    def test_stop_never_kills_while_preparing(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(engine, "PROCESS", process), patch.object(engine, "LEASES", 1):
            with self.assertRaisesRegex(RuntimeError, "正在准备"):
                engine.stop()
        process.terminate.assert_not_called()

    def test_failed_start_releases_lease(self):
        with patch.object(engine, "ensure_ready", side_effect=RuntimeError("start failed")):
            with self.assertRaises(RuntimeError):
                with engine.execution_lease():
                    pass
        self.assertEqual(engine.LEASES, 0)

    def test_external_connection_does_not_touch_engine(self):
        with patch.object(engine, "ensure_ready", side_effect=AssertionError("must not start")) as start:
            bridge._write_json(bridge.ROOT / "1" / "connection.json", {"base_url": "http://example.test:8188"})
            self.assertEqual(bridge._connection(1)["base_url"], "http://example.test:8188")
            start.assert_not_called()

    def test_execution_captures_route_preserves_external_address(self):
        bridge._write_json(bridge.ROOT / "1" / "connection.json", {"mode": "managed", "base_url": "http://external:8188"})

        @bridge._managed_execution
        def work(user_id, profile_id=""):
            self.assertEqual(engine.LEASES, 1)
            return bridge._connection(user_id)["base_url"]

        with patch.object(engine, "ensure_ready", return_value="http://127.0.0.1:8199"), patch.object(bridge, "video_profile", return_value={"engine": "managed"}):
            self.assertEqual(work(1, profile_id="builtin"), "http://127.0.0.1:8199")
        self.assertEqual(bridge._saved_connection(1)["base_url"], "http://external:8188")
        self.assertEqual(engine.LEASES, 0)

    def test_workbench_start_failure_is_saved(self):
        bridge._write_json(bridge.ROOT / "1" / "connection.json", {"mode": "managed"})
        with patch.object(engine, "ensure_ready", side_effect=RuntimeError("模型缺失")):
            bridge._execute(1, "a" * 32, {"engine": "managed"}, None, None, {})
        record = bridge._read_json(bridge._job_path(1, "a" * 32) / "record.json", {})
        self.assertEqual(record["status"], "failed")
        self.assertIn("模型缺失", record["message"])

    def test_dead_managed_engine_does_not_poll_for_hours(self):
        with patch.object(engine, "status", return_value={"state": "failed", "error": "引擎崩溃"}), patch.object(bridge.requests, "get") as get:
            with self.assertRaisesRegex(RuntimeError, "引擎崩溃"):
                bridge._history_payload({"mode": "managed"}, "old-prompt")
            get.assert_not_called()

    def test_external_profile_stays_external_when_workbench_uses_managed(self):
        bridge._write_json(bridge.ROOT / "1" / "connection.json", {"mode": "managed", "base_url": "http://external:8188"})

        @bridge._managed_execution
        def work(user_id, profile_id):
            return bridge._connection(user_id)

        with patch.object(bridge, "video_profile", return_value={}), patch.object(engine, "ensure_ready") as start:
            value = work(1, "old-external-profile")
        self.assertEqual(value["base_url"], "http://external:8188")
        self.assertEqual(value["mode"], "external")
        start.assert_not_called()

    def test_builtin_tracks_component_update_instead_of_stale_user_copy(self):
        engine.write_json(self.release / "profile.json", {"id": "builtin", "name": "new workflow", "workflow": {"1": {"class_type": "NewNode", "inputs": {}}}})
        bridge._write_json(bridge.ROOT / "1" / "profiles.json", [
            {"id": "builtin", "name": "old workflow", "workflow": {}},
            {"id": "external", "name": "my workflow", "engine": "external", "workflow": {}},
        ])
        with patch.object(engine, "maintenance_enabled", return_value=False):
            profiles = bridge._profiles(1)
        self.assertEqual(profiles[0]["name"], "new workflow")
        self.assertEqual(profiles[0]["component_version"], "test1")
        self.assertTrue(profiles[0]["managed_builtin"])
        self.assertEqual(profiles[1]["name"], "my workflow")

    def test_builtin_cannot_be_overwritten_or_deleted_by_regular_user(self):
        from fastapi import HTTPException
        engine.write_json(self.release / "profile.json", {"id": "builtin", "workflow": {}})
        payload = bridge.ProfileRequest(id="builtin", name="modified", engine="managed", workflow={})
        with patch.object(engine, "maintenance_enabled", return_value=False), patch.object(bridge, "require_user", return_value={"id": 1}):
            with self.assertRaises(HTTPException) as save:
                bridge.save_profile(payload, None)
            self.assertEqual(save.exception.status_code, 403)
            with self.assertRaises(HTTPException) as delete:
                bridge.delete_profile("builtin", None)
            self.assertEqual(delete.exception.status_code, 403)

    def test_multiple_builtin_profiles_keep_legacy_id_and_external_profiles(self):
        manifest = engine.read_json(self.release / "component.json", {})
        manifest["profiles"] = "profiles.json"
        engine.write_json(self.release / "component.json", manifest)
        engine.write_json(self.release / "profiles.json", [
            {"id": "ocv-h3-managed-v1", "name": "fast", "workflow": {}},
            {"id": "ocv-h3-aiwood-v1", "name": "aiwood", "workflow": {}},
        ])
        bridge._write_json(bridge.ROOT / "1" / "profiles.json", [
            {"id": "ocv-h3-aiwood-v1", "name": "stale", "engine": "external"},
            {"id": "external", "name": "user workflow", "engine": "external"},
        ])
        with patch.object(engine, "maintenance_enabled", return_value=False):
            profiles = bridge._profiles(1)
        self.assertEqual([p["name"] for p in profiles], ["fast", "aiwood", "user workflow"])
        self.assertEqual(engine.builtin_profile()["id"], "ocv-h3-managed-v1")
        self.assertEqual(profiles[1]["engine"], "managed")
        from fastapi import HTTPException
        with patch.object(engine, "maintenance_enabled", return_value=False), patch.object(bridge, "require_user", return_value={"id": 1}):
            for profile_id in ("ocv-h3-managed-v1", "ocv-h3-aiwood-v1"):
                with self.assertRaises(HTTPException) as error:
                    bridge.save_profile(bridge.ProfileRequest(id=profile_id, name="override", engine="external", workflow={}), None)
                self.assertEqual(error.exception.status_code, 403)
                with self.assertRaises(HTTPException):
                    bridge.delete_profile(profile_id, None)


if __name__ == "__main__":
    unittest.main()
