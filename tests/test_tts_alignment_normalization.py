import os
import unittest

from backend.app.tts_alignment import align_starts, clean


def character_words(text):
    return [dict(word=char, start=index * .2, end=(index + 1) * .2)
            for index, char in enumerate(text)]


class AlignmentNormalizationTests(unittest.TestCase):
    def test_year_month_and_count_have_same_representation(self):
        self.assertEqual(clean('二零二五年四月至十二月，三十一例'), clean('2025年4月至12月，31例'))
        self.assertEqual(clean('二〇二五'), '2025')
        self.assertNotEqual(clean('三十一'), clean('32'))

    def test_numbers_split_across_asr_words_keep_timing(self):
        recognized = character_words('2025年4月至12月31例患者')
        result = align_starts(['二零二五年四月至十二月', '三十一例患者'], recognized, 10)
        self.assertEqual(result, [0, 2.2])

    def test_chinese_number_tokens_merge_before_normalization(self):
        self.assertEqual(align_starts(['31例患者'], character_words('三十一例患者'), 3), [0])

    @unittest.skipUnless(os.name == 'nt', 'Windows built-in simplified Chinese conversion')
    def test_actual_local_asr_transcript_with_specialist_errors(self):
        display = ['南方医科大学南方医院普通外科团队，',
                   '在《中华消化外科杂志》报告了一项探索。',
                   '论文第一作者是刘浩，通信作者是胡彦锋。',
                   '研究前瞻性收集了二〇二五年四月至十二月，',
                   '三十一例接受国产单孔蛇形臂机器人辅助胃肠手术的患者资料。']
        recognized = ('南方一科大學南方醫院普通外科團隊在中華消化外科雜誌報告了一項探索'
                      '論文第一作者是劉浩通信作者是湖岩峰'
                      '研究前瞻性收集了2025年4月至12月'
                      '31例接受國產單孔涉行避激器人輔助衛乘手術的患者資料')
        starts = align_starts(display, character_words(recognized), 30,
                              ''.join(display).replace('二〇二五', '二零二五'))
        self.assertEqual(len(starts), 5)
        self.assertTrue(all(b > a for a, b in zip(starts, starts[1:])))

    @unittest.skipUnless(os.name == 'nt', 'Windows built-in simplified Chinese conversion')
    def test_simplification_does_not_fabricate_missing_words(self):
        self.assertEqual(clean('醫院團隊學術報告'), clean('医院团队学术报告'))
        with self.assertRaises(ValueError):
            align_starts(['三十一例接受国产单孔蛇形臂机器人辅助胃肠手术的患者资料'],
                         character_words('31例接受'), 10)

    def test_numeric_normalization_does_not_allow_wrong_short_number(self):
        with self.assertRaises(ValueError):
            align_starts(['三十一'], character_words('32'), 1)


if __name__ == '__main__':
    unittest.main()
