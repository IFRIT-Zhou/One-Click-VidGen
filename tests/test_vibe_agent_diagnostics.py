import tempfile
import unittest
from pathlib import Path
from PIL import Image
from backend.app.vibe_agent_diagnostics import alpha_evidence, json_feedback, iteration_guidance


class DiagnosticsTests(unittest.TestCase):
    def test_json_reports_exact_error_in_middle_not_only_tail(self):
        text='{"arguments":{"inventory":{}}},"subjects":[]}'
        evidence=json_feedback(text)
        self.assertEqual(evidence['message'],'Extra data')
        self.assertEqual(evidence['excerpt'][evidence['caret_offset']],',')
        self.assertGreater(evidence['column'],1)
        self.assertEqual(json_feedback('[]')['kind'],'not_object')

    def test_alpha_counts_hidden_rgb_as_transparent_not_blue_background(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'a.png'
            image=Image.new('RGBA',(10,10),(0,100,255,0))
            for x in range(2,8):
                for y in range(2,8):image.putpixel((x,y),(80,40,20,255))
            image.save(path)
            before=path.read_bytes()
            result=alpha_evidence(path)
            self.assertEqual(result['transparent_fraction'],0.64)
            self.assertEqual(result['visible_bbox_px'],[2,2,8,8])
            self.assertEqual(result['edge_contact'], dict(left=0, right=0, top=0, bottom=0))
            self.assertEqual(path.read_bytes(),before)

    def test_opaque_and_empty_are_distinguished(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'a.png'
            Image.new('RGB',(8,8),'blue').save(path)
            self.assertEqual(alpha_evidence(path)['transparent_fraction'],0)
            self.assertEqual(alpha_evidence(path)['edge_contact'], dict(left=1, right=1, top=1, bottom=1))
            Image.new('RGBA',(8,8),(0,0,0,0)).save(path)
            self.assertIsNone(alpha_evidence(path)['visible_bbox_px'])

    def test_advisory_resets_after_new_material_or_evidence(self):
        events=[dict(tool='preview_scene') for _ in range(3)]
        self.assertTrue(iteration_guidance(events)['advisory_only'])
        self.assertIsNone(iteration_guidance(events+[dict(tool='prepare_subject')]))
        self.assertIsNone(iteration_guidance(events+[dict(tool='review_evidence')]))
