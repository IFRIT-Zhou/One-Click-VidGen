"""Exercise the opt-in RMBG dependency updater without internet or model weights."""
from contextlib import ExitStack, nullcontext
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools.rmbg import setup_runtime as setup


class Setup(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.enter = stack.enter_context
        self.root = Path(self.enter(tempfile.TemporaryDirectory())).resolve()
        self.target = {'python': '3.10', 'packages': {'torch': '2.8', 'torchvision': '0.23'}}
        self.spec = {'install': self.target, 'also_compatible': []}

    def test_check_uses_production_resolution_no_weight_requirement(self):
        resolver=self.enter(patch.object(setup, 'ready_interpreter',side_effect=setup.RmbgRuntimeError('missing')))
        self.assertIsNone(setup.compatible(self.root, self.spec))
        resolver.assert_called_once_with(self.root, self.spec)
        resolver.side_effect=None; resolver.return_value=('launcher-managed',self.root/'python.exe')
        self.assertEqual(setup.compatible(self.root, self.spec), 'launcher-managed')

    def test_pointer_changes_only_after_success_and_failure_keeps_old_runtime(self):
        base=self.root/'runtime/rmbg'
        base.mkdir(parents=True)
        active=base/'active.json'; before='{"version":"old"}'; active.write_text(before)
        self.enter(patch.object(setup, 'compatible', return_value=None))
        self.enter(patch.object(setup, 'install_lock', return_value=nullcontext()))
        installer = self.enter(patch.object(setup, 'install_environment', side_effect=RuntimeError('bad wheel')))
        with self.assertRaisesRegex(RuntimeError, 'bad wheel'):
            setup.install(self.root, self.spec)
        self.assertEqual(active.read_text(), before)
        self.assertEqual(list((base/'versions').iterdir()), [])
        installer.side_effect=None
        setup.install(self.root, self.spec)
        version=json.loads(active.read_text())['version']
        self.assertRegex(version, r'^rmbg-[0-9a-f]{12}-[0-9a-f]{12}$')
        self.assertTrue((base/'versions'/version).is_dir())

    @unittest.skipUnless(os.name=='nt', 'Only the Windows Launcher maintains Windows venvs')
    def test_clean_install_using_only_tiny_local_fixture_wheels(self):
        # This specifically tests the shipped Python distribution supporting a
        # pip-less target venv, a fake offline index and fixed versions. No torch import.
        base=Path(sys.executable)
        target={'python':'%d.%d'%sys.version_info[:2],
                'packages': {'torch':'0.0.101', 'torchvision':'0.0.102'}}
        index=self.root/'fixture-index'
        for name,ver in target['packages'].items():
            sub=index/name; sub.mkdir(parents=True)
            wheel=sub/f'{name}-{ver}-py3-none-any.whl'
            dist=f'{name}-{ver}.dist-info'
            with zipfile.ZipFile(wheel, 'w') as z:
                z.writestr(name+'/__init__.py', '')
                z.writestr(dist+'/METADATA', f'Metadata-Version: 2.1\nName: {name}\nVersion: {ver}\n')
                z.writestr(dist+'/WHEEL','Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
                z.writestr(dist+'/RECORD','')
            (sub/'index.html').write_text(f'<a href="{wheel.name}">{wheel.name}</a>')
        self.enter(patch.object(setup, 'TORCH_INDEX', index.as_uri()))
        self.enter(patch.object(setup, 'PYPI', index.as_uri()))
        self.enter(patch.object(setup, '_probe'))
        current=setup.installed_versions(base, sorted(target['packages']))
        dest=self.root/'test-venv'
        setup.install_environment(self.root,dest,target,base=base)
        self.assertIn('include-system-site-packages = false', (dest/'pyvenv.cfg').read_text().lower())
        self.assertEqual(setup.installed_versions(dest/'Scripts/python.exe',sorted(target['packages'])),target)
        self.assertEqual(setup.installed_versions(base, sorted(target['packages'])),current)
