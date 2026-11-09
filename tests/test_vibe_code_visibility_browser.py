"""Opt-in real Chromium geometry regression; synthetic assets, no cloud/project writes.

Run OCV_TEST_REAL_CODE_VISIBILITY=1 with the bundled renderer installed.
"""
import os
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from backend.app import vibe_code_renderer as renderer


@unittest.skipUnless(os.environ.get('OCV_TEST_REAL_CODE_VISIBILITY') == '1',
                     'requires installed local Node/Chrome renderer')
class RealGeometryVisibilityTest(unittest.TestCase):
    def test_glyph_ink_separates_metric_overlap_but_keeps_real_collisions(self):
        """Mixed-size date labels must not inherit the numeral's empty ascenders."""
        with tempfile.TemporaryDirectory(prefix='ocv-code-text-ink-') as temporary:
            root=Path(temporary)
            background=root/'background.png'
            Image.new('RGB',(1000,700),'#102030').save(background)
            output=root/'preview'
            output.mkdir()
            code='''function Scene(ctx) {
              return <div style={{position:'absolute',inset:0}}>
                <Text id="prefix" style={{position:'absolute',left:200,top:100,fontSize:24,lineHeight:1}}>第</Text>
                <Text id="number" style={{position:'absolute',left:150,top:124,fontSize:100,fontWeight:700,lineHeight:0.95}}>37</Text>
                <Text id="suffix" style={{position:'absolute',left:200,top:219,fontSize:24,lineHeight:1}}>天</Text>
                <Text id="collision_a" style={{position:'absolute',left:600,top:100,fontSize:70}}>碰撞</Text>
                <Text id="collision_b" style={{position:'absolute',left:630,top:110,fontSize:70}}>碰撞</Text>
                <div style={{position:'absolute',left:20,top:350,transform:'scale(0.75)',transformOrigin:'top left'}}>
                  <Text id="scaled" style={{position:'absolute',left:0,top:0,fontSize:40,width:180}}>多行文字测试换行</Text>
                </div>
                <Text id="clipped" style={{position:'absolute',left:970,top:400,fontSize:40}}>不可出界</Text>
              </div>;
            }'''
            metrics=renderer.run(mode='preview',code=code,canvas=[1000,700],duration=2,
                assets={'background':background},geometry={},output_dir=output)
            measured=metrics['measurements']
            rows={row['id']:row for row in measured['texts']}
            for name in ('prefix','number','suffix','collision_a','collision_b','scaled'):
                self.assertTrue(rows[name]['ink_rects'], name)
            def overlaps(first, second, field):
                return any(min(a['right'],b['right']) > max(a['left'],b['left'])
                    and min(a['bottom'],b['bottom']) > max(a['top'],b['top'])
                    for a in rows[first][field] for b in rows[second][field])
            self.assertTrue(overlaps('prefix','number','rects'))
            self.assertTrue(overlaps('suffix','number','rects'))
            self.assertFalse(overlaps('prefix','number','ink_rects'))
            self.assertFalse(overlaps('suffix','number','ink_rects'))
            collisions=[item for item in measured['diagnostics'] if item['kind']=='text_overlap']
            self.assertEqual({frozenset((item['target'],item['other'])) for item in collisions},
                             {frozenset(('collision_a','collision_b'))})
            self.assertTrue(any(item['kind']=='text_outside_canvas' and item['target']=='clipped'
                                for item in measured['diagnostics']))

    def test_canvas_overflow_nested_layout_and_source_offsets(self):
        with tempfile.TemporaryDirectory(prefix='ocv-code-visibility-') as temporary:
            root=Path(temporary)
            background=root/'background.png'
            subject=root/'subject.png'
            Image.new('RGB',(640,360),'#102030').save(background)
            Image.new('RGBA',(80,80),'#77ddffff').save(subject)
            output=root/'preview'
            output.mkdir()
            assets={'background':background,**{f'subject_{i}':subject for i in range(8)}}
            geometry={f'subject_{i}':dict(prepared_size=[80,80],anchor_available=False) for i in range(8)}
            geometry['subject_4'].update(anchor_available=True,box=[300,30,60,60])
            geometry['subject_5'].update(anchor_available=True,box=[400,200,80,80])
            code='''function Scene(ctx) {
              const enteringX = ctx.poster || ctx.frame >= ctx.fps ? 10 : -90;
              return <div style={{position:'absolute',inset:0}}>
                <Text id="title" style={{position:'absolute',left:20,top:10,fontSize:24}}>Geometry test</Text>
                <Asset id="subject_0" box={[660,20,40,40]} />
                <div id="clip_parent" style={{position:'absolute',left:110,top:60,width:60,height:50,overflow:'hidden'}}>
                  <Asset id="subject_1" box={[80,0,40,40]} />
                </div>
                <div id="partial_parent" style={{position:'absolute',left:10,top:160,width:40,height:40,overflow:'hidden'}}>
                  <Asset id="subject_2" box={[20,5,40,30]} />
                </div>
                <div id="manual_parent" style={{position:'absolute',left:300,top:140,width:100,height:100}}>
                  <Asset id="subject_3" anchor="manual" box={[5,5,40,40]} />
                </div>
                <div id="shifted_source_parent" style={{position:'absolute',left:380,top:0,width:100,height:100}}>
                  <Asset id="subject_4" anchor="source" />
                </div>
                <Group id="source_group" at={0} enter={0.3} motion="fade">
                  <Asset id="subject_5" anchor="source" />
                </Group>
                <div id="overflow_visible_parent" style={{position:'absolute',left:120,top:180,width:20,height:20,overflow:'visible'}}>
                  <Asset id="subject_6" box={[0,0,80,80]} />
                </div>
                <Asset id="subject_7" box={[enteringX,280,60,60]} />
              </div>;
            }'''
            metrics=renderer.run(mode='preview',code=code,canvas=[640,360],duration=3,
                assets=assets,geometry=geometry,output_dir=output)
            measured=metrics['measurements']
            rows={row['id']:row for row in measured['assets']}
            self.assertFalse(rows['subject_0']['visible'])
            self.assertEqual(rows['subject_0']['geometry_visibility']['state'],'outside_canvas')
            self.assertFalse(rows['subject_1']['visible'])
            self.assertEqual(rows['subject_1']['geometry_visibility']['state'],'clipped')
            self.assertEqual(rows['subject_1']['geometry_visibility']['clipped_by'][0]['target'],'clip_parent')
            self.assertTrue(rows['subject_2']['visible'])
            self.assertEqual(rows['subject_2']['geometry_visibility']['state'],'partial')
            self.assertAlmostEqual(rows['subject_2']['geometry_visibility']['visible_fraction'],.5)
            self.assertEqual(rows['subject_3']['box'],[305,145,40,40])
            self.assertTrue(rows['subject_3']['visible'])
            self.assertFalse(rows['subject_4']['visible'])
            self.assertEqual(rows['subject_4']['source_anchor']['delta'],[380,0,0,0])
            self.assertIn('anchor="manual"',rows['subject_4']['source_anchor']['hint'])
            self.assertTrue(rows['subject_5']['visible'])
            self.assertFalse(rows['subject_5']['source_anchor']['displaced'])
            self.assertTrue(rows['subject_6']['visible'])
            self.assertEqual(rows['subject_6']['geometry_visibility']['state'],'inside')
            self.assertTrue(rows['subject_7']['visible'])
            early=next(frame for frame in measured['frames'] if frame['id']=='early')
            self.assertFalse(next(row for row in early['assets'] if row['id']=='subject_7')['visible'])
            warning=next(item for item in early['diagnostics'] if item.get('target')=='subject_7')
            self.assertEqual((warning['kind'],warning['severity']),('asset_transient_clip','warning'))
            hold=next(frame for frame in measured['frames'] if frame['id']=='hold')
            self.assertTrue(next(row for row in hold['assets'] if row['id']=='subject_7')['visible'])
            self.assertTrue((output/'poster.png').is_file())


if __name__ == '__main__':
    unittest.main()
