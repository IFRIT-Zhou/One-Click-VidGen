"""Budget facts cannot force creative work or silently trigger paid retries."""
import copy
import unittest

from backend.app.vibe_agent_blockers import claims_image_budget, reconcile_blocker


class BlockerPolicyTests(unittest.TestCase):
    def setUp(self):
        self.budget = dict(image_limit=2, image_used=1, image_remaining=1,
                           new_images_allowed=True, model_remaining=12)

    def reconcile(self, message='需要增加图片编辑预算，允许再次定向编辑底图', reason='other', **kwargs):
        return reconcile_blocker(message, reason, self.budget, **kwargs)

    def test_legacy_budget_requests_and_explicit_reason(self):
        for text in ('需要增加图片编辑预算，允许再次定向编辑底图',
                     '图片预算已耗尽', '图片编辑额度不足', '已无授权图片请求额度',
                     '请扩充图片预算或提供局部修补能力',
                     'Please increase image editing budget.', 'Image quota is exhausted',
                     'No remaining image budget', 'Image budget has been used up'):
            with self.subTest(text=text):
                self.assertTrue(claims_image_budget(text))
        self.assertTrue(claims_image_budget('无法继续', 'image_budget'))

    def test_quality_model_limit_and_negated_budget_requests_are_not_claims(self):
        for text in ('现有图片有接缝，无法在当前画面上得到可信结果',
                     '模型调用预算不足，图片已经完成',
                     '图片预算仍有剩余，但质量问题不能可靠解决',
                     '不需要增加图片编辑预算，问题是缺少参考素材',
                     '不是图片预算不足，是参考图不完整',
                     "We do not need more image budget. The source is missing.",
                     'Image generation is unreliable; stop for quality review.',
                     '模型调用次数已达上限'):
            with self.subTest(text=text):
                self.assertFalse(claims_image_budget(text))

    def test_one_correction_contains_facts_and_does_not_mutate_budget(self):
        before = copy.deepcopy(self.budget)
        result = self.reconcile(edit_available=True)
        self.assertTrue(result['retry'])
        self.assertEqual(result['receipt']['code'], 'image_budget_claim_mismatch')
        self.assertEqual(result['receipt']['budget']['image_remaining'], 1)
        self.assertIn('不强制', result['receipt']['next'])
        self.assertIn('remove_regions', result['receipt']['next'])
        self.assertEqual(self.budget, before)

    def test_repeated_false_claim_stops_with_truth_not_purchase_advice(self):
        result = self.reconcile(edit_available=True, already_corrected=True)
        self.assertFalse(result['retry'])
        self.assertIn('仍剩 1 次', result['message'])
        self.assertIn('模型复核后仍选择停止', result['message'])
        self.assertNotIn('需要增加图片', result['message'])
        self.assertIn('未自动出图', result['message'])

    def test_real_quality_blocker_can_stop_despite_remaining_budget(self):
        result = self.reconcile('主体边缘存在缺损且原始参考图不可用', 'quality', edit_available=True)
        self.assertFalse(result['retry'])
        self.assertFalse(result['receipt']['budget_claim_corrected'])
        self.assertEqual(result['message'], '主体边缘存在缺损且原始参考图不可用')

    def test_exhausted_quota_never_suggests_more_authorized_calls(self):
        self.budget.update(image_used=2, image_remaining=0)
        result = self.reconcile('图片额度耗尽', 'image_budget', edit_available=True)
        self.assertFalse(result['retry'])
        self.assertEqual(result['message'], '图片额度耗尽')
        self.assertFalse(result['receipt']['budget_claim_corrected'])

    def test_explicit_multi_request_shortfall_is_not_a_false_budget_claim(self):
        before = copy.deepcopy(self.budget)
        message = '剩余1次，但还需3次分别修补背景、人物和道具，需要增加图片预算'
        result = self.reconcile(message, 'image_budget', edit_available=True,
                                required_image_requests=3)
        self.assertFalse(result['retry'])
        self.assertEqual(result['message'], message)
        self.assertFalse(result['receipt']['budget_claim_corrected'])
        self.assertEqual(result['receipt']['budget']['image_remaining'], 1)
        self.assertEqual(result['receipt']['required_image_requests'], 3)
        self.assertTrue(result['receipt']['required_requests_exceed_remaining'])
        self.assertEqual(self.budget, before)

    def test_explicit_request_count_within_remaining_still_gets_one_correction(self):
        result = self.reconcile('需要额外1次图片请求', 'image_budget', edit_available=True,
                                required_image_requests=1)
        self.assertTrue(result['retry'])
        self.assertEqual(result['receipt']['required_image_requests'], 1)
        self.assertFalse(result['receipt']['required_requests_exceed_remaining'])

    def test_required_count_does_not_turn_quality_claim_into_quota_authorization(self):
        message = '当前质量不足，另一个方案需要3次图片请求，但不要求继续'
        result = self.reconcile(message, 'quality', edit_available=True,
                                required_image_requests=3)
        self.assertFalse(result['retry'])
        self.assertEqual(result['message'], message)
        self.assertTrue(result['receipt']['required_requests_exceed_remaining'])

    def test_no_image_permission_never_promotes_leftover_quota_to_authority(self):
        self.budget.update(new_images_allowed=False)
        result = self.reconcile(edit_available=True)
        self.assertFalse(result['retry'])
        self.assertFalse(result['receipt']['budget_claim_corrected'])

    def test_pending_submission_stops_without_suggesting_duplicate_edit(self):
        result = self.reconcile(edit_available=True, pending_image=True)
        self.assertFalse(result['retry'])
        self.assertIn('不能重复提交', result['message'])
        self.assertNotIn('next', result['receipt'])

    def test_unavailable_tool_is_not_fixed_by_buying_more_budget(self):
        result = self.reconcile(edit_available=False)
        self.assertFalse(result['retry'])
        self.assertIn('编辑工具尚不可用', result['message'])
        self.assertIn('并未耗尽', result['message'])

    def test_no_model_budget_for_reconsideration_stops_truthfully(self):
        self.budget['model_remaining'] = 0
        result = self.reconcile(edit_available=True)
        self.assertFalse(result['retry'])
        self.assertIn('模型调用额度已用完', result['message'])
        self.assertIn('仍剩 1 次', result['message'])


if __name__ == '__main__':
    unittest.main()
