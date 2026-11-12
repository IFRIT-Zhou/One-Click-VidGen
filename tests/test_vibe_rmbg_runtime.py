import json
import hashlib
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.app import vibe_rmbg_runtime as runtime


class RmbgRuntimeTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.model = self.root/'tools/rmbg/models/BiRefNet'
        self.model.mkdir(parents=True)
        self.probe = self.enter_patch('_probe')
        self.verify = self.enter_patch('verify_models')
        self.worker = self.root/'tools/rmbg/worker.py'
        self.worker.touch()
        self.spec={'install':{'python':'3.10','packages':{'torch':'2.8'}},'also_compatible':[]}
        (self.worker.parent/'runtime-spec.json').write_text(json.dumps(self.spec),encoding='utf-8')
        self.versions=self.enter_patch('_runtime_versions')
        self.versions.return_value=self.spec['install']
        self.weight_call = {runtime._WEIGHT:runtime.MODEL_FILES[runtime._WEIGHT]}

    def enter_patch(self, name):
        p = patch.object(runtime, name)
        self.addCleanup(p.stop)
        return p.start()

    def python(self, directory):
        p = directory / ('python.exe' if os.name == 'nt' else 'bin/python')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        return p.resolve()

    def component(self, version):
        base = self.root/'comfyui/engine'
        base.mkdir(parents=True, exist_ok=True)
        (base/'active.json').write_text(json.dumps({'version': version}), encoding='utf-8')
        return base

    def test_normal_install_needs_no_comfyui(self):
        py = self.python(self.root/'tools/rmbg/python')
        r = runtime.resolve_runtime(self.root)
        self.assertEqual((r.python, r.model_dir, r.origin), (py, self.model.resolve(), 'standalone'))
        self.assertEqual(r.worker,self.worker)
        self.verify.assert_called_with(self.model,files=self.weight_call)

    def test_versioned_launcher_runtime_precedes_legacy_python(self):
        base = self.root/'runtime/rmbg'
        version='rmbg-aabbccddeeff-112233445566'
        self.python(self.root/'tools/rmbg/python')
        interp=base/'versions'/version/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        interp.parent.mkdir(parents=True)
        interp.touch()
        for name in ('../outside',version):
            (base/'active.json').write_text(json.dumps({'version':name}),encoding='utf-8')
            r=runtime.resolve_runtime(self.root)
            if name==version:
                self.assertEqual((r.origin,r.python),('launcher-managed',interp))
            else:
                self.assertEqual(r.origin,'standalone')

    def test_legacy_manual_model_and_interpreter_are_still_supported(self):
        self.model.rmdir()
        legacy=self.root/'models/rmbg/BiRefNet'
        legacy.mkdir(parents=True)
        py=self.python(self.root/'runtime/rmbg/python')
        r=runtime.resolve_runtime(self.root)
        self.assertEqual((r.python,r.model_dir,r.origin),(py,legacy,'legacy-python'))

    def test_shared_site_packages_environment_is_rejected(self):
        version='rmbg-aabbccddeeff-112233445566'
        base=self.root/'runtime/rmbg'
        folder=base/'versions'/version
        interp=folder/'Scripts/python.exe'
        interp.parent.mkdir(parents=True)
        interp.touch()
        (folder/'pyvenv.cfg').write_text('include-system-site-packages = true\n', encoding='utf-8')
        (base/'active.json').write_text(json.dumps({'version':version}), encoding='utf-8')
        with self.assertRaisesRegex(runtime.RmbgRuntimeError, '共享依赖'):
            runtime.ready_interpreter(self.root, self.spec)
        self.probe.assert_not_called()

    def test_old_model_and_component_interpreter_no_running_service_needed(self):
        self.model.rmdir()
        legacy = self.root/'workspace/vibe_rmbg_trial/models/RMBG/BiRefNet'
        legacy.mkdir(parents=True)
        self.python(self.root/'runtime/python')
        py = self.python(self.component('v1')/'releases/v1/python')
        with self.assertRaises(runtime.RmbgRuntimeError):
            runtime.resolve_runtime(self.root)
        self.probe.assert_not_called()

    def test_invalid_component_pointer_is_never_executed(self):
        self.component('../elsewhere')
        self.python(self.root/'comfyui/engine/elsewhere/python')
        with self.assertRaisesRegex(runtime.RmbgRuntimeError,'未找到随包 Python'):
            runtime.resolve_runtime(self.root)
        self.probe.assert_not_called()

    def test_stale_version_even_if_importable_never_shadows_new_candidate(self):
        stale=self.python(self.root/'tools/rmbg/python')
        correct=self.python(self.root/'runtime/rmbg/python')
        self.versions.side_effect=[{'python':'3.10','packages':{'torch':'2.7'}},self.spec['install']]
        r=runtime.resolve_runtime(self.root)
        self.assertEqual(r.python,correct)
        self.assertEqual(self.versions.call_args_list[0].args[0],stale)
        self.probe.assert_called_once_with(correct, correct.stat().st_mtime_ns)

    def test_incomplete_install_does_not_silently_use_trial(self):
        (self.root/'workspace/vibe_rmbg_trial/models/RMBG/BiRefNet').mkdir(parents=True)
        self.verify.side_effect = [None,runtime.RmbgModelError('本地模型文件校验失败')]
        with self.assertRaisesRegex(runtime.RmbgRuntimeError,'请修复'):
            runtime.resolve_runtime(self.root)
        self.verify.assert_called_with(self.model,files=self.weight_call)
        self.probe.assert_not_called()

    def test_missing_weights_and_dependencies_have_actionable_messages(self):
        self.model.rmdir()
        with self.assertRaisesRegex(runtime.RmbgRuntimeError, 'tools/rmbg/models/BiRefNet'):
            runtime.resolve_runtime(self.root)
        self.model.mkdir()
        self.python(self.root/'tools/rmbg/python')
        self.probe.side_effect=runtime.RmbgRuntimeError('缺少：timm')
        with self.assertRaisesRegex(runtime.RmbgRuntimeError,'无需启动 ComfyUI.*缺少：timm'):
            runtime.resolve_runtime(self.root)

    def test_model_only_preflight_needs_no_runtime_or_engine(self):
        self.assertEqual(runtime.resolve_model(self.root), self.model.resolve())
        self.verify.assert_called_once_with(self.model,files=self.weight_call)
        self.probe.assert_not_called()


