"""Offline checks for the bundled, silent template example gallery."""
import hashlib
import json
from pathlib import Path
import unittest

from PIL import Image, ImageStat

ROOT = Path(__file__).resolve().parents[1]
GALLERY = ROOT / 'tools/vibe_remotion/gallery'


class GalleryAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((GALLERY / 'manifest.json').read_text(encoding='utf-8'))
        cls.rules = json.loads((ROOT / 'tools/vibe_remotion/lib/shotcraft-recipes.json').read_text(encoding='utf-8'))

    def test_all_twelve_adapted_templates_have_named_examples(self):
        self.assertEqual(self.manifest['schema_version'], 1)
        self.assertEqual(set(self.manifest['styles']), set(self.rules))
        self.assertEqual(len(self.manifest['styles']), 12)
        self.assertFalse(self.manifest['audio'])
        for identity, entry in self.manifest['styles'].items():
            self.assertEqual(entry['name'], self.rules[identity]['label'])
            self.assertEqual(entry['preview'], identity + '.gif')
            self.assertEqual(entry['poster'], identity + '.jpg')

    def test_distributable_assets_match_recorded_size_and_hash(self):
        for identity, entry in self.manifest['styles'].items():
            with self.subTest(identity=identity):
                for kind in ('preview', 'poster'):
                    data = (GALLERY / entry[kind]).read_bytes()
                    self.assertEqual(len(data), entry[kind + '_bytes'])
                    self.assertEqual(hashlib.sha256(data).hexdigest(), entry[kind + '_sha256'])
                self.assertLess(entry['preview_bytes'], 2 * 1024 * 1024)
        self.assertLess(sum(entry['preview_bytes'] for entry in self.manifest['styles'].values()), 4 * 1024 * 1024)

    def test_gifs_loop_at_declared_dimensions_and_complete_duration(self):
        for identity, entry in self.manifest['styles'].items():
            with self.subTest(identity=identity), Image.open(GALLERY / entry['preview']) as image:
                self.assertEqual(image.format, 'GIF')
                self.assertEqual(image.size, (entry['width'], entry['height']))
                self.assertEqual(image.info['loop'], 0)
                total_ms = 0
                for frame in range(image.n_frames):
                    image.seek(frame)
                    total_ms += image.info['duration']
                self.assertGreater(image.n_frames, 1)
                self.assertAlmostEqual(total_ms / 1000, entry['duration_seconds'], places=2)

    def test_posters_are_readable_frames_not_blank_starts(self):
        for identity, entry in self.manifest['styles'].items():
            with self.subTest(identity=identity), Image.open(GALLERY / entry['poster']) as image:
                self.assertEqual(image.format, 'JPEG')
                self.assertEqual(image.size, (entry['width'], entry['height']))
                self.assertGreater(sum(ImageStat.Stat(image.convert('RGB')).stddev), 10)

    def test_gallery_does_not_package_project_records_or_audio(self):
        expected = {'manifest.json'} | {entry[kind]
            for entry in self.manifest['styles'].values() for kind in ('preview', 'poster')}
        self.assertEqual({path.name for path in GALLERY.iterdir()}, expected)
        text = (GALLERY / 'manifest.json').read_text(encoding='utf-8')
        for private_value in ('workspace/', 'workspace\\', 'narration.wav', 'api_key', 'G:\\'):
            self.assertNotIn(private_value, text)


if __name__ == '__main__':
    unittest.main()
