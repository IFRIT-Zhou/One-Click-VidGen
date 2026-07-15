import unittest

from module2_5_text_corrector import split_corrected_scenes


class SubtitleSplitTest(unittest.TestCase):
    def test_long_corrected_text_is_split_without_time_gaps(self) -> None:
        text = (
            "看看现在的真实环境，大语言模型已经非常强大，哪怕面对复杂专业领域，"
            "它们也能理解普通人的自然表达，并给出清晰可靠的回答，所以字幕不应该一次塞满整个屏幕。"
        )
        scenes = [{"id": "segment_001", "start": 10.0, "end": 26.36, "text_content": "ASR"}]
        result = split_corrected_scenes(scenes, [text], max_chars=24)

        self.assertGreater(len(result), 1)
        self.assertTrue(all(len(item["text_content"]) <= 24 for item in result))
        self.assertEqual(result[0]["start"], 10.0)
        self.assertEqual(result[-1]["end"], 26.36)
        for previous, current in zip(result, result[1:]):
            self.assertEqual(previous["end"], current["start"])
        self.assertEqual("".join(item["text_content"] for item in result), text)


if __name__ == "__main__":
    unittest.main()
