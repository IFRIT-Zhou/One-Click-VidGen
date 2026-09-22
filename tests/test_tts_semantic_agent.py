"""Semantic boundaries must survive the voice Agent's transport safeguards."""

import json
import re
import unittest

from backend.app.indextts25_local import load_indextts25_config
from backend.app.tts_segmentation import (
    INDEXTTS25_SEGMENT_MAX_TOKENS,
    build_indextts25_token_counter,
    segment_indextts25_text,
    split_strong_sentence_units,
)


TRAVEL_SCRIPT = (
    "在2024年的今天，如果我说去新马泰旅游，你的第一反应是什么？电诈、危险。"
    "那去欧美澳旅游呢？抢劫、偷窃、歧视。"
    "那如果我说去日韩旅游呢？汉奸、不爱国。将军的炮火已经瞄准了南边。"
    "最后所有的落点都会回到一句：出国不如逛祖国的大好河山。"
    "祖国地大物博，什么没有？国内这么大还不够你玩？"
    "发现了吗？在当下的主流叙事里，外国只有三种状态："
    "要么危险，要么歧视你，要么既危险又歧视你。"
)
TRAVEL_GROUPS = [[1, 2], [3, 4], [5, 6, 7], [8], [9, 10], [11, 12]]


def _response(groups):
    return json.dumps([{"includes_sentences": ids} for ids in groups])


class TtsSemanticAgentTests(unittest.TestCase):
    def assert_safe_complete(self, text, chunks, count=len, ceiling=110):
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(chunks))
        self.assertTrue(all(count(chunk) <= ceiling for chunk in chunks))

    def test_travel_question_answer_and_followup_remain_six_complete_groups(self):
        calls = []

        def agent(**kwargs):
            calls.append(kwargs)
            return _response(TRAVEL_GROUPS)

        chunks, source, _ = segment_indextts25_text(
            TRAVEL_SCRIPT, token_count=len, agent_enabled=True, agent_call=agent
        )
        units = split_strong_sentence_units(TRAVEL_SCRIPT)
        expected = ["".join(units[index - 1] for index in ids) for ids in TRAVEL_GROUPS]
        self.assertEqual(chunks, expected)
        self.assertEqual(source, "voice_segmentation_agent")
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(len(chunk) < 65 for chunk in chunks))
        self.assertEqual(chunks[2], "那如果我说去日韩旅游呢？汉奸、不爱国。将军的炮火已经瞄准了南边。")
        self.assert_safe_complete(TRAVEL_SCRIPT, chunks)

    def test_short_topic_groups_are_not_repacked_to_fill_the_token_budget(self):
        groups = [
            "这条公交为什么取消了？因为沿途正在修路。工人要先把地下损坏的管道换掉。",
            "那附近的学校怎么办？孩子们可以从后门进入。老师会在路口带队，家长不用绕到施工区域里。",
            "最后，我们把临时路线贴在公告栏。每个路口都安排了工作人员，任何人不清楚方向都可以先询问他们。",
        ]
        text = "".join(groups)
        self.assertGreater(len(text), 110)
        calls = []

        def agent(**kwargs):
            calls.append(kwargs)
            return _response([[1, 2, 3], [4, 5, 6], [7, 8]])

        chunks, source, _ = segment_indextts25_text(
            text, token_count=len, agent_enabled=True, agent_call=agent
        )
        self.assertEqual(chunks, groups)
        self.assertEqual(source, "voice_segmentation_agent")
        self.assertEqual(len(calls), 1)
        self.assert_safe_complete(text, chunks)

    def test_agent_prompt_prioritizes_semantics_without_target_length_filling(self):
        calls = []

        def agent(**kwargs):
            calls.append(kwargs)
            return _response(TRAVEL_GROUPS)

        segment_indextts25_text(
            TRAVEL_SCRIPT, token_count=len, agent_enabled=True, agent_call=agent
        )
        prompt = calls[0]["system_prompt"]
        self.assertIn("语义", prompt)
        self.assertIn("问答", prompt)
        self.assertIsNone(re.search(r"65\s*[-–~～]\s*105", prompt))
        self.assertNotIn("尽量落在", prompt)
        self.assertIn(str(INDEXTTS25_SEGMENT_MAX_TOKENS), prompt)

    def test_oversized_group_gets_one_semantic_revision_before_length_fallback(self):
        units = ["甲" * 50 + "。", "乙" * 30 + "。", "丙" * 30 + "。", "丁" * 10 + "。"]
        text = "".join(units)
        calls = []

        def agent(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return _response([[1, 2, 3], [4]])
            return _response([[1], [2, 3], [4]])

        chunks, source, _ = segment_indextts25_text(
            text, token_count=len, agent_enabled=True, agent_call=agent
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(chunks, [units[0], units[1] + units[2], units[3]])
        self.assertEqual(source, "voice_segmentation_agent")
        self.assertNotEqual(calls[0]["user_prompt"], calls[1]["user_prompt"])
        self.assert_safe_complete(text, chunks)

    def test_semantic_revision_is_bounded_when_agent_repeats_oversized_groups(self):
        text = "甲" * 50 + "。" + "乙" * 30 + "。" + "丙" * 30 + "。"
        calls = []

        def agent(**kwargs):
            calls.append(kwargs)
            return _response([[1, 2, 3]])

        chunks, _source, _ = segment_indextts25_text(
            text, token_count=len, agent_enabled=True, agent_call=agent
        )
        self.assertEqual(len(calls), 2)
        self.assert_safe_complete(text, chunks)

    def test_invalid_coverage_does_not_trigger_the_overlimit_revision(self):
        text = "甲" * 60 + "。" + "乙" * 60 + "。"
        calls = []

        def agent(**kwargs):
            calls.append(kwargs)
            return _response([[2], [1]])

        chunks, source, _ = segment_indextts25_text(
            text, token_count=len, agent_enabled=True, agent_call=agent
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(source, "python_fallback")
        self.assert_safe_complete(text, chunks)

    def test_actual_tokenizer_has_room_for_the_korea_followup(self):
        config = load_indextts25_config()
        vocabulary = config.model_dir / "multilingual_zh_ja_yue_char_del.tiktoken"
        if not vocabulary.is_file():
            self.skipTest("Official IndexTTS-2.5 vocabulary is not installed")
        count = build_indextts25_token_counter(config)
        units = split_strong_sentence_units(TRAVEL_SCRIPT)
        korea = "".join(units[4:7])
        old_boundary = "".join(units[:6])
        with_followup = "".join(units[:7])
        self.assertEqual(count(korea), 32)
        self.assertEqual(count(old_boundary), 72)
        self.assertEqual(count(with_followup), 85)
        self.assertLessEqual(count(with_followup), INDEXTTS25_SEGMENT_MAX_TOKENS)
        chunks, source, _ = segment_indextts25_text(
            TRAVEL_SCRIPT,
            token_count=count,
            agent_enabled=True,
            agent_call=lambda **_kwargs: _response(TRAVEL_GROUPS),
        )
        self.assertEqual(source, "voice_segmentation_agent")
        self.assertEqual(chunks[2], korea)
        self.assert_safe_complete(TRAVEL_SCRIPT, chunks, count=count)


if __name__ == "__main__":
    unittest.main()
