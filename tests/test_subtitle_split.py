import unittest

from module2_5_text_corrector import (
    clean_alignment_text,
    correct_scene_texts_to_original,
    split_corrected_scenes,
    split_subtitle_text,
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
        self.assertTrue(all(len(item["text_content"]) <= 28 for item in result))
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

    def test_recombines_asr_boundary_that_split_a_name(self) -> None:
        scenes = [
            {"start": 0.0, "end": 1.0, "text_content": "晚上十点，周"},
            {"start": 1.0, "end": 4.0, "text_content": "屿和许宁坐在出租屋的餐桌旁。"},
        ]
        corrected = ["晚上十点，周", "屿和许宁坐在出租屋的餐桌旁。"]
        result = split_corrected_scenes(scenes, corrected, max_chars=24)

        self.assertEqual([item["text_content"] for item in result], ["晚上十点，周屿和许宁坐在出租屋的餐桌旁。"])
        self.assertEqual(result[0]["start"], 0.0)
        self.assertEqual(result[-1]["end"], 4.0)

    def test_comma_is_candidate_but_enumeration_comma_is_not(self) -> None:
        text = "关系需要讨论家务、职业机会、父母照料和经济风险，也需要讨论一个人的未来为什么总要依靠另一个人的牺牲。"
        chunks = split_subtitle_text(text, max_chars=24)

        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(any(chunk.endswith("，") for chunk in chunks[:-1]))
        self.assertTrue(all(not chunk.endswith("、") for chunk in chunks[:-1]))
        self.assertTrue(all(not chunk.startswith("、") for chunk in chunks[1:]))

    def test_wrappers_do_not_create_dangling_quote_or_title_mark(self) -> None:
        text = "成熟的爱情不是一句“只要相爱就够了”，而是愿意把《共同生活》里的期待变成清楚的协商。"
        chunks = split_subtitle_text(text, max_chars=24)

        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(not chunk.endswith(("“", "‘", "<", "《")) for chunk in chunks))
        self.assertTrue(all(not chunk.startswith(("”", "’", ">", "》")) for chunk in chunks))

    def test_long_enumeration_prefers_result_clause_over_splitting_a_word(self) -> None:
        text = "更多时候，两个人明明还在乎彼此，却被房租、工作、父母、未来和日复一日的疲惫推到了桌子的两端。"
        chunks = split_subtitle_text(text, max_chars=24)

        self.assertEqual("".join(chunks), text)
        self.assertIn("推到了桌子的两端。", chunks)
        self.assertTrue(all(not chunk.endswith("工") for chunk in chunks))
        self.assertTrue(all(not chunk.startswith("作、") for chunk in chunks))

    def test_long_unpunctuated_text_uses_rare_fallback_without_losing_text(self) -> None:
        text = "这是一段刻意没有任何标点而且长度明显超过正常字幕显示范围用于验证最终安全兜底仍然能够完整覆盖全部原始文字的测试内容"
        chunks = split_subtitle_text(text, max_chars=24)

        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk) <= 28 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
