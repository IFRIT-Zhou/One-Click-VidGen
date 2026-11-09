import unittest
from pydantic import ValidationError
from backend.app.vibe_visual_basis import VisualBasis, validate_visual_basis


class NativeDecorationTests(unittest.TestCase):
    def basis(self, kind='decoration', treatment='native'):
        return VisualBasis(mode='native', style_notes='保留原图配色与细分隔线', elements=[
            dict(label='四家中心署名', source='core', kind='text', treatment='native'),
            dict(label='分隔线', source='core', kind=kind, treatment=treatment)])

    def test_code_decoration_is_valid_without_fake_preserved_subject(self):
        basis = self.basis()
        validate_visual_basis(basis, {'stages': []}, {'core'}, {}, original_only=True)

    def test_real_subject_and_evidence_remain_protected(self):
        for kind in ('subject', 'evidence'):
            with self.subTest(kind=kind), self.assertRaises(ValidationError):
                self.basis(kind)

    def test_preserved_decoration_cannot_masquerade_as_native(self):
        with self.assertRaises(ValidationError):
            self.basis(treatment='preserve')

    def test_attribution_text_needs_no_separate_screenshot(self):
        labels = ['中国科学技术大学附属第一医院', 'World Journal of Gastrointestinal Oncology',
                  '2024', 'Song HC et al.', 'doi:10.4251/wjgo.v16.i2.331']
        basis = VisualBasis(mode='native', style_notes='署名与出处按原文排版', elements=[
            dict(label=label, source='core', kind='text', treatment='native') for label in labels] + [
            dict(label='整版署名海报版式', source='core', kind='decoration',
                 treatment='omit', reason='文字分别保留，不复用原布局')])
        validate_visual_basis(basis, {'stages': []}, {'core'}, {}, original_only=True)

    def test_director_distinguishes_citation_text_from_visual_evidence(self):
        from backend.app.vibe_shotcraft_guidance import DIRECTOR
        for rule in ('无需用户提供文字截图', '期刊名称文字不等于期刊封面照片',
                     '真实视觉证据不能改名为text绕过保护'):
            self.assertIn(rule, DIRECTOR)
