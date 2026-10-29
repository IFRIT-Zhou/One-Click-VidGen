"""AI onboarding stays discoverable, generic, bounded and tied to real operations."""
import re
import tempfile
import unittest
from pathlib import Path

from plugins.codex_bridge.ocv_mcp.catalog import OPERATIONS
from plugins.codex_bridge.ocv_mcp.guidance import BOOTSTRAP, GUIDE_PATH, TOPICS, discovery, read_guide


class AiGuideTests(unittest.TestCase):
    def test_single_source_chapters_and_discovery(self):
        document = read_guide('all')
        self.assertEqual(document['content'], GUIDE_PATH.read_text(encoding='utf-8-sig'))
        self.assertEqual([row['topic'] for row in document['topics']], list(TOPICS))
        self.assertEqual(discovery()['topics'], list(TOPICS))
        for name in TOPICS:
            chapter = read_guide(name)
            self.assertTrue(chapter['content'].startswith('## ' + name + ' · '))
            self.assertEqual(len(re.findall(r'^## ', chapter['content'], re.M)), 1)
            self.assertIn(chapter['content'], document['content'])
            self.assertEqual(chapter['guide_version'], discovery()['guide_version'])
        self.assertLess(len(BOOTSTRAP), 1100)
        self.assertLess(len(read_guide()['content']), 2200)
        self.assertIn('ocv_guide', BOOTSTRAP)

    def test_document_names_only_real_operations_or_tools(self):
        tools = {'ocv_guide', 'ocv_connection', 'ocv_upload_preflight', 'ocv_operations', 'ocv_describe',
                 'ocv_read', 'ocv_prepare', 'ocv_commit', 'ocv_receipt', 'ocv_discard',
                 'ocv_result', 'ocv_preview_asset', 'ocv_bridge'}
        names = set(re.findall(r'\bocv_[a-z]+(?:_[a-z]+)*\b', read_guide('all')['content']))
        self.assertFalse(names - ({op.name for op in OPERATIONS} | tools), names)

    def test_no_project_data_or_local_install_assumptions(self):
        text = read_guide('all')['content']
        self.assertNotRegex(text, r'[A-Z]:[\\/]')
        for private in ('西南医院', 'wxid_', 'swn-gastric', 'ms-876', 'ocv-medical-production'):
            self.assertNotIn(private, text)
        for rule in ('不是执行授权', '不绕过', '最终已提交', 'unknown', '不能把它当作绕过 MCP 权限限制'):
            self.assertIn(rule, text)

    def test_unknown_topics_never_resolve_to_paths(self):
        for topic in ('../cookies.txt', '../../.env', '/start', '', 'START'):
            with self.assertRaisesRegex(ValueError, '未知指南章节'):
                read_guide(topic)

    def test_missing_or_partial_distribution_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'guide.md'
            with self.assertRaises(FileNotFoundError):
                read_guide(path=path)
            path.write_text('# only a heading\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '章节不完整'):
                read_guide(path=path)
