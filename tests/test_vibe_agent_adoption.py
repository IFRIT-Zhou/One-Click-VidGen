"""The autonomous Vibe agent cannot bypass explicit preview adoption QA.

Offline guard/route tests: temporary synthetic data, no provider, renderer,
production project, or application process is used.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from backend.app import vibe_adoption as adoption


VERSION = 'tool-agent-layered-v4'


class VibeAgentAdoptionTests(unittest.TestCase):
    def receipt(self, **fields):
        return dict(workflow_version=VERSION, engine='cloud',
                    renderer='ocv-remotion-code-v1', **fields)

    def test_missing_pending_and_conflicting_quality_fail_closed(self):
        variants = [
            {},
            {'quality_status': None},
            {'quality_status': 'pending'},
            {'quality_status': 'needs_review'},
            {'quality_status': 'unverified'},
            {'metrics': {'quality_status': 'needs_review'}},
            {'quality_status': 'passed', 'metrics': {'quality_status': 'needs_review'}},
            {'quality_status': 'needs_review', 'metrics': {'quality_status': 'passed'}},
            {'quality_status': 'passed', 'metrics': {'quality_status': 'pending'}},
            {'quality_status': 'pending', 'metrics': {'quality_status': 'passed'}},
            {'metrics': None},
            {'metrics': []},
            {'metrics': 'passed'},
        ]
        for fields in variants:
            with self.subTest(fields=fields):
                receipt = self.receipt(**fields)
                before = copy.deepcopy(receipt)
                with self.assertRaises(HTTPException) as raised:
                    adoption._require_quality_passed(receipt)
                self.assertEqual(raised.exception.status_code, 409)
                self.assertIn('视觉核查', raised.exception.detail)
                self.assertEqual(receipt, before, 'QA guard must not manufacture a pass')

    def test_completed_render_or_agent_claim_is_not_a_quality_pass(self):
        receipt = self.receipt(
            status='completed', quality_issues=[],
            proof_url='/synthetic/proof',
            agent_summary='已完成，效果良好',
            metrics={'renderer': 'ocv-remotion-code-v1', 'frames': 300,
                     'layout_overflow': [], 'preview_count': 3},
        )
        with self.assertRaises(HTTPException) as raised:
            adoption._require_quality_passed(receipt)
        self.assertEqual(raised.exception.status_code, 409)

    def test_explicit_unambiguous_pass_is_eligible(self):
        variants = [
            {'quality_status': 'passed'},
            {'metrics': {'quality_status': 'passed'}},
            {'quality_status': 'passed', 'metrics': {'quality_status': 'passed'}},
            {'quality_status': None, 'metrics': {'quality_status': 'passed'}},
        ]
        for fields in variants:
            with self.subTest(fields=fields):
                self.assertIsNone(adoption._require_quality_passed(self.receipt(**fields)))

    def test_formal_adoption_stops_before_media_or_record_mutation(self):
        preview_id = 'a' * 32
        variants = [
            {},
            {'quality_status': 'needs_review'},
            {'quality_status': 'passed', 'metrics': {'quality_status': 'needs_review'}},
            {'metrics': {'quality_status': 'pending'}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            pointer = project / 'preview-pointer.json'
            pointer.write_text(json.dumps({'preview_id': preview_id}), encoding='utf-8')
            record = {'revision': 7, 'status': 'completed', 'shots': [
                {'id': 'shot', 'image_status': 'completed', 'kind': 'video',
                 'video': 'original.mp4', 'video_status': 'completed'},
            ]}
            original_record = copy.deepcopy(record)
            original_clip = project / 'original.mp4'
            original_clip.write_bytes(b'unchanged-formal-result')
            for fields in variants:
                with self.subTest(fields=fields), \
                     patch.object(adoption, 'owned_project', return_value=project), \
                     patch.object(adoption, '_target', return_value=(record, record['shots'][0])), \
                     patch.object(adoption.previews, '_pointer', return_value=pointer), \
                     patch.object(adoption.previews, '_receipt', return_value=self.receipt(**fields)), \
                     patch.object(adoption.previews, '_snapshot', side_effect=AssertionError('must stop at QA')) as snapshot, \
                     patch.object(adoption, '_verify_silent', side_effect=AssertionError('must not copy media')) as verify, \
                     patch.object(adoption, 'publish', side_effect=AssertionError('must not publish')) as publish, \
                     patch.object(adoption, '_finish', side_effect=AssertionError('must not save')) as finish:
                    with self.assertRaises(HTTPException) as raised:
                        adoption.adopt('synthetic', 'shot',
                                       adoption.Choice(revision=7, preview_id=preview_id),
                                       Request({'type': 'http'}))
                    self.assertEqual(raised.exception.status_code, 409)
                    for downstream in (snapshot, verify, publish, finish):
                        downstream.assert_not_called()
                    self.assertEqual(record, original_record)
                    self.assertEqual(original_clip.read_bytes(), b'unchanged-formal-result')
                    self.assertFalse((project / 'assets').exists())

    def test_legacy_local_preview_without_new_workflow_is_unchanged(self):
        self.assertIsNone(adoption._require_quality_passed({'engine': 'local'}))


if __name__ == '__main__':
    unittest.main()
