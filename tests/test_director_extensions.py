import json
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from backend.app import director_extensions as ext
from backend.app.video_medical_director import medical_contract
from tools.create_portable_archive import is_excluded


class DirectorExtensionTests(unittest.TestCase):
    def fixture(self, root, name='private_test'):
        folder = root / name
        folder.mkdir()
        (folder / 'plugin.json').write_text(json.dumps(dict(manifest_version=1,type='director_profile',entry='director.json')))
        (folder / 'director.json').write_text(json.dumps(dict(schema_version=1,id='medical_paper',label='Private',
            description='Optional test',common='CONTRACT',stages={'core':'CORE'},design_fields=[],core_example={},motion_example={})))
        return folder

    def test_absent_and_disabled_do_not_advertise_or_silently_plan(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ext,'PLUGINS_DIR',Path(tmp)):
            self.assertEqual(ext.profiles(),{})
            self.assertEqual(medical_contract({},'core'),'')
            with self.assertRaisesRegex(ValueError,'插件'):
                medical_contract({'video_direction':{'dynamic_text_mode':'medical_paper'}},'core')
            folder = self.fixture(Path(tmp))
            self.assertEqual(medical_contract({'video_direction':{'dynamic_text_mode':'medical_paper'}},'core'),'CONTRACTCORE')
            (folder / 'disabled').touch()
            self.assertEqual(ext.profiles(),{})

    def test_traversal_and_executable_entry_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = self.fixture(root)
            for entry in ('../outside.json','entry.py'):
                (folder / 'plugin.json').write_text(json.dumps(dict(manifest_version=1,type='director_profile',entry=entry)))
                self.assertEqual(ext.profiles(root),{})

    def test_duplicate_profiles_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fixture(Path(tmp),'private_a')
            self.fixture(Path(tmp),'private_b')
            self.assertEqual(ext.profiles(tmp),{})

    def test_portable_excludes_private_plugin_and_keeps_public(self):
        self.assertTrue(is_excluded(Path('plugins/private_medical_director/director.json')))
        self.assertFalse(is_excluded(Path('plugins/example_plugin/plugin.json')))

    def test_publisher_refuses_tracked_private_content_before_push(self):
        tools_path = str(Path(__file__).resolve().parents[1] / 'tools')
        with patch.object(sys,'path',[tools_path]+sys.path):
            import publish_launcher_release as release
            with patch.object(release,'run',return_value='plugins/private_medical_director/director.json\n') as command:
                with self.assertRaisesRegex(RuntimeError,'私有插件'):
                    release.sync_github()
                self.assertEqual(command.call_count,1)
                self.assertEqual(command.call_args.args[:2],('git','ls-tree'))

if __name__ == '__main__':
    unittest.main()
