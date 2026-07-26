import unittest

from module2_5_text_corrector import (
    clean_alignment_text,
    correct_scene_texts_to_original,
    split_corrected_scenes,
)


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

    def test_asr_omitted_phrase_is_restored_without_loss_or_overlap(self) -> None:
        original = "远处的海面像凝固的墨水，钟楼的轮廓在雾中若隐若现。蓝焰提灯忽然熄灭，整座城市陷入黑暗。"
        scenes = [
            {"start": 0, "end": 2, "text_content": "远处的海面像凝固的墨水，"},
            {"start": 2, "end": 4, "text_content": "中若隐若现。蓝"},
            {"start": 4, "end": 6, "text_content": "焰提灯忽然熄灭，"},
            {"start": 6, "end": 8, "text_content": "整座城市陷入黑暗。"},
        ]
        corrected = correct_scene_texts_to_original(scenes, original)
        self.assertEqual(clean_alignment_text("".join(corrected)), clean_alignment_text(original))
        self.assertIn("钟楼的轮廓在雾中若隐若现", "".join(corrected))
        self.assertTrue(corrected[1].startswith("钟楼的轮廓在雾"))


if __name__ == "__main__":
    unittest.main()
