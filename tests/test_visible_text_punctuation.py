import unittest

from backend.app.video_text_policy import visual_first_prompt_issues, remove_unapproved_visible_text


class VisibleTextPunctuationTests(unittest.TestCase):
    def test_punctuation_width_is_not_new_text(self):
        plan = {'beats': [{'texts': [{'text': '深部感染为何难控制？'}]}]}
        prompt = '标题显示“深部感染为何难控制?”'
        self.assertEqual(visual_first_prompt_issues(prompt, plan), [])
        self.assertEqual(remove_unapproved_visible_text(prompt, plan), prompt)

    def test_actual_rewording_still_rejected(self):
        plan = {'beats': [{'texts': [{'text': '深部感染为何难控制？'}]}]}
        self.assertTrue(visual_first_prompt_issues('标题显示“深部感染无法控制?”', plan))

    def test_numeric_and_question_changes_still_rejected(self):
        plan = {'beats': [{'texts': [{'text': '浓度0.5'}, {'text': '安全吗？'}]}]}
        for prompt in ('标签显示“浓度05”', '标题显示“安全吗!”'):
            self.assertTrue(visual_first_prompt_issues(prompt, plan))


if __name__ == '__main__':
    unittest.main()
