import copy
import hashlib
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from backend.app.vibe_review_evidence import build_review_evidence


class ReviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.canvas = [640, 360]
        self.paths = {}
        for role, color, size in [('core', '#ff0000', (800,450)),
                                  ('background', '#00ff00', (800,450)),
                                  ('poster', '#0000ff', (640,360))]:
            path = self.folder/(role+'.png')
            with Image.new('RGB', size, color) as image:
                image.save(path)
            self.paths[role] = path
        self.measured = dict(poster=True, texts=[dict(id='title',text='Axis',box=[140,112,120,36],visible=True)],
                             assets=[],groups=[])
        self.source = self.source_report('core', [self.ocr('axis','Axis',[.2,.3,.4,.4])])
        self.background = self.source_report('background', [])

    def sha(self, role):
        return hashlib.sha256(self.paths[role].read_bytes()).hexdigest()

    def ocr(self, identity, text, box):
        return dict(id=identity,text=text,normalized=box,confidence=.99,
            canvas_pixels=[box[0]*640,box[1]*360,box[2]*640,box[3]*360],origin='local_ocr')

    def source_report(self, role, texts):
        return dict(image_id='image_'+role,source_sha256=self.sha(role),source_size=[800,450],
            canvas=self.canvas,status='ready',ocr=dict(status='ready',truncated=False),
            texts=texts,lines=[],boxes=[],geometry=dict(status='ready'),warnings=[])

    def build(self, **changes):
        kwargs=dict(**self.paths,canvas=self.canvas,measurements=self.measured,
            source_measurements=self.source,background_measurements=self.background,
            output_dir=self.folder/'evidence',preview_id='preview_bound')
        kwargs.update(changes)
        return build_review_evidence(**kwargs)

    def test_browser_anchor_produces_same_window_three_panel_evidence_and_measured_delta(self):
        before=copy.deepcopy((self.measured,self.source,self.background))
        hashes={role:self.sha(role) for role in self.paths}
        result=self.build(element_ids=['title'])
        report=result['report']
        self.assertTrue(report['ok'])
        self.assertFalse(report['automatic_quality_verdict'])
        self.assertEqual(len(result['images']),1)
        anchor=report['anchors'][0]
        self.assertEqual(anchor['status'],'located')
        self.assertEqual(anchor['matches'][0]['source'],'browser_dom')
        geometry=anchor['matches'][0]['text_geometry']
        self.assertEqual(geometry['delta_px']['center'],[8.,4.])
        self.assertEqual(geometry['delta_px']['edges'],dict(left=12.,top=4.,right=4.,bottom=4.))
        self.assertFalse(geometry['automatic_quality_verdict'])
        window=report['windows'][0]
        self.assertEqual([p['role'] for p in window['panels']],['core','background','poster'])
        self.assertTrue(all(p['normalized_window']==window['region'] for p in window['panels']))
        image_path=result['images'][window['image_id']]
        with Image.open(image_path) as sheet:
            self.assertLessEqual(sheet.width,2000)
            for panel,color in zip(window['panels'],[(255,0,0),(0,255,0),(0,0,255)]):
                x,y,w,h=panel['sheet_box_xywh']
                self.assertEqual(sheet.getpixel((x+w//2,y+h//2)),color)
                self.assertEqual(panel['source_sha256'],hashes[panel['role']])
        self.assertEqual(before,(self.measured,self.source,self.background))
        self.assertEqual(hashes,{role:self.sha(role) for role in self.paths})

    def test_repeated_exact_text_stays_ambiguous_even_when_model_region_prefers_one(self):
        self.source['texts']=[self.ocr('left','0',[.1,.6,.15,.65]),self.ocr('right','0',[.7,.6,.75,.65])]
        self.measured['texts']=[dict(id='left',text='0',box=[64,216,32,18]),
                                dict(id='right',text='0',box=[448,216,32,18])]
        result=self.build(text_queries=['0'],region=[.05,.55,.2,.7])
        anchor=result['report']['anchors'][0]
        self.assertEqual(anchor['core']['status'],'ambiguous')
        self.assertIsNone(anchor['core']['tool_box'])
        self.assertEqual(len(anchor['core']['candidates']),2)
        self.assertIsNone(anchor['text_geometry']['delta_px'])
        self.assertFalse(result['report']['anchors'][1]['verified'])
        self.assertEqual(len(result['images']),3)

    def test_containing_line_is_a_search_candidate_not_a_verified_substring_box(self):
        self.source['texts']=[self.ocr('long','Many patients',[.1,.2,.5,.3])]
        result=self.build(text_queries=['patients'])
        anchor=result['report']['anchors'][0]
        self.assertEqual(anchor['core']['status'],'unresolved')
        self.assertIsNone(anchor['core']['tool_box'])
        self.assertEqual(anchor['core']['candidates'][0]['method'],'containing_line_not_exact_bounds')
        self.assertIn('not_verified',result['report']['windows'][0]['region_role'])

    def test_no_anchors_does_not_invent_a_default_full_frame(self):
        for kwargs in ({},{'element_ids':['missing']},{'text_queries':['unseen text']}):
            with self.subTest(kwargs=kwargs):
                result=self.build(**kwargs)
                self.assertFalse(result['report']['ok'])
                self.assertEqual(result['images'],{})
                self.assertIn('No measured anchor',result['report']['error'])
                self.assertFalse((self.folder/'evidence').exists())

    def test_stale_ocr_is_rejected_but_dom_and_requested_window_remain_usable(self):
        self.source['source_sha256']='wrong bytes'
        self.background['source_size']=[640,360]
        result=self.build(element_ids=['title'])
        report=result['report']
        self.assertFalse(report['measurement_binding']['core']['available'])
        self.assertFalse(report['measurement_binding']['background']['available'])
        self.assertEqual(report['anchors'][0]['matches'][0]['text_geometry']['source_status'],'unresolved')
        self.assertFalse(report['windows'][0]['core_candidates']['available'])
        self.assertEqual(report['windows'][0]['core_candidates']['texts'],[])
        self.assertFalse(report['windows'][0]['background_candidates']['available'])
        self.assertTrue(report['ok'])

    def test_requested_search_window_preserves_local_geometry_without_claiming_ownership(self):
        self.source['lines']=[dict(id='core_line',image_id='image_core',normalized=[.1,.35,.9,.35],
            canvas_pixels=[64,126,576,126],verified=True,semantic_role='residual')]
        self.background['lines']=[dict(id='bg_line',image_id='image_background',normalized=[.3,.25,.3,.45],
            canvas_pixels=[192,90,192,162],verified=False)]
        self.background['boxes']=[dict(id='bg_box',normalized=[.2,.3,.4,.4],canvas_pixels=[128,108,256,144])]
        result=self.build(region=[.15,.2,.5,.5])
        window=result['report']['windows'][0]
        self.assertFalse(result['report']['anchors'][0]['verified'])
        self.assertEqual(window['core_candidates']['lines'][0]['id'],'core_line')
        self.assertEqual(window['background_candidates']['lines'][0]['id'],'bg_line')
        self.assertEqual(window['core_candidates']['lines'][0]['semantic_role'],'unassigned')
        self.assertFalse(window['core_candidates']['lines'][0]['verified'])
        self.assertEqual(window['background_candidates']['boxes'][0]['id'],'bg_box')
        self.assertEqual(window['browser_elements'][0]['id'],'title')
        self.assertEqual(window['browser_elements'][0]['box'],[140,112,120,36])
        self.assertFalse(window['automatic_quality_verdict'])

    def test_window_limit_keeps_omitted_anchor_information_and_safe_output_names(self):
        self.measured['texts']=[dict(id=f'element_{i}',text=f'T{i}',box=[20+i*140,100,60,30]) for i in range(4)]
        result=self.build(element_ids=[f'element_{i}' for i in range(4)],preview_id='../not-a-path')
        self.assertEqual(len(result['images']),3)
        self.assertEqual(len(result['report']['anchors']),4)
        self.assertEqual(result['report']['omitted_window_count'],1)
        self.assertTrue(result['report']['warnings'])
        for path in result['images'].values():
            self.assertEqual(path.parent,self.folder/'evidence')
            self.assertTrue(path.name.startswith('review_evidence_'))

    def test_hidden_or_unknown_browser_elements_are_not_guessed(self):
        self.measured['texts'][0]['visible']=False
        result=self.build(element_ids=['title'])
        self.assertFalse(result['report']['ok'])
        self.assertEqual(result['report']['anchors'][0]['status'],'unresolved')
        self.assertEqual(result['images'],{})

    def test_duplicate_browser_ids_remain_ambiguous(self):
        self.measured['texts'].append(dict(id='title',text='Axis',box=[400,200,120,36]))
        result=self.build(element_ids=['title'])
        self.assertEqual(result['report']['anchors'][0]['status'],'ambiguous')
        self.assertEqual(len(result['report']['anchors'][0]['matches']),2)

    def test_invalid_regions_and_non_poster_measurements_fail_without_output(self):
        for region in ([0,0,640,360],[-.1,0,1,1],[.8,.2,.1,.4],[0,0,0,1],
                       [0,0,1,math.nan],[False,0,1,1],[0,1,1]):
            with self.subTest(region=region),self.assertRaises(ValueError):
                self.build(region=region)
        with self.assertRaisesRegex(ValueError,'keyframe'):
            self.build(region=[0,0,1,1],measurements=dict(poster=False,texts=[]))
        self.assertFalse((self.folder/'evidence').exists())

    def test_dimension_mismatch_decode_budget_and_animated_input_are_rejected(self):
        with patch('backend.app.vibe_review_evidence.MAX_TOTAL_INPUT_PIXELS',1):
            with self.assertRaisesRegex(ValueError,'aggregate decode'):
                self.build(element_ids=['title'])
        with patch('backend.app.vibe_review_evidence.MAX_INPUT_PIXELS',1):
            with self.assertRaisesRegex(ValueError,'invalid dimensions'):
                self.build(element_ids=['title'])
        bad=self.folder/'square.png'
        with Image.new('RGB',(100,100),'black') as image:
            image.save(bad)
        with self.assertRaisesRegex(ValueError,'aspect ratios'):
            self.build(element_ids=['title'],background=bad)
        animated=self.folder/'animated.gif'
        with Image.new('RGB',(80,45),'red') as first, Image.new('RGB',(80,45),'blue') as second:
            first.save(animated,save_all=True,append_images=[second])
        with self.assertRaisesRegex(ValueError,'multiple frames'):
            self.build(element_ids=['title'],background=animated)
        self.assertFalse((self.folder/'evidence').exists())


if __name__ == '__main__':
    unittest.main()
