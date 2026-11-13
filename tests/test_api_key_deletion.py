"""Exercise the endpoint without importing the application's startup machinery."""
import ast
from pathlib import Path
import unittest
from unittest.mock import Mock

from fastapi import HTTPException


class ApiKeyDeletionTests(unittest.TestCase):
    def endpoint(self, values):
        source = Path(__file__).resolve().parents[1] / "backend/app/main.py"
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "delete_api_key")
        node.decorator_list = []
        scope = {"Request": object, "Any": object, "HTTPException": HTTPException,
                 "require_user": Mock(), "PROJECT_ROOT": Path("unused"),
                 "_parse_env_lines": Mock(return_value=values),
                 "save_project_env_values": Mock(), "_api_key_status": Mock(return_value={})}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), scope)
        return scope

    def test_qwen_deletion_only_clears_qwen(self):
        scope = self.endpoint({"DASHSCOPE_API_KEY": "fake", "RUNNINGHUB_API_KEY": "keep"})
        scope["delete_api_key"]("qwen_tts", 0, None)
        scope["save_project_env_values"].assert_called_once_with({"DASHSCOPE_API_KEY": ""})
        scope["require_user"].assert_called_once()

    def test_missing_key_or_invalid_index_never_writes(self):
        for values, index in [({}, 0), ({"DASHSCOPE_API_KEY": "fake"}, 1)]:
            scope = self.endpoint(values)
            with self.assertRaises(HTTPException):
                scope["delete_api_key"]("qwen_tts", index, None)
            scope["save_project_env_values"].assert_not_called()
