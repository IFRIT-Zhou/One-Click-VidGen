"""Routing is non-mutating advice, not a request to generate anything."""
import copy
import unittest
from unittest.mock import Mock, patch
from backend.app.vibe_routing import recommend_shot
from backend.app.vibe_files import publish


class VibeRoutingTests(unittest.TestCase):
    def test_route_by_visual_not_medical_topic(self):
        for intent, words, expected in [
            ('展示术式成功率的柱状图','切除肝脏','vibe'),
            ('演示手术刀沿血管移动','手术','video'),
            ('对照原文切片的比例尺','两组 35% 和 12%','static'),
            ('打开一张风景照','远望','static'),
            ('','两组反应率为15%和10%','vibe'),
        ]:
            shot={'id':'s','kind':'static','intent':intent}
            before=copy.deepcopy(shot)
            self.assertEqual(recommend_shot(shot,[{'text':words}])['route'],expected)
            self.assertEqual(before,shot)
        self.assertEqual(recommend_shot({'visual_design':{'presenter_speaking':True}})['route'],'video')

    def test_brief_windows_scan_lock_can_retry_without_reencoding(self):
        src,dest=Mock(),Mock()
        locked=PermissionError()
        locked.winerror=32
        src.rename.side_effect=[locked,None];dest.exists.return_value=False
        with patch('backend.app.vibe_files.time.sleep') as wait:
            publish(src,dest)
            self.assertEqual(wait.call_count,1)
        self.assertEqual(src.rename.call_count,2)
        dest.exists.return_value=True;src.rename.side_effect=locked
        with self.assertRaises(PermissionError):
            publish(src,dest)