class PluginIntegrityTests(unittest.TestCase):
    def test_model_folder_contributes_only_weights(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            folder=root/'tools/rmbg/models/BiRefNet'
            folder.mkdir(parents=True)
            weight=b'tiny fixture'
            (folder/runtime._WEIGHT).write_bytes(weight)
            # Legacy hand-installed packages can contain untrusted Python. It
            # must not take part in lookup, validation, or model construction.
            (folder/'birefnet.py').write_text('raise RuntimeError("must not run")',encoding='utf-8')
            with patch.dict(runtime.MODEL_FILES,{runtime._WEIGHT:(len(weight),hashlib.sha256(weight).hexdigest())}):
                self.assertEqual(runtime.resolve_model(root),folder)

    def test_vendor_code_is_validated_separately_from_weights(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            plugin=root/'tools/rmbg'
            src=runtime.ROOT/'tools/rmbg'
            shutil.copytree(src/'vendor',plugin/'vendor')
            shutil.copy2(src/'worker.py',plugin)
            self.assertEqual(runtime.resolve_program(root),plugin/'worker.py')
            code=plugin/'vendor/birefnet/birefnet.py'
            code.write_text('raise AssertionError("unverified code")',encoding='utf-8')
            with self.assertRaisesRegex(runtime.RmbgRuntimeError,'birefnet.py.*Launcher'):
                runtime.resolve_program(root)
