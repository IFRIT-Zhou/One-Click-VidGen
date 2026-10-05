import json
import os
import unittest
from unittest.mock import patch

from backend.app.reference_materials import required_every_shot_labels
import module4_video_render as visual


class EveryShotWordingTests(unittest.TestCase):
    def rows(self, description):
        return [dict(label='图1', kind='unknown', description='这是讲解员，' + description)]

    def test_natural_universal_wording(self):
        for note in ['需要在每一张分镜中出现', '每张分镜都有她', '每个分镜必须出现',
                     '每幅画面都使用', '所有分镜都出现', '全部镜头都保留',
                     '全程出镜', '贯穿全片出现', '每张图都要有她',
                     '每一张图片中出现', '每个镜头必须出现']:
            with self.subTest(note=note):
                self.assertEqual(required_every_shot_labels(self.rows(note), characters_only=True), ['图1'])

    def test_negation_and_partial_use_not_forced(self):
        for note in ['不要每张分镜都出现', '每个镜头不必出现', '不需要全程出镜',
                     '不是每张图都要有她', '仅在部分镜头出现', '只在开头出镜',
                     '每张分镜都出现，但只在开头出镜', '每张分镜都不要使用',
                     '全程出镜不是必须的', '每个镜头可以出现', '每张分镜不一定出现',
                     '每个镜头可以出现，但不是每个镜头必须出现']:
            with self.subTest(note=note):
                self.assertEqual(required_every_shot_labels(self.rows(note)), [])

    def test_unrelated_negative_instruction_keeps_requirement(self):
        self.assertEqual(required_every_shot_labels(self.rows('每张分镜都出现，不要改变服装')), ['图1'])

    def test_original_customer_note_forces_binding_in_both_director_modes(self):
        rows = self.rows('需要在每一张分镜中出现。')
        for mode in ['stable', 'enhanced_beta']:
            with self.subTest(mode=mode), patch.dict(os.environ, {
                'DIRECTOR_STRATEGY': mode,
                'USER_REFERENCE_IMAGE_PATHS_JSON': '["presenter.png"]',
                'USER_REFERENCE_IMAGE_METADATA_JSON': json.dumps(rows, ensure_ascii=False),
            }):
                self.assertEqual(visual._synchronized_reference_image_ids(
                    {'reference_image_ids': []}, '普通科普画面', '普通科普画面', {}, []), ['图1'])
                self.assertIn('所有镜头必须选择这些编号', visual._reference_image_instruction())


if __name__ == '__main__':
    unittest.main()
