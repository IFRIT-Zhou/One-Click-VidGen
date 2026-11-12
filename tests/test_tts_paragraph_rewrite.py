"""Full paragraph revision must not masquerade as deleted picture units."""
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from backend.app.tts_editor import TtsEditor, _redistribute_subtitle_paragraph
from backend.app.visual_editor import VisualEditor


class ParagraphRewriteTests(unittest.TestCase):
    def test_audio_subtitle_commit_uses_resolved_working_copy_not_video_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            working, export = root / 'working', root / 'video_export'
            for folder in (working, export):
                (folder / 'other').mkdir(parents=True)
            timeline = working / 'other/画面时间线.json'
            timeline.write_text(json.dumps([dict(slide_id='scene_046', start=0, end=1,
                                                  text_content='旧字幕')]), encoding='utf-8')
            other = export / 'other/画面时间线.json'
            other.write_text('[]', encoding='utf-8')
            editor = VisualEditor()
            with patch.object(editor, 'output_dir', return_value=export) as resolve, \
                 patch.object(editor, 'inspect', side_effect=AssertionError('wrong-folder inspection')):
                result = editor.save_subtitle_texts(job_id='job', user_id=1,
                    updates={'scene_046': '更新字幕'}, _project_dir=working)
                same = editor.save_subtitle_texts(job_id='job', user_id=1,
                    updates={'scene_046': '更新字幕'}, _project_dir=working)
            resolve.assert_not_called()
            self.assertEqual(result, dict(ok=True, changed=1))
            self.assertEqual(same, dict(ok=True, changed=0))
            self.assertEqual(json.loads(timeline.read_text(encoding='utf-8'))[0]['text_content'], '更新字幕')
            self.assertEqual(other.read_text(encoding='utf-8'), '[]')

    def updates(self, parts, text):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'other').mkdir()
            rows = [dict(slide_id=f's{i}', start=i, end=i+1, text_content=part)
                    for i, part in enumerate(parts)]
            path = root / 'other/画面时间线.json'
            path.write_text(json.dumps(rows), encoding='utf-8')
            before = path.read_bytes()
            result = TtsEditor._subtitle_updates_for_segments(root,
                [dict(index=13, start=0, end=len(parts))], {13: text})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(result), [row['slide_id'] for row in rows])
            self.assertEqual(''.join(result.values()), text)
            self.assertTrue(all(any(c.isalnum() for c in value) for value in result.values()))
            return result

    def test_deleted_clause_is_reflowed_not_rejected(self):
        self.updates(['团队指出，', '这更可能与肥胖相关的胰岛素抵抗，', '以及瘦素等因子有关。',
                      '它们可能重塑微环境并诱导耐药。'],
                     '团队在讨论中提出，肥胖相关的代谢和肿瘤微环境改变，可能参与了这种病理反应差异。'
                     '不过，本研究没有直接验证这些机制，具体原因仍需进一步研究。')

    def test_delete_entire_old_cue_keeps_picture_ids(self):
        self.updates(['前文保持不变。', '这整句话需要删除。', '结尾保持不变。'],
                     '前文保持不变。结尾保持不变。')

    def test_minor_edit_preserves_existing_cue_boundaries(self):
        result = self.updates(['张教授介绍研究。', '感谢李教授团队。'],
                              '张医生介绍研究。感谢李医生团队。')
        self.assertEqual(list(result.values()), ['张医生介绍研究。', '感谢李医生团队。'])

    def test_numbers_and_latin_words_remain_whole(self):
        text = 'BMI分组显示12.2%和11.5%，差异没有统计学意义。'
        parts = _redistribute_subtitle_paragraph(text, ['一段旧字幕。'] * 4)
        self.assertEqual(''.join(parts), text)
        for token in ('BMI', '12.2', '11.5'):
            self.assertEqual(sum(token in part for part in parts), 1)

    def test_too_short_does_not_duplicate_or_invent_words(self):
        with self.assertRaisesRegex(ValueError, '新文案过短'):
            _redistribute_subtitle_paragraph('好。', ['甲。', '乙。'])
