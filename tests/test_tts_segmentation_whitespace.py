"""Offline regression: paragraph separators must never be numbered as speech."""
import io
import json
import random
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock

from backend.app.tts_segmentation import (
    _validate_agent_groups,
    build_safe_sentence_units,
    group_with_voice_segmentation_agent,
    segment_indextts25_text,
    split_strong_sentence_units,
)


class ParagraphSegmentationTests(unittest.TestCase):
    def check_units(self, text, limit=110):
        units = build_safe_sentence_units(text, max_tokens=limit, token_count=len)
        self.assertEqual(''.join(units), text)
        self.assertTrue(all(unit.strip() for unit in units))
        self.assertTrue(all(len(unit) <= limit for unit in units))
        return units

    def test_separators_keep_original_bytes_without_blank_units(self):
        for separator in ('\n', '\n\n', '\r\n\r\n', '\n \t\n', '\r', '\u3000\n'):
            text = '\t\n第一句。' + separator + '第二句！' + separator + '末句没有句号\n\t'
            with self.subTest(separator=repr(separator)):
                units = self.check_units(text)
                self.assertEqual(len(units), 3)
                self.assertEqual(units[0], '\t\n第一句。' + separator)
                self.assertEqual(units[1], '第二句！' + separator)
                self.assertEqual(units[2], '末句没有句号\n\t')

    def test_quotes_ellipses_and_english_spaces_are_preserved(self):
        text = '他说：“先看这里。”\r\n\r\n再等一会儿……\nOne Click VidGen keeps spaces.\n'
        units = self.check_units(text)
        self.assertEqual(len(units), 3)
        self.assertEqual(units[0], '他说：“先看这里。”\r\n\r\n')
        self.assertIn('One Click VidGen', units[-1])

    def test_newline_boundary_without_sentence_punctuation(self):
        text = '第一段没有句号\n\n第二段也没有句号\n\n'
        self.assertEqual(split_strong_sentence_units(text),
                         ['第一段没有句号\n\n', '第二段也没有句号\n\n'])

    def test_paragraph_case_has_29_speech_ids_not_40(self):
        # Same 12-paragraph/29-sentence structure as the report; no private copy.
        paragraphs = [''.join(f'这是第{p}段的第{s}句测试文本。' for s in range(2 + (p < 5)))
                      for p in range(12)]
        text = '\n\n'.join(paragraphs)
        seen = []

        def fake_agent(**kwargs):
            payload = json.loads(kwargs['user_prompt'])
            sentences = payload['sentences']
            seen.extend(sentences)
            self.assertTrue(all(row['text'].strip() for row in sentences))
            return json.dumps([{'includes_sentences': [row['sentence_id']]} for row in sentences])

        chunks, source, _ = segment_indextts25_text(text, token_count=len,
                                                  agent_enabled=True, agent_call=fake_agent)
        self.assertEqual(source, 'voice_segmentation_agent')
        self.assertEqual(len(seen), 29)
        self.assertEqual([row['sentence_id'] for row in seen], list(range(1, 30)))
        self.assertEqual(''.join(chunks), text)
        self.assertEqual(''.join(row['text'] for row in seen), text)
        self.assertEqual(sum(row['text'].count('\n\n') for row in seen), 11)

    def test_near_limit_newlines_never_become_empty_or_punctuation_only_tail(self):
        for text in ('甲' * 109 + '。\n\n', '甲' * 108 + '。”\r\n\r\n',
                     '甲' * 109 + '。\n\n' + '乙' * 109 + '。\n\n',
                     '\n\n' + '甲' * 200 + '。\n\n'):
            with self.subTest(text_length=len(text)):
                units = self.check_units(text)
                self.assertTrue(all(any(char.isalnum() for char in unit) for unit in units))

    def test_agent_failure_keeps_exact_text_and_no_blank_chunks(self):
        text = ('甲' * 70 + '。\n\n乙' * 70 + '。\r\n\r\n丙' * 65 + '。\n')
        agent = Mock(return_value='[{"includes_sentences":[1,3]}]')
        log = io.StringIO()
        with redirect_stdout(log):
            chunks, source, _ = segment_indextts25_text(text, token_count=len,
                                                      agent_enabled=True, agent_call=agent)
        self.assertEqual(source, 'python_fallback')
        self.assertEqual(''.join(chunks), text)
        self.assertTrue(all(chunk.strip() and len(chunk) <= 110 for chunk in chunks))
        self.assertIn('第 1 组期望 [1,2]，实际 [1,3]', log.getvalue())
        self.assertIn('本组第 2 项应为 2，实际为 3', log.getvalue())
        self.assertEqual(agent.call_count, 1)

    def test_whitespace_only_cannot_start_agent_or_synthesis(self):
        agent = Mock(side_effect=AssertionError('must not call model'))
        for text in ('', '\n\n', ' \r\n\t '):
            with self.subTest(text=repr(text)), self.assertRaisesRegex(ValueError, '文案为空'):
                segment_indextts25_text(text, token_count=len, agent_enabled=True, agent_call=agent)
        with self.assertRaisesRegex(ValueError, '不能单独编号空白'):
            group_with_voice_segmentation_agent(['甲。', '\n\n'], max_tokens=110,
                                               token_count=len, agent_call=agent)
        agent.assert_not_called()

    def test_extreme_whitespace_is_rejected_not_silently_dropped(self):
        for text in ('\n' * 150 + '甲', '甲' + '\n' * 150):
            with self.subTest(text=repr(text[:10])), self.assertRaisesRegex(ValueError, '连续空白过长'):
                self.check_units(text)

    def test_many_layout_variants_preserve_coverage_and_token_ceiling(self):
        rng = random.Random(20261030)
        for _ in range(100):
            text = rng.choice(('', '\n', '\r\n\t')) + ''.join(
                rng.choice(('甲', '乙', 'A')) * rng.randint(1, 180)
                + rng.choice(('。', '！”', '……', ''))
                + rng.choice(('\n\n', '\r\n', '\t \n', ' '))
                for _ in range(rng.randint(1, 7)))
            self.check_units(text)


