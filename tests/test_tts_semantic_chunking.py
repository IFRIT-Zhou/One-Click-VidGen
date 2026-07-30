import re
import unittest

from module1_agent_director import (
    CHUNK_MAX_LEN,
    _split_sentences_preserving_closers,
    split_indextts2_text,
)


class TtsSemanticChunkingTest(unittest.TestCase):
    def assert_complete(self, source: str, chunks: list[str]) -> None:
        expected = "".join(
            re.sub(r"[ \t]+", " ", paragraph).strip()
            for paragraph in re.split(r"[\r\n]+", source)
            if paragraph.strip()
        )
        self.assertEqual("".join(chunks), expected)
        self.assertTrue(all(chunk for chunk in chunks))
        self.assertTrue(all(len(chunk) <= CHUNK_MAX_LEN for chunk in chunks))

    def test_complete_sentences_do_not_cross_a_strong_boundary(self) -> None:
        source = (
            "所有走进浓雾的人，第二天都会忘记自己的名字，也再也找不到回家的道路。"
            "年轻的王国骑士艾德里安奉命来到雾港，他按住腰间的佩剑。"
        )
        chunks = split_indextts2_text(source)
        self.assert_complete(source, chunks)
        self.assertTrue(chunks[0].endswith("。"))
        self.assertTrue(chunks[1].startswith("年轻的王国骑士"))

    def test_short_sentence_chooses_a_viable_neighbor(self) -> None:
        source = "钟响了。浓雾中的守卫缓慢抬起头，望向那座从未存在过的黑色塔楼。"
        chunks = split_indextts2_text(source)
        self.assert_complete(source, chunks)
        self.assertGreaterEqual(len(chunks[0]), 25)

    def test_long_punctuation_free_text_obeys_the_hard_limit(self) -> None:
        source = "这是一个没有任何停顿标记但仍然必须安全切开的超长中文测试段落" * 8
        chunks = split_indextts2_text(source)
        self.assert_complete(source, chunks)
        self.assertGreater(len(chunks), 1)

    def test_closing_quote_stays_with_the_spoken_sentence(self) -> None:
        source = (
            "守门人压低声音说道：“钟声响起前，千万不要直视塔顶。”"
            "年轻骑士没有回答，只是握紧佩剑，继续望向浓雾深处那座沉默的黑塔。"
        )
        chunks = split_indextts2_text(source)
        self.assert_complete(source, chunks)
        self.assertTrue(any(chunk.endswith("。”") for chunk in chunks))

    def test_wrapper_marks_never_create_sentence_boundaries(self) -> None:
        source = "“沟通”《关系修复》<重点>【温柔表达】都属于同一个完整句子"
        self.assertEqual(_split_sentences_preserving_closers(source), [source])
        self.assertEqual(split_indextts2_text(source), [source])

    def test_spoken_square_bracket_content_is_not_treated_as_production_note(self) -> None:
        source = "今天要讲的是【关系修复】，这些内容应当正常保留并参与配音。"
        chunks = split_indextts2_text(source)
        self.assert_complete(source, chunks)
        self.assertIn("【关系修复】", "".join(chunks))

    def test_production_notes_are_removed_before_integrity_check(self) -> None:
        source = "【镜头说明】第一句需要被朗读。\n此处留白三秒\n第二句同样需要被朗读。"
        chunks = split_indextts2_text(source)
        self.assertEqual("".join(chunks), "第一句需要被朗读。第二句同样需要被朗读。")

    def test_english_word_spaces_are_preserved(self) -> None:
        source = "One Click VidGen keeps English words readable. Mixed 中文 narration also works."
        chunks = split_indextts2_text(source)
        self.assert_complete(source, chunks)
        self.assertIn("One Click VidGen", "".join(chunks))


if __name__ == "__main__":
    unittest.main()
