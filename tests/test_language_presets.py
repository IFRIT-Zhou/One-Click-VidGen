import json
import os
import unittest
from unittest.mock import patch
from backend.app import language_presets as module


class LanguagePresetTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'OCV_LANGUAGE_PRESETS': '[]',
            'CUSTOM_LLM_API_BASE': 'https://api.example.com/v1',
            'CUSTOM_LLM_API_KEY': 'secret-test', 'CUSTOM_LLM_MODEL': 'old'}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.saved = patch.object(module, 'save_project_env_values', side_effect=lambda values: os.environ.update(values))
        self.saved.start()
        self.addCleanup(self.saved.stop)

    def test_save_and_switch_reuses_key_without_serializing_it(self):
        row = module.save_preset(module.LanguagePreset(name='Test', provider='custom', model='vendor/model'))
        self.assertNotIn('secret-test', os.environ['OCV_LANGUAGE_PRESETS'])
        self.assertEqual(os.environ['CUSTOM_LLM_MODEL'], 'old')
        module.activate_preset(row['id'])
        self.assertEqual(os.environ['CUSTOM_LLM_MODEL'], 'vendor/model')
        self.assertEqual(os.environ['CUSTOM_LLM_API_KEY'], 'secret-test')

    def test_changed_endpoint_requires_reconfirmation(self):
        row = module.save_preset(module.LanguagePreset(name='Test', provider='custom', model='vendor/model'))
        os.environ['CUSTOM_LLM_API_BASE'] = 'https://different.example/v1'
        with self.assertRaises(ValueError):
            module.activate_preset(row['id'])

    def test_edit_and_delete_preserves_credentials(self):
        row = module.save_preset(module.LanguagePreset(name='Test', provider='custom', model='first'))
        module.save_preset(module.LanguagePreset(id=row['id'], name='New', provider='custom', model='second'))
        self.assertEqual(len(module.presets()), 1)
        module.delete_preset(row['id'])
        self.assertEqual(module.presets(), [])
        self.assertEqual(os.environ['CUSTOM_LLM_API_KEY'], 'secret-test')
