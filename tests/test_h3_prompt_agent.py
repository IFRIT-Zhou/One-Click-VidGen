import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from backend.app import h3_prompt_agent as agent


def prompt(duration=8):
    detail = (
        "[Shot 1] [00:00-00:04] <Picture 1> anchors the opening composition. "
        "The presenter turns toward the audience while the camera slowly moves closer. "
        "The audience raises several hands in a staggered rhythm and keeps the same simple drawn style. "
        f"[Shot 2] [00:04-00:{duration:02d}] The presenter acknowledges one audience member; "
        "the remaining people settle naturally and the camera holds the final readable composition. "
    )
    return f"""subject_definitions:
<Picture 1> is the core storyboard image and composition anchor.
<Subject 1> is the presenter defined by <Picture 1>.

summary:
[reference generation] Create a short explanatory scene from <Picture 1>.

retention_analysis:
<Picture 1> is partially preserved for composition, style, and subject placement.
<Subject 1> is fully preserved in appearance.

detailed_description:
{detail * 3}

overall_soundscape:
Soft room ambience and restrained clothing movement only, with no speech.

non_diegetic_music:
N/A"""


def shot():
    return {
        "intent": "观众向讲解员踊跃提问",
        "action": "观众陆续举手，讲解员示意一人提问",
        "image_prompt": "讲解员站在讲台中央，观众围坐",
        "video_prompt": "图1是核心分镜图。观众陆续举手，讲解员示意其中一人提问。",
    }


class H3PromptAgentTest(unittest.TestCase):
    def test_official_six_section_contract_and_cache(self):
        calls = []
        fake = lambda **kwargs: calls.append(kwargs) or json.dumps({"h3_prompt": prompt()})
        with patch.object(agent, "generate_gemini_text", side_effect=fake):
            converted, fingerprint = agent.convert_for_h3(shot(), 8)
            self.assertTrue(converted.startswith("subject_definitions:"))
            self.assertIn("non_diegetic_music:", converted)
            self.assertEqual(calls[0]["json_root"], "object")
            cached = {**shot(), "h3_prompt": converted, "h3_prompt_source": fingerprint}
            again, same = agent.convert_for_h3(cached, 8)
        self.assertEqual(again, converted)
        self.assertEqual(same, fingerprint)
        self.assertEqual(len(calls), 1)

    def test_missing_section_and_timeline_are_repaired(self):
        malformed = prompt().replace("subject_definitions:", "x:", 1)
        restored = agent._validate(malformed, 8)
        self.assertIn("subject_definitions:", restored)
        self.assertIn("<Picture 1>", restored)
        repaired = agent._validate(prompt(9), 8)
        self.assertIn("[00:04-00:08]", repaired)
        self.assertNotIn("[00:04-00:09]", repaired)

    def test_source_fingerprint_changes_with_generic_prompt(self):
        original = agent.source_fingerprint(shot(), 8)
        changed = shot()
        changed["video_prompt"] += " 镜头保持稳定。"
        self.assertNotEqual(agent.source_fingerprint(changed, 8), original)

    def test_core_storyboard_pixels_are_sent_and_invalidate_cache(self):
        calls = []
        with tempfile.TemporaryDirectory() as folder:
            image_path = Path(folder) / 'core.png'
            Image.new('RGB', (24, 16), 'red').save(image_path)
            with patch.object(agent, 'generate_gemini_text', side_effect=lambda **kwargs: calls.append(kwargs) or json.dumps({'h3_prompt': prompt()})):
                _, first = agent.convert_for_h3(shot(), 8, image_path=image_path)
            self.assertEqual(calls[0]['image_data']['mime_type'], 'image/jpeg')
            payload = json.loads(calls[0]['user_prompt'])
            self.assertTrue(payload['core_storyboard_image_attached'])
            self.assertEqual(len(payload['core_storyboard_image_sha256']), 64)
            Image.new('RGB', (24, 16), 'blue').save(image_path)
            _, digest = agent._image_data(image_path)
            second = agent.source_fingerprint(shot(), 8, image_digest=digest)
            self.assertNotEqual(first, second)

    def test_missing_picture_label_is_repaired_without_another_agent_call(self):
        missing = prompt().replace("<Picture 1>", "the supplied core storyboard image")
        repaired = agent._validate(missing, 8)
        self.assertIn("<Picture 1>", repaired)
        for section in ("summary", "retention_analysis", "detailed_description"):
            start, end = agent._section_bounds(repaired)[section]
            self.assertIn("<Picture 1>", repaired[start:end])

    def test_common_image_label_alias_is_canonicalized(self):
        aliased = prompt().replace("<Picture 1>", "<Image 1>")
        repaired = agent._validate(aliased, 8)
        self.assertIn("<Picture 1>", repaired)
        self.assertNotIn("<Image 1>", repaired)

    def test_decimal_or_missing_timeline_is_normalized_without_retry(self):
        decimal = prompt().replace("[00:04-00:08]", "[00:04–00:11.5]")
        self.assertIn("[00:04-00:12]", agent._validate(decimal, 12))
        missing = agent._validate(prompt().replace("[00:00-00:04]", "opening stage").replace("[00:04-00:08]", "closing stage"), 12)
        self.assertIn("[00:00-00:12]", missing)

    def test_converter_declares_structured_visible_text_as_authoritative(self):
        calls = []
        current = shot()
        current['motion_plan'] = {
            'beats': [{'action': '观众摇头', 'texts': [
                {'text': '危险', 'owner': '观众', 'container': '对话气泡'}]}],
            'reference_texts': [],
        }
        with patch.object(agent, 'generate_gemini_text', side_effect=lambda **kwargs: calls.append(kwargs) or json.dumps({'h3_prompt': prompt()})):
            agent.convert_for_h3(current, 8)
        payload = json.loads(calls[0]['user_prompt'])
        self.assertEqual(payload['approved_visible_texts'], ['危险'])
        self.assertIn('authoritative', calls[0]['system_prompt'])

    def test_reference_audio_is_declared_and_changes_cache_identity(self):
        calls = []
        with patch.object(agent, 'generate_gemini_text', side_effect=lambda **kwargs: calls.append(kwargs) or json.dumps({'h3_prompt': prompt()})):
            _, fingerprint = agent.convert_for_h3(shot(), 8, reference_audio=True)
        payload = json.loads(calls[0]['user_prompt'])
        self.assertTrue(payload['reference_audio_supplied'])
        self.assertEqual(payload['reference_assets'][-1]['label'], '<Audio 1>')
        self.assertNotEqual(fingerprint, agent.source_fingerprint(shot(), 8, reference_audio=False))
        self.assertIn('cadence', calls[0]['system_prompt'])


if __name__ == "__main__":
    unittest.main()
