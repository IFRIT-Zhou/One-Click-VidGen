"""Bundled gallery and explicit template request guards; entirely offline."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Request
from pydantic import ValidationError

from backend.app import vibe_shotcraft_gallery as gallery
from backend.app.vibe_cloud import CloudRequest
from backend.app.vibe_shotcraft import validate_requested_templates
from backend.app.vibe_shotcraft_plan import RECIPE_RULES
from test_vibe_shotcraft_pipeline import FixtureLibrary


class ExplicitTemplateTests(unittest.TestCase):
    def test_empty_default_preserves_legacy_and_auto_selection(self):
        self.assertEqual(CloudRequest(confirm_cloud=True).template_ids, [])
        self.assertEqual(CloudRequest(confirm_cloud=True, template_mode='shotcraft').template_ids, [])
        self.assertEqual(CloudRequest(confirm_cloud=True, template_mode='shotcraft',
            template_ids=['card-stack']).template_ids, ['card-stack'])

    def test_invalid_duplicate_excessive_or_semantic_selection_is_rejected(self):
        for mode, values in [('semantic', ['card-stack']), ('shotcraft', ['made-up']),
                ('shotcraft', ['card-stack', 'card-stack']), ('shotcraft', list(RECIPE_RULES)[:4])]:
            with self.subTest(mode=mode, values=values), self.assertRaises(ValidationError):
                CloudRequest(confirm_cloud=True, template_mode=mode, template_ids=values)

    def test_time_budget_and_outro_coverage_guard_before_billing(self):
        library = FixtureLibrary()
        for duration, values in [(3.99, ['spotlight-hero-card']), (8.99, ['spotlight-hero-card', 'card-stack']),
                (13, ['outro-group-photo-launch'])]:
            with self.subTest(duration=duration, values=values), self.assertRaisesRegex(ValueError, '未调用模型'):
                validate_requested_templates(library, duration, values)
        validate_requested_templates(library, 9, ['spotlight-hero-card', 'card-stack'])
        validate_requested_templates(library, 13, ['spotlight-hero-card', 'outro-group-photo-launch'])
        validate_requested_templates(library, 6, ['outro-group-photo-launch'])
        validate_requested_templates(library, 13, [])

    def test_missing_runtime_rejected_but_evidence_soft_tags_never_block(self):
        library = FixtureLibrary()
        with patch.object(library, 'compact_catalog', return_value={'complete': True, 'items': []}), \
             self.assertRaisesRegex(ValueError, '未就绪'):
            validate_requested_templates(library, 13, ['card-stack'])
        # No independent photo count restriction: a genuine crop of core can
        # satisfy the required original image later, during visual planning.
        validate_requested_templates(FixtureLibrary(), 13, ['scanline-annotate-focus'])


class GalleryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.request = Request({'type': 'http', 'headers': []})
        self.manifest = {'schema_version': 1, 'version': 'offline-v1', 'styles': {}}
        for identity in RECIPE_RULES:
            self.manifest['styles'][identity] = {'preview': identity+'.gif', 'poster': identity+'.jpg'}
            (self.root/(identity+'.gif')).write_bytes(b'GIF89a-offline')
            (self.root/(identity+'.jpg')).write_bytes(b'jpeg-offline')
        self.write_manifest()
        for item in (patch.object(gallery, 'GALLERY_ROOT', self.root),
                     patch.object(gallery, 'require_user', return_value={'id': 1}),
                     patch.object(gallery, 'ShotcraftLibrary')):
            mock = item.start(); self.addCleanup(item.stop)
            if item.attribute == 'ShotcraftLibrary':
                self.library = mock
                mock.return_value.get_style.return_value = {'runtime_readiness': {'ready': True}}

    def write_manifest(self):
        (self.root/'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def test_twelve_named_tagged_examples_are_read_only(self):
        result = gallery.catalog(self.request)
        self.assertEqual(len(result['items']), 12)
        self.assertFalse(result['may_charge'])
        self.assertEqual(result['max_templates'], 3)
        self.assertIn('content', result['tag_labels'])
        for item in result['items']:
            self.assertEqual(item['name'], RECIPE_RULES[item['id']]['label'])
            self.assertTrue(item['available'])
            self.assertTrue(item['preview_available'])
            self.assertIn('/api/vibe-templates/'+item['id']+'/preview?v=', item['preview_url'])
            self.assertTrue(item['capabilities']['content_tags'])

    def test_media_only_returns_exact_bundled_files(self):
        identity = next(iter(RECIPE_RULES))
        response = gallery.media(identity, 'preview', self.request)
        self.assertEqual(Path(response.path), self.root/(identity+'.gif'))
        self.assertEqual(response.media_type, 'image/gif')
        self.assertEqual(gallery.media(identity, 'poster', self.request).media_type, 'image/jpeg')
        for identity, asset in [('../secret', 'preview'), ('unknown', 'preview'), (identity, 'manifest.json')]:
            with self.subTest(identity=identity, asset=asset), self.assertRaises(HTTPException) as error:
                gallery.media(identity, asset, self.request)
            self.assertEqual(error.exception.status_code, 404)

    def test_manifest_path_traversal_absolute_path_and_remote_url_are_not_served(self):
        identity = next(iter(RECIPE_RULES))
        for value in ('../private.gif', 'C:/private.gif', 'https://example.invalid/a.gif', 'other.gif'):
            with self.subTest(value=value):
                self.manifest['styles'][identity]['preview'] = value
                self.write_manifest()
                item = gallery.catalog(self.request)['items'][0]
                self.assertFalse(item['preview_available'])
                self.assertIsNone(item['preview_url'])
                with self.assertRaises(HTTPException):
                    gallery.media(identity, 'preview', self.request)

    def test_missing_preview_does_not_claim_runtime_is_unavailable(self):
        identity = next(iter(RECIPE_RULES))
        (self.root/(identity+'.gif')).unlink()
        item = gallery.catalog(self.request)['items'][0]
        self.assertTrue(item['available'])
        self.assertFalse(item['preview_available'])
        self.assertIsNotNone(item['poster_url'])
        self.assertIn('未安装', item['preview_reason'])

    def test_missing_runtime_can_still_show_bundled_examples(self):
        self.library.side_effect = FileNotFoundError('offline missing library')
        result = gallery.catalog(self.request)
        self.assertTrue(all(not item['available'] for item in result['items']))
        self.assertTrue(all(item['preview_available'] for item in result['items']))
        self.assertTrue(all('安装' in item['reason'] for item in result['items']))

    def test_invalid_manifest_keeps_tags_but_does_not_forge_media_urls(self):
        (self.root/'manifest.json').write_text('{broken', encoding='utf-8')
        result = gallery.catalog(self.request)
        self.assertEqual(result['version'], 'unavailable')
        self.assertTrue(all(item['preview_url'] is None for item in result['items']))
        self.assertEqual(len(result['items']), 12)

    def test_media_and_catalog_require_local_session(self):
        with patch.object(gallery, 'require_user', side_effect=HTTPException(401, 'login')):
            with self.assertRaises(HTTPException): gallery.catalog(self.request)
            with self.assertRaises(HTTPException): gallery.media(next(iter(RECIPE_RULES)), 'preview', self.request)


if __name__ == '__main__':
    unittest.main()
