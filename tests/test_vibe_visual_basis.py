"""Source visual identity cannot disappear behind a nominal hybrid plan."""
import copy
import unittest

from pydantic import ValidationError

from backend.app.vibe_visual_basis import VisualBasis, validate_visual_basis


class VisualBasisTests(unittest.TestCase):
    def basis(self, **overrides):
        raw = dict(mode='hybrid', style_notes='保留原人物与纸色，文字原生重排', elements=[
            dict(label='教授', source='core', kind='subject', treatment='preserve', output_asset_id='mat_person'),
            dict(label='介绍文案', source='core', kind='text', treatment='native')])
        raw.update(overrides)
        return VisualBasis.model_validate(raw)

    def scene(self, asset='mat_person', *, item=False):
        return {'stages': [{'id': 'one', 'visual': {} if item else {'asset_id': asset},
                            'items': [{'id': 'a', **({'asset_id': asset} if item else {})}]}]}

    def specs(self):
        return {'mat_crop': {'mode': 'crop', 'source': 'core', 'references': [], 'box': [0, 0, .4, 1]},
                'mat_person': {'mode': 'matte', 'source': 'mat_crop', 'references': [], 'prompt': '仅保留教授'}}

    def test_crop_then_matte_preserves_lineage_in_stage_visual_or_item(self):
        for item in (False, True):
            basis, scene, specs = self.basis(), self.scene(item=item), self.specs()
            before = copy.deepcopy((basis.model_dump(), scene, specs))
            validate_visual_basis(basis, scene, {'core'}, specs, required=True)
            self.assertEqual((basis.model_dump(), scene, specs), before)

    def test_direct_reuse_needs_no_generation_or_output_alias(self):
        basis = self.basis(elements=[dict(label='期刊', source='ref_03', kind='evidence', treatment='preserve')])
        validate_visual_basis(basis, self.scene('ref_03'), {'core', 'ref_03'}, {}, required=True)

    def test_unknown_source_or_output_is_rejected(self):
        basis = self.basis()
        with self.assertRaisesRegex(ValueError, '不是本次输入原图'):
            validate_visual_basis(basis, self.scene(), {'other'}, {}, required=True)
        with self.assertRaisesRegex(ValueError, '输出素材 mat_person 不存在'):
            validate_visual_basis(basis, self.scene(), {'core'}, {}, required=True)

    def test_declared_preservation_must_be_used_on_screen(self):
        with self.assertRaisesRegex(ValueError, '未在 stage.visual.asset_id 或 item.asset_id 中实际显示'):
            validate_visual_basis(self.basis(), self.scene('core'), {'core'}, self.specs(), required=True)

    def test_preserve_cannot_hide_illustration_anywhere_in_chain(self):
        for variant in ('direct', 'upstream'):
            specs = self.specs()
            if variant == 'direct':
                specs['mat_person'] = {'mode': 'illustration', 'references': ['core']}
            else:
                specs['mat_crop'] = {'mode': 'illustration', 'references': ['core']}
            with self.subTest(variant=variant), self.assertRaisesRegex(ValueError, 'illustration 冒充保真'):
                validate_visual_basis(self.basis(), self.scene(), {'core'}, specs, required=True)

    def test_output_must_derive_from_declared_source_not_another_picture(self):
        specs = self.specs(); specs['mat_crop']['source'] = 'different'
        with self.assertRaisesRegex(ValueError, '不能以 illustration 冒充保真'):
            validate_visual_basis(self.basis(), self.scene(), {'core', 'different'}, specs)

    def test_subject_and_evidence_cannot_be_native_redrawn(self):
        for kind in ('subject', 'evidence'):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValidationError, '不可用代码或生成式插画重画'):
                self.basis(elements=[dict(label='相关主体', source='core', kind=kind, treatment='native')])

    def test_native_can_explain_irrelevant_subject_omission_but_not_silently_drop_it(self):
        elements = [dict(label='统计数据', source='core', kind='chart', treatment='native'),
                    dict(label='无关装饰人像', source='core', kind='subject', treatment='omit', reason='仅为背景装饰，与本镜统计结果无关')]
        basis = self.basis(mode='native', elements=elements)
        validate_visual_basis(basis, {'stages': []}, {'core'}, {}, required=True)
        elements[1]['reason'] = ''
        with self.assertRaisesRegex(ValidationError, '省略原图元素必须说明'):
            self.basis(mode='native', elements=elements)

    def test_hybrid_modes_need_an_actually_preserved_subject_or_evidence(self):
        for mode in ('hybrid', 'image_led'):
            basis = self.basis(mode=mode, elements=[dict(label='文案', source='core', kind='text', treatment='native')])
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, '至少须保留一个原图主体或证据'):
                validate_visual_basis(basis, {'stages': []}, {'core'}, {})

    def test_original_ids_cannot_be_spoofed_or_overwritten_by_generated_assets(self):
        basis = self.basis(elements=[dict(label='人物', source='mat_crop', kind='subject', treatment='preserve')])
        with self.assertRaisesRegex(ValueError, '生成素材不能冒充原图'):
            validate_visual_basis(basis, self.scene('mat_crop'), {'core'}, self.specs())
        with self.assertRaisesRegex(ValueError, '不可覆盖输入原图身份'):
            validate_visual_basis(self.basis(), self.scene(), {'core', 'mat_crop'}, self.specs())

    def test_illustration_cannot_regenerate_protected_subject_via_indirect_reference(self):
        specs = self.specs()
        specs['mat_fake'] = {'mode': 'illustration', 'references': ['mat_crop']}
        with self.assertRaisesRegex(ValueError, '以受保护主体/证据为生成式参考'):
            validate_visual_basis(self.basis(), self.scene(), {'core'}, specs)

    def test_unrelated_optional_illustration_is_allowed_without_budget_requirement(self):
        specs = self.specs()
        specs['mat_icon'] = {'mode': 'illustration', 'references': [], 'prompt': '独立装饰图标'}
        validate_visual_basis(self.basis(), self.scene(), {'core'}, specs)

    def test_entire_material_graph_rejects_cycles_and_missing_roots(self):
        for failure in ('cycle', 'missing'):
            specs = self.specs()
            specs['mat_extra'] = {'mode': 'crop', 'source': 'mat_extra' if failure == 'cycle' else 'unknown'}
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                validate_visual_basis(self.basis(), self.scene(), {'core'}, specs)

    def test_legacy_none_loads_but_new_requests_must_explicitly_account_for_visuals(self):
        validate_visual_basis(None, {}, set(), {})
        with self.assertRaisesRegex(ValueError, '不可默默变成纯文字'):
            validate_visual_basis(None, {}, set(), {}, required=True)

    def test_original_only_policy_never_accepts_derived_image_even_if_faithful(self):
        with self.assertRaisesRegex(ValueError,'不使用生成、裁切或抠图素材'):
            validate_visual_basis(self.basis(),self.scene(),{'core'},self.specs(),original_only=True)
        with self.assertRaisesRegex(ValueError,'只能直接复用原输入'):
            validate_visual_basis(self.basis(),self.scene(),{'core'}, {},original_only=True)

    def test_unknown_fields_and_unbound_output_on_omitted_element_rejected(self):
        with self.assertRaises(ValidationError): self.basis(raw_html='<img>')
        with self.assertRaises(ValidationError):
            self.basis(elements=[dict(label='忽略', source='core', kind='text', treatment='omit',
                                      reason='无关文字', output_asset_id='mat_person')])


if __name__ == '__main__': unittest.main()
