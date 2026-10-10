import unittest

from backend.app.video_text_policy import (
    visual_first_prompt_issues,
    remove_unapproved_visible_text,
    visual_first_long_text_issues,
)


class VisibleTextQuoteBoundariesTests(unittest.TestCase):
    def setUp(self):
        self.plan = {'reference_texts': [
            {'text': '汗液乳酸线性检测范围'},
            {'text': '实验室性能验证'},
        ], 'beats': []}

    def test_ascii_labels_do_not_capture_layout_between_quotes(self):
        prompt = 'The title reads "汗液乳酸线性检测范围" at the top, the footer reads "实验室性能验证".'
        self.assertEqual(visual_first_prompt_issues(prompt, self.plan), [])
        self.assertEqual(remove_unapproved_visible_text(prompt, self.plan), prompt)

    def test_unapproved_second_label_is_still_detected(self):
        prompt = 'The title reads "汗液乳酸线性检测范围" at the top, the footer reads "未经批准的文字".'
        issues = visual_first_prompt_issues(prompt, self.plan)
        self.assertEqual(len(issues), 1)
        self.assertIn('未经批准的文字', issues[0])
        self.assertNotIn('at the top', issues[0])

    def test_chinese_quotes_and_negative_instructions(self):
        self.assertEqual(visual_first_prompt_issues('标题显示“汗液乳酸线性检测范围”；禁止字幕显示“旁白全文”。', self.plan), [])
        self.assertTrue(visual_first_prompt_issues('标签显示“新增字样”。', self.plan))

    def test_actual_long_prose_is_still_detected(self):
        self.assertTrue(visual_first_long_text_issues('The caption reads "This is an entire sentence of explanatory prose".'))


if __name__ == '__main__':
    unittest.main()
