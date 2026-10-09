import ast
import unittest
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from types import SimpleNamespace

source = Path(__file__).resolve().parents[1] / 'deploy/cloud-api/app/ican_images.py'
tree = ast.parse(source.read_text(encoding='utf-8'))
function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'price_quote')

class PricingTests(unittest.TestCase):
    def quote(self, price, currency='CNY'):
        namespace = dict(Decimal=Decimal, ROUND_CEILING=ROUND_CEILING,
                         settings=SimpleNamespace(billing_usd_to_cny_rate=Decimal('7'), ican_image_markup_percent=5),
                         prices=lambda: {'gpt-image-2.5': {'currency': currency, 'sizes': {'2560x1440': price}}})
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
        return namespace['price_quote']('gpt-image-2.5', '2560x1440')

    def test_fractional_cent(self):
        self.assertEqual(self.quote('0.035')['credits'], '0.04')

    def test_exact_cent_no_extra_markup(self):
        self.assertEqual(self.quote('0.04')['credits'], '0.04')
        self.assertEqual(self.quote('0.04')['markup_percent'], 0)

    def test_next_cent(self):
        self.assertEqual(self.quote('0.040001')['credits'], '0.05')

    def test_fx_before_rounding(self):
        self.assertEqual(self.quote('0.005', 'USD')['credits'], '0.04')

    def test_video_sums_rounded_images(self):
        self.assertEqual(Decimal(self.quote('0.035')['credits']) * 6, Decimal('0.24'))

if __name__ == '__main__':
    unittest.main(verbosity=2)
