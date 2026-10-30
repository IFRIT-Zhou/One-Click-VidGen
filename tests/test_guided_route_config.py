import ast
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


class GuidedRouteConfigTests(unittest.TestCase):
    def test_configuration_validation_matches_project_routes(self):
        source = Path(__file__).parents[1] / 'backend/app/main.py'
        tree = ast.parse(source.read_text('utf-8'))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_required_job_config_error')
        module = ast.Module(body=[fn], type_ignores=[])
        routing = ModuleType('backend.app.language_routing')
        routing.uses_cloud_language = lambda d: d.get('language_route') == 'cloud' or (d.get('language_route') != 'personal' and bool(d.get('use_cloud_image_pool')))
        scope = {'__package__': 'backend.app', 'Any': object, '_api_key_status': lambda: {'language': {'configured': False}, 'image': {'configured': False}}}
        exec(compile(module, str(source), 'exec'), scope)
        validate = scope['_required_job_config_error']
        with patch.dict('sys.modules', {'backend.app.language_routing': routing}):
            self.assertIn('个人语言模型', validate({'language_route':'personal','use_cloud_image_pool':False}))
            self.assertIsNone(validate({'language_route':'cloud','use_cloud_image_pool':True}))
            self.assertIn('个人图像', validate({'language_route':'cloud','use_cloud_image_pool':False}))
            self.assertIn('个人语言模型', validate({'language_route':'personal','use_cloud_image_pool':True}))
            self.assertIsNone(validate({'language_route':'personal','step_mode':True}))


if __name__ == '__main__':
    unittest.main()