class AgentNumberDiagnosticTests(unittest.TestCase):
    def validate(self, groups):
        return _validate_agent_groups(groups, ['甲。', '乙。', '丙。', '丁。'],
                                      max_tokens=110, token_count=len)

    def test_missing_duplicate_and_out_of_order_report_group_and_first_mismatch(self):
        for ids, position, expected, actual in (([2], 1, 1, 2), ([1, 3], 2, 2, 3),
                                               ([1, 1], 2, 2, 1), ([1, 3, 2], 2, 2, 3)):
            with self.subTest(ids=ids), self.assertRaises(ValueError) as caught:
                self.validate([{'includes_sentences': ids}])
            error = str(caught.exception)
            self.assertIn('第 1 组', error)
            self.assertIn(f'本组第 {position} 项应为 {expected}，实际为 {actual}', error)
        with self.assertRaisesRegex(ValueError, '第 2 组期望'):
            self.validate([{'includes_sentences': [1, 2]}, {'includes_sentences': [2, 3, 4]}])

    def test_missing_tail_reports_range(self):
        with self.assertRaisesRegex(ValueError, '缺少句子编号 3～4'):
            self.validate([{'includes_sentences': [1, 2]}])

    def test_out_of_range_reports_actual_id(self):
        for bad in (0, -1, 5):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as caught:
                self.validate([{'includes_sentences': [bad]}])
            self.assertIn(f'超出范围：[{bad}]', str(caught.exception))
            self.assertIn('有效范围 1～4', str(caught.exception))

    def test_boolean_and_fractional_ids_are_not_silently_converted(self):
        for bad in (True, False, 1.5, 1.0, None, '1.5'):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, '第 1 组句子编号不是整数'):
                self.validate([{'includes_sentences': [bad, 2, 3, 4]}])

    def test_integer_strings_and_group_wrapper_remain_compatible(self):
        self.assertEqual(self.validate({'groups': [{'includes_sentences': ['1', '2', '3', '4']}]}),
                         ['甲。乙。丙。丁。'])

    def test_missing_group_field_is_identified(self):
        with self.assertRaisesRegex(ValueError, '第 2 组缺少非空 includes_sentences'):
            self.validate([{'includes_sentences': [1]}, {}])


if __name__ == '__main__':
    unittest.main()
