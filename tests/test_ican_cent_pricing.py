import ast
import unittest
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from types import SimpleNamespace

source = Path(__file__).resolve().parents[1] / 'deploy/cloud-api/app/ican_images.py'
tree = ast.parse(source.read_text(encoding='utf-8'))
function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'price_quote')

class PricingTests(unittest.TestCase):
    def quote(self, price, currency='CNY'):
        namespace = dict(Decimal=Decimal, ROUND_FLOOR=ROUND_FLOOR,
                         settings=SimpleNamespace(billing_usd_to_cny_rate=Decimal('7'), ican_image_markup_percent=5),
                         prices=lambda: {'gpt-image-2.5': {'currency': currency, 'sizes': {'2560x1440': price}}})
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
        return namespace['price_quote']('gpt-image-2.5', '2560x1440')

    def test_fractional_cent(self):
        self.assertEqual(self.quote('0.035')['credits'], '0.04')

    def test_exact_cent_adds_one_fen(self):
        self.assertEqual(self.quote('0.04')['credits'], '0.05')
        self.assertEqual(self.quote('0.0400')['credits'], '0.05')
        self.assertEqual(self.quote('1.00')['credits'], '1.01')
        self.assertEqual(self.quote('0.04')['markup_percent'], 0)
        self.assertEqual(self.quote('0.04')['rounding'], 'next_cent')

    def test_next_cent(self):
        self.assertEqual(self.quote('0.040001')['credits'], '0.05')
        self.assertEqual(self.quote('0.039999')['credits'], '0.04')
        self.assertEqual(self.quote('0.041')['credits'], '0.05')

    def test_fx_before_rounding(self):
        self.assertEqual(self.quote('0.005', 'USD')['credits'], '0.04')
        self.assertEqual(self.quote('0.01', 'USD')['credits'], '0.08')

    def test_each_size_uses_its_own_price(self):
        namespace = dict(Decimal=Decimal, ROUND_FLOOR=ROUND_FLOOR,
                         settings=SimpleNamespace(billing_usd_to_cny_rate=Decimal('7')),
                         prices=lambda: {'gpt-image-2.5': {'currency': 'CNY', 'sizes': {
                             '2048x1152': '0.035', '2560x1440': '0.04', '3840x2160': '0.071'}}})
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
        for size, expected in [('2048x1152', '0.04'), ('2560x1440', '0.05'), ('3840x2160', '0.08')]:
            self.assertEqual(namespace['price_quote']('gpt-image-2.5', size)['credits'], expected)

    def test_video_sums_rounded_images(self):
        self.assertEqual(Decimal(self.quote('0.035')['credits']) * 6, Decimal('0.24'))

if __name__ == '__main__':
    unittest.main(verbosity=2)
