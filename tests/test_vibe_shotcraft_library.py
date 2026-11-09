"""Offline archive integrity, exact style mapping and bounded source access."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from backend.app.vibe_shotcraft_library import ShotcraftLibrary, safe_path, status
from tools.sync_video_shotcraft import download_asset, download_previews, parse_library, resolve_style_demos


class ShotcraftLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.files = {}
        for relative, text in {
            'source/references/shots/camera/camera-moves.md': '# recipe\n' + '参数不能丢失。' * 100,
            'source/demos/camera/camera-moves/First.tsx': 'export const First = () => null;',
            'source/demos/camera/camera-moves/Second.tsx': 'export const Second = () => null;',
            'source/demos/_fixtures/Look.tsx': 'export const LOOK = {};',
        }.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
            data = path.read_bytes()
            self.files[relative] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        styles = []
        for identity, stem in (('first', 'First'), ('second', 'Second')):
            styles.append({'id': identity, 'card_id': 'camera-moves', 'category': 'camera',
                'label': identity, 'description': '摄影机运动', 'use': '演示',
                'recipe_path': 'source/references/shots/camera/camera-moves.md',
                'demo_paths': [f'source/demos/camera/camera-moves/{stem}.tsx'],
                'dependency_paths': ['source/demos/_fixtures/Look.tsx'],
                'preview_path': f'previews/camera/{identity}.mp4'})
        self.manifest = {'schema_version': 1, 'upstream': {'commit': 'a' * 40},
            'categories': {'camera': {'zh': '运镜与空间'}}, 'files': self.files,
            'cards': [{'id': 'camera-moves', 'style_ids': ['first', 'second']}], 'styles': styles}
        self.write_manifest()
        (self.root / 'runtime_supported.json').write_text(json.dumps({'schema_version': 1,
            'styles': {'first': {'renderer_id': 'first', 'status': 'supported'}}}), encoding='utf-8')

    def write_manifest(self):
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def library(self):
        return ShotcraftLibrary(self.root)

    def test_status_distinguishes_reference_runtime_preview_and_render_validation(self):
        result = self.library().status(verify_hashes=True)
        self.assertEqual(result['card_count'], 1)
        self.assertEqual(result['style_count'], 2)
        self.assertEqual(result['runtime_ready_styles'], ['first'])
        self.assertEqual(result['preview_count'], 0)
        self.assertFalse(result['archive_complete'])
        self.assertFalse(result['all_styles_rendered'])
        self.assertTrue(result['hashes_verified'])

    def test_multistyle_card_has_exact_independent_demo_paths(self):
        library = self.library()
        self.assertIn('/First.tsx', library.read_sources('first')['files'][0]['path'])
        self.assertIn('/Second.tsx', library.read_sources('second')['files'][0]['path'])
        self.assertEqual(library.get_card('camera-moves')['style_ids'], ['first', 'second'])
        self.assertEqual(library.get_style('second')['runtime_readiness']['status'], 'archived_not_ready')

    def test_ready_only_is_an_explicit_runtime_allowlist(self):
        catalog = self.library().compact_catalog(ready_only=True)
        self.assertEqual([item['id'] for item in catalog['items']], ['first'])
        self.assertTrue(catalog['complete'])
        self.assertEqual(catalog['omitted_styles'], 0)

    def test_ready_only_checks_readiness_only_for_runtime_supported_ids(self):
        library = self.library()
        expected = [item for item in library.compact_catalog()['items'] if item['ready']]
        # An explicit non-supported registry entry must not become a candidate.
        library.runtime['second'] = {'status': 'archived_not_ready'}
        with patch.object(library, '_readiness', wraps=library._readiness) as readiness:
            catalog = library.compact_catalog(ready_only=True)
        self.assertEqual(catalog['items'], expected)
        self.assertEqual(catalog['total_styles'], len(expected))
        self.assertEqual([call.args[0]['id'] for call in readiness.call_args_list], ['first'])

    def test_ready_only_empty_registry_does_not_probe_any_style_files(self):
        library = self.library()
        library.runtime = {}
        with patch.object(library, '_readiness', wraps=library._readiness) as readiness:
            catalog = library.compact_catalog(ready_only=True)
        readiness.assert_not_called()
        self.assertEqual(catalog['items'], [])
        self.assertEqual(catalog['total_styles'], 0)
        self.assertTrue(catalog['complete'])

    def test_full_catalog_still_checks_and_returns_archived_styles(self):
        library = self.library()
        with patch.object(library, '_readiness', wraps=library._readiness) as readiness:
            catalog = library.compact_catalog(ready_only=False)
        self.assertEqual([call.args[0]['id'] for call in readiness.call_args_list], ['first', 'second'])
        self.assertEqual([item['id'] for item in catalog['items']], ['first', 'second'])
        self.assertFalse(catalog['items'][1]['ready'])
        self.assertEqual(catalog['items'][1]['runtime'], 'archived_not_ready')

    def test_catalog_budget_reports_omissions_without_cutting_json(self):
        catalog = self.library().compact_catalog(max_chars=500)
        self.assertFalse(catalog['complete'])
        self.assertGreater(catalog['omitted_styles'], 0)
        self.assertLessEqual(len(json.dumps(catalog, ensure_ascii=False, separators=(',', ':'))), 500)
        self.assertEqual(catalog['total_styles'], catalog['returned_styles'] + catalog['omitted_styles'])
        with self.assertRaises(ValueError):
            self.library().compact_catalog(max_chars=1)

    def test_read_recipe_and_sources_never_claim_truncated_text_is_complete(self):
        recipe = self.library().read_recipe('first', max_chars=10)
        self.assertEqual(len(recipe['text']), 10)
        self.assertTrue(recipe['truncated'])
        self.assertFalse(recipe['complete'])
        result = self.library().read_sources('first', max_chars_per_file=5, include_dependencies=True)
        self.assertEqual(len(result['files']), 2)
        self.assertFalse(result['complete'])

    def test_unknown_id_and_path_traversal_are_rejected(self):
        for identity in ('missing', '../first', 'a/b', 'C:/x', 'first\\second', ''):
            with self.subTest(identity=identity), self.assertRaises((ValueError, KeyError)):
                self.library().read_recipe(identity)
        for path in ('../secret', '/secret', 'C:/secret', 'a\\..\\secret', './source/file'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_path(self.root, path)

    def test_source_cannot_read_other_style_or_arbitrary_file(self):
        with self.assertRaises(ValueError):
            self.library().read_source('first', 'source/demos/camera/camera-moves/Second.tsx')
        with self.assertRaises(ValueError):
            self.library().read_source('first', 'manifest.json')

    def test_missing_and_tampered_sources_fail_closed(self):
        path = self.root / self.manifest['styles'][0]['demo_paths'][0]
        original = path.read_bytes()
        path.write_bytes(original.replace(b'First', b'Wrong'))
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            self.library().read_sources('first')
        self.assertIn(path.relative_to(self.root).as_posix(), self.library().status(True)['damaged_files'])
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.library().read_sources('first')
        self.assertFalse(self.library().get_style('first')['runtime_readiness']['ready'])
        self.assertEqual(self.library().compact_catalog(ready_only=True)['items'], [])

    def test_manifest_and_registry_unknown_or_duplicate_ids_fail(self):
        duplicate = copy.deepcopy(self.manifest['styles'][0])
        self.manifest['styles'].append(duplicate)
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.library()
        self.manifest['styles'].pop()
        self.write_manifest()
        (self.root / 'runtime_supported.json').write_text(json.dumps({'schema_version': 1,
            'styles': {'unknown': {'status': 'supported'}}}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'unknown style'):
            self.library()

    def test_corrupt_inventory_path_and_symlink_escape_fail(self):
        self.manifest['files']['../secret'] = {'bytes': 1, 'sha256': '0' * 64}
        self.write_manifest()
        with self.assertRaises(ValueError):
            self.library()
        with tempfile.TemporaryDirectory() as outside:
            try:
                (self.root / 'escape').symlink_to(Path(outside), target_is_directory=True)
            except OSError:
                return  # Windows may deny creating symlinks without developer mode.
            with self.assertRaises(ValueError):
                safe_path(self.root, 'escape/secret')

    def test_missing_optional_archive_has_actionable_status(self):
        result = status(self.root / 'absent')
        self.assertFalse(result['available'])
        self.assertFalse(result['archive_complete'])


class UpstreamShotcraftMappingTests(unittest.TestCase):
    source = Path(__file__).resolve().parents[1] / 'workspace/vibe_lab/vendor/video-shotcraft'

    def test_full_pinned_gallery_maps_124_cards_and_150_styles(self):
        if not (self.source / 'gallery/api/library.json').is_file():
            self.skipTest('Optional upstream checkout is not installed')
        library, cards, styles = parse_library(self.source)
        self.assertEqual((len(cards), len(styles), len(library['categories'])), (124, 150, 10))
        self.assertEqual(len({s['id'] for s in styles}), 150)
        index = {s['id']: s for s in styles}
        self.assertEqual(index['panel-to-canvas']['demo_paths'],
            ['source/demos/interaction/canvas-materialize-moves/PanelToCanvasMaterialize.tsx'])
        self.assertEqual(index['diagram-cascade']['demo_paths'],
            ['source/demos/interaction/canvas-materialize-moves/DiagramCascadeBuild.tsx'])
        self.assertEqual(index['mask-wipe']['demo_paths'],
            ['source/demos/transition/shot-transitions/PortalWipeV2.tsx'])
        self.assertEqual(len(index['crash-zoom-punch']['demo_paths']), 2)
        self.assertIn('source/assets/lib/FlashCut.tsx', index['flash-cut']['demo_paths'])
        self.assertTrue(all(s['runtime_readiness']['status'] == 'archived_not_ready' for s in styles))

    def test_unmapped_multistyle_does_not_guess_by_card_or_filename(self):
        if not (self.source / 'gallery/api/library.json').is_file():
            self.skipTest('Optional upstream checkout is not installed')
        card = {'name': 'canvas-materialize-moves',
                'source': 'references/shots/interaction/canvas-materialize-moves.md',
                'styles': [{'key': 'panel-to-canvas'}, {'key': 'diagram-cascade'}]}
        with self.assertRaisesRegex(ValueError, 'Unresolved multi-style'):
            resolve_style_demos(self.source, card, card['styles'][0], {})


class ShotcraftDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    @staticmethod
    def asset(identity, name, updated='2026-10-01T00:00:00Z'):
        return {'id': identity, 'name': name, 'updated_at': updated,
                'browser_download_url': 'https://github.com/Vincentwei1021/video-shotcraft/releases/download/gallery-media/' + name}

    def test_zip_then_newer_or_missing_singles_matches_author_rules(self):
        zipped = self.asset(1, 'gallery-media.zip')
        older = self.asset(2, 'first.mp4', '2026-09-01T00:00:00Z')
        newer = self.asset(3, 'second.mp4', '2026-11-01T00:00:00Z')
        absent = self.asset(4, 'third.mp4', '2026-09-01T00:00:00Z')
        unwanted = self.asset(5, 'removed.mp4')
        release = {'id': 100, 'tag_name': 'gallery-media', 'draft': False,
                   'html_url': 'https://github.com/Vincentwei1021/video-shotcraft/releases/tag/gallery-media',
                   'assets': [zipped, older, newer, absent, unwanted]}
        archive = self.root / 'input.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('first.mp4', b'first from zip')
            z.writestr('second.mp4', b'old second from zip')
            z.writestr('../removed.mp4', b'must not extract')
        singles = {}
        for name in ('second.mp4', 'third.mp4'):
            path = self.root / ('download-' + name)
            path.write_bytes(name.encode())
            singles[name] = path
        styles = [{'id': name, 'preview_path': f'previews/camera/{name}.mp4'}
                  for name in ('first', 'second', 'third')]
        def download(asset, cache):
            return archive if asset['name'] == 'gallery-media.zip' else singles[asset['name']]
        with patch('tools.sync_video_shotcraft.open_url', return_value=io.BytesIO(json.dumps(release).encode())), \
             patch('tools.sync_video_shotcraft.download_asset', side_effect=download) as mocked:
            result = download_previews(self.root, styles)
        self.assertEqual((self.root / 'previews/camera/first.mp4').read_bytes(), b'first from zip')
        self.assertEqual((self.root / 'previews/camera/second.mp4').read_bytes(), b'second.mp4')
        self.assertEqual(set(result['previews']), {'first.mp4', 'second.mp4', 'third.mp4'})
        self.assertEqual({call.args[0]['name'] for call in mocked.call_args_list},
                         {'gallery-media.zip', 'second.mp4', 'third.mp4'})
        self.assertFalse((self.root / 'removed.mp4').exists())

    def test_missing_release_preview_is_a_hard_failure(self):
        release = {'id': 100, 'tag_name': 'gallery-media', 'draft': False,
                   'html_url': 'https://github.com/Vincentwei1021/video-shotcraft/releases/tag/gallery-media',
                   'assets': []}
        with patch('tools.sync_video_shotcraft.open_url', return_value=io.BytesIO(json.dumps(release).encode())):
            with self.assertRaisesRegex(ValueError, 'missing 1 required'):
                download_previews(self.root, [{'id': 'first', 'preview_path': 'previews/camera/first.mp4'}])

    def test_asset_download_checks_publisher_digest_size_and_official_url(self):
        content = b'official asset content'
        asset = self.asset(1, 'first.mp4')
        asset.update(size=len(content), digest='sha256:' + hashlib.sha256(content).hexdigest())
        with patch('tools.sync_video_shotcraft.open_url', return_value=io.BytesIO(content)):
            downloaded = download_asset(asset, self.root / 'cache')
        self.assertEqual(downloaded.read_bytes(), content)
        with patch('tools.sync_video_shotcraft.open_url') as network:
            self.assertEqual(download_asset(asset, self.root / 'cache'), downloaded)
            network.assert_not_called()
        asset['browser_download_url'] = 'https://example.com/first.mp4'
        with self.assertRaisesRegex(ValueError, 'expected official release'):
            download_asset(asset, self.root / 'other-cache')

    def test_bad_asset_digest_is_not_installed(self):
        content = b'corrupt content'
        asset = self.asset(1, 'first.mp4')
        asset.update(size=len(content), digest='sha256:' + '0' * 64)
        with patch('tools.sync_video_shotcraft.open_url', return_value=io.BytesIO(content)):
            with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
                download_asset(asset, self.root / 'cache')
        self.assertFalse((self.root / 'cache/1-first.mp4').exists())


if __name__ == '__main__':
    unittest.main()
