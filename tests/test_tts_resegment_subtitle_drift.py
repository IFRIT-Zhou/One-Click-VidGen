import json
import tempfile
import unittest
from pathlib import Path

from backend.app.tts_editor import TtsEditor, _srt_entries, _write_srt_entries


class TtsResegmentSubtitleDriftTest(unittest.TestCase):
    def test_manual_split_replaces_stale_asr_times_with_audio_times(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / 'other').mkdir()
            subtitle = project / 'other' / '最终字幕.srt'
            _write_srt_entries(subtitle, [
                {'text': '上一句。', 'start': 0, 'end': 5.5},
                {'text': '第一部分，第二部分。', 'start': 5.5, 'end': 12.0},
                {'text': '下一句。', 'start': 12.0, 'end': 16.0},
            ])
            timeline = [{'text_content': e['text'], 'start': e['start'], 'end': e['end']} for e in _srt_entries(subtitle)]
            (project / 'other' / '画面时间线.json').write_text(json.dumps(timeline, ensure_ascii=False), encoding='utf-8')
            TtsEditor._reshape_subtitles_for_span(project, 5.0, 11.0,
                [{'start': 5.0, 'end': 8.0}, {'start': 8.0, 'end': 10.0}], ['第一部分，', '第二部分。'])
            TtsEditor._warp_timeline_span(project, 5.0, 11.0, 10.0)
            TtsEditor._adopt_resegmented_subtitles(project)
            entries = _srt_entries(subtitle)
            self.assertEqual([(e['start'], e['end']) for e in entries], [(0, 5), (5, 8), (8, 10), (11, 15)])
            updated = json.loads((project / 'other' / '画面时间线.json').read_text(encoding='utf-8'))
            self.assertEqual([r['text_content'] for r in updated], [e['text'] for e in entries])

    def test_neighboring_caption_is_preserved_when_asr_boundary_drifts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            other = project / "other"
            other.mkdir()
            subtitle = other / "最终字幕.srt"
            _write_srt_entries(subtitle, [
                {"text": "上一句。", "start": 59.0, "end": 63.22},
                {"text": "第一部分，", "start": 63.22, "end": 70.0},
                {"text": "第二部分。", "start": 70.0, "end": 82.10},
                {"text": "下一句。", "start": 82.10, "end": 86.0},
            ])
            timeline = [
                {"text_content": entry["text"], "start": entry["start"], "end": entry["end"]}
                for entry in _srt_entries(subtitle)
            ]
            (other / "画面时间线.json").write_text(json.dumps(timeline, ensure_ascii=False), encoding="utf-8")
            parts = [{"start": 63.193, "end": 70.1}, {"start": 70.1, "end": 82.3}]
            TtsEditor._reshape_subtitles_for_span(
                project, 63.193, 82.0825, parts, ["第一部分，", "第二部分。"]
            )
            TtsEditor._warp_timeline_span(project, 63.193, 82.0825, 82.3)
            TtsEditor._adopt_resegmented_subtitles(project)
            captions = _srt_entries(subtitle)
            self.assertEqual([entry["text"] for entry in captions],
                             ["上一句。", "第一部分，", "第二部分。", "下一句。"])
            self.assertLessEqual(captions[0]["end"], captions[1]["start"])
            updated = json.loads((other / "画面时间线.json").read_text(encoding="utf-8"))
            self.assertEqual([row["text_content"] for row in updated], [entry["text"] for entry in captions])


if __name__ == "__main__":
    unittest.main()
