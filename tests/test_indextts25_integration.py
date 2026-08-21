from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from backend.app.main import GenerateRequest
from module1_agent_director import (
    _build_indextts25_command,
    split_cluster_tts_text,
    step1_indextts25_raw_input,
)
from backend.app.tts_segmentation import segment_indextts25_text


class IndexTTS25IntegrationTests(unittest.TestCase):
    def test_request_schema_accepts_test_engine(self):
        self.assertEqual(GenerateRequest(tts_engine="indextts25").tts_engine, "indextts25")

    def test_25_command_uses_isolated_runner_and_native_speed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = SimpleNamespace(
                python=root / "python.exe",
                device="cuda:0",
                language="ZH",
                use_bf16=True,
                use_accel=False,
                use_torch_compile=False,
                emotion_weight=0.65,
            )
            command = _build_indextts25_command(
                config,
                manifest=root / "batch.jsonl",
                output_dir=root / "output",
                output_prefix="chunk",
                voice_path=root / "voice.wav",
                emotion_vector="0.8,0,0,0,0,0,0,0",
                emotion_weight=0.9,
                speed=2.0,
            )
        self.assertTrue(any(value.endswith("indextts25_runner.py") for value in command))
        self.assertIn("--bf16", command)
        self.assertIn("--no-accel", command)
        factor_index = command.index("--duration-factor") + 1
        self.assertAlmostEqual(float(command[factor_index]), 0.5)
        emotion_weight_index = command.index("--emotion-weight") + 1
        self.assertAlmostEqual(float(command[emotion_weight_index]), 0.9)

    def test_request_schema_accepts_zero_emotion_weight(self):
        self.assertEqual(GenerateRequest(tts_emotion_weight=0).tts_emotion_weight, 0)

    def test_25_short_text_skips_agent_and_stays_one_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            text_path = Path(temporary) / "sample.txt"
            text_path.write_text("第一句很短。\n第二句明显更长，但仍应作为同一个2.5基准任务。", encoding="utf-8")
            chunks = step1_indextts25_raw_input(text_path)
        self.assertEqual(chunks, ["第一句很短。\n第二句明显更长，但仍应作为同一个2.5基准任务。"])

    def test_25_agent_groups_contiguous_complete_sentences(self):
        text = "甲" * 40 + "。" + "乙" * 40 + "。" + "丙" * 40 + "。"
        calls = []

        def fake_agent(**kwargs):
            calls.append(kwargs)
            return '[{"includes_sentences":[1,2]},{"includes_sentences":[3]}]'

        chunks, source, total = segment_indextts25_text(
            text,
            max_tokens=110,
            token_count=len,
            agent_enabled=True,
            agent_call=fake_agent,
        )
        self.assertEqual(source, "voice_segmentation_agent")
        self.assertEqual(total, len(text))
        self.assertEqual(chunks, ["甲" * 40 + "。" + "乙" * 40 + "。", "丙" * 40 + "。"])
        self.assertEqual(len(calls), 1)

    def test_25_invalid_agent_result_uses_python_fallback(self):
        text = "甲" * 60 + "。" + "乙" * 60 + "。"

        def invalid_agent(**_kwargs):
            return '[{"includes_sentences":[2]},{"includes_sentences":[1]}]'

        chunks, source, _ = segment_indextts25_text(
            text,
            max_tokens=110,
            token_count=len,
            agent_enabled=True,
            agent_call=invalid_agent,
        )
        self.assertEqual(source, "python_fallback")
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk) <= 110 for chunk in chunks))

    def test_25_oversized_agent_group_is_locally_clamped_without_losing_agent_plan(self):
        text = "甲" * 45 + "。" + "乙" * 45 + "。" + "丙" * 45 + "。"

        def oversized_agent(**_kwargs):
            return '[{"includes_sentences":[1,2,3]}]'

        chunks, source, _ = segment_indextts25_text(
            text,
            max_tokens=110,
            token_count=len,
            agent_enabled=True,
            agent_call=oversized_agent,
        )
        self.assertEqual(source, "voice_segmentation_agent")
        self.assertEqual(chunks, ["甲" * 45 + "。" + "乙" * 45 + "。", "丙" * 45 + "。"])
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk) <= 110 for chunk in chunks))

    def test_25_python_guard_prefers_full_stop_over_comma(self):
        text = "甲" * 35 + "，" + "乙" * 35 + "。" + "丙" * 35 + "，" + "丁" * 35 + "。"
        chunks, source, _ = segment_indextts25_text(
            text,
            max_tokens=90,
            token_count=len,
            agent_enabled=False,
        )
        self.assertEqual(source, "python_fallback")
        self.assertEqual(chunks[0], "甲" * 35 + "，" + "乙" * 35 + "。")
        self.assertEqual("".join(chunks), text)

    def test_25_python_guard_uses_comma_only_for_overlong_sentence(self):
        text = "甲" * 70 + "，" + "乙" * 70 + "。"
        chunks, _, _ = segment_indextts25_text(
            text,
            max_tokens=110,
            token_count=len,
            agent_enabled=False,
        )
        self.assertEqual(chunks[0], "甲" * 70 + "，")
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk) <= 110 for chunk in chunks))

    def test_25_real_tokenizer_enforces_official_110_token_limit(self):
        text = ("ATP是细胞可以直接使用的能量货币，它连接着生命活动与能量转换。" * 20)
        chunks, _, total = segment_indextts25_text(text, agent_enabled=False)
        self.assertGreater(total, 110)
        self.assertEqual("".join(chunks), text)
        # Re-entering the function with each chunk must classify it as short.
        for chunk in chunks:
            single, source, token_total = segment_indextts25_text(chunk, agent_enabled=False)
            self.assertEqual(single, [chunk])
            self.assertEqual(source, "short_text")
            self.assertLessEqual(token_total, 110)

    def test_cluster_chunker_remains_stable(self):
        text = "第一段用于验证集群断句逻辑保持不变，并且不会受到本地二点五模式的影响。" * 4
        chunks = split_cluster_tts_text(text)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), text)


if __name__ == "__main__":
    unittest.main()
