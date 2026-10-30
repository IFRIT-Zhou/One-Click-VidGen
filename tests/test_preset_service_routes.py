import ast
import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import Mock

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator


class PresetRoutesTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / 'backend/app/main.py'
        tree = ast.parse(path.read_text('utf-8'))
        names = {'GenerateRequest', 'ParameterPresetRequest', 'save_parameter_preset'}
        nodes = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in names]
        for node in nodes:
            if isinstance(node, ast.FunctionDef):
                node.decorator_list = []
        self.output = Mock()
        self.scope = dict(BaseModel=BaseModel, Field=Field, field_validator=field_validator,
                          Any=Any, Literal=Literal, DEFAULT_QWEN_VOICE='Elias',
                          ImageSize=str, BgmTrackRequest=BaseModel, Request=object,
                          HTTPException=HTTPException, json=json, time=time,
                          require_user=lambda r: {'id':1}, normalize_project_name=lambda n:n,
                          _parameter_preset_path=lambda *a:self.output)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), self.scope)

    def save(self, params):
        payload = self.scope['ParameterPresetRequest'](name='test', parameters=params)
        before = dict(payload.parameters)
        self.scope['save_parameter_preset'](payload, None)
        self.assertEqual(payload.parameters, before)
        return json.loads(self.output.write_text.call_args.args[0])['parameters']

    def test_old_client_null_and_missing_keep_inheritance(self):
        for pool in (True, False):
            for route in ({}, {'language_route':None}):
                saved = self.save({'use_cloud_image_pool':pool, **route})
                self.assertEqual(saved['language_route'], 'project')
                self.assertEqual(saved['use_cloud_image_pool'], pool)

    def test_explicit_routes_preserved(self):
        for route in ('project', 'cloud', 'personal'):
            self.assertEqual(self.save({'language_route':route})['language_route'], route)

    def test_invalid_route_still_rejected(self):
        with self.assertRaises(HTTPException) as error:
            self.save({'language_route':'invalid'})
        self.assertEqual(error.exception.status_code, 422)
        self.output.write_text.assert_not_called()


if __name__ == '__main__':
    unittest.main()
