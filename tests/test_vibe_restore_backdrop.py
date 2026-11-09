"""Offline original-pixel and hidden-content checks for the backdrop pilot."""
import unittest
from pathlib import Path
import importlib.util
import numpy as np
from PIL import Image, ImageDraw

trial_path = Path(__file__).resolve().parents[1] / 'tools/vibe_restore_backdrop.py'
if not trial_path.is_file():
    raise unittest.SkipTest('Private backdrop prototype is not distributed')
spec = importlib.util.spec_from_file_location('backdrop_trial', trial_path)
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


def rect(label, x, y, r, b):
    return dict(label=label, polygon=[[x,y],[r,y],[r,b],[x,b]])


class BackdropTests(unittest.TestCase):
    def setUp(self):
        self.a = Image.new('RGB', (160, 88), (239,237,241))
        draw = ImageDraw.Draw(self.a)
        draw.line((125,14,150,62), fill=(20,110,160), width=2)
        draw.rectangle((23,30,61,61), fill=(10,80,150))
        self.b = Image.new('RGB', self.a.size, (215,231,249))
        self.hole = [rect('promoted subject',19,25,67,68)]
        self.keep = [rect('evidence',117,8,158,72)]

    def test_no_hidden_pixels_even_in_low_frequency_field(self):
        # Mutating the source behind the hole must change no output pixel.
        altered = self.a.copy()
        ImageDraw.Draw(altered).rectangle((19,25,67,68), fill=(255,120,0))
        first, weights = trial.blend(self.a, self.b, self.hole, self.keep, 65)
        second, other = trial.blend(altered, self.b, self.hole, self.keep, 65)
        self.assertEqual(first.tobytes(), second.tobytes())
        self.assertTrue(np.array_equal(weights, other))
        self.assertEqual(first.crop((117,8,159,73)).tobytes(), self.a.crop((117,8,159,73)).tobytes())
        self.assertEqual(weights[47,67], 255)
        self.assertEqual(weights[47,117], 0)
        row = weights[47,67:118].astype('int16')
        self.assertLess(np.abs(np.diff(row)).max(), 12)
        self.assertTrue(np.all(np.diff(row) <= 0))

    def test_reject_overlapping_or_wrong_canvas(self):
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            trial.blend(self.a, self.b, self.hole, [rect('oops',65,3,85,76)], 40)
        with self.assertRaisesRegex(ValueError, 'same native'):
            trial.blend(self.a, self.b.resize((80,44)), self.hole, self.keep, 40)
        with self.assertRaisesRegex(ValueError, 'Invalid native-canvas'):
            trial.blend(self.a, self.b, [rect('oops',159,0,165,80)], self.keep, 40)
