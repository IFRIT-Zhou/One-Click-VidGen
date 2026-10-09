import unittest
from backend.app.tts_alignment import align_starts


def words(*texts):
    return [dict(word=text, start=i * 2, end=i * 2 + 1.8)
            for i, text in enumerate(texts)]


class PhoneticAlignmentTests(unittest.TestCase):
    def test_actual_specialist_homophones_keep_new_audio_times(self):
        self.assertEqual(align_starts(
            ['今天我们来聊聊胃黏膜糜烂', '瞬间就慌了，心里咯噔一下'],
            words('今天我们来聊聊为年模迷烂', '瞬间就荒了行李哥等一下'), 4), [0, 2])

    def test_pronunciation_override_and_asr_homophone(self):
        self.assertEqual(align_starts(['会不会癌变'], words('会不会挨变'), 2,
                                      '会不会皑变'), [0])

    def test_missing_phrase_not_fabricated_by_phonetics(self):
        with self.assertRaises(ValueError):
            align_starts(['胃黏膜糜烂', '需要进行规范治疗'], words('为年模迷烂'), 4)

    def test_different_speech_and_wrong_numbers_rejected(self):
        for expected, recognized in [('胃黏膜糜烂', '今天出去玩'), ('三十一', '32')]:
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                align_starts([expected], words(recognized), 2)

    def test_long_gap_still_rejected(self):
        recognized = words('为年模迷烂', '需要治疗')
        recognized[1].update(start=8, end=9)
        with self.assertRaisesRegex(ValueError, '无语音'):
            align_starts(['胃黏膜糜烂', '需要治疗'], recognized, 10)


if __name__ == '__main__':
    unittest.main()
