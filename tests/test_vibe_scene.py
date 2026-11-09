"""Independent model-directed compositor regression; no model/network/project writes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from PIL import Image, ImageChops
from pydantic import ValidationError

from backend.app.vibe_scene import ScenePlan, Scene, inspect_scene, render_scene
from backend.app.pipeline import ffmpeg_binary


def example():
    return {'canvas':[640,360],'background':'#FAF8F0','fade_in':.1,'fade_out':.1,
            'elements':[
                {'id':'title','type':'text','box':[28,18,580,55],'text':'研究结果 · 分组比较',
                 'font_size':30,'bold':True,'enter':.2,'exit':.1,'fill':'#143047'},
                {'id':'axis','type':'line','box':[70,100,490,190],'points':[[0,0],[0,190]],
                 'fill':'#37515F','motion':'draw','enter':.35,'exit':.1},
                {'id':'bar1','type':'rect','box':[110,140,120,150],'fill':'#1E8D9C','motion':'grow',
                 'cue':'s1','enter':.6,'exit':.1,'radius':5},
                {'id':'bar2','type':'rect','box':[270,200,120,90],'fill':'#D98951','motion':'grow',
                 'cue':'s2','enter':.45,'exit':.1,'radius':5},
                {'id':'n1','type':'number','box':[110,90,120,42],'value':50,'suffix':'%',
                 'font_size':28,'cue':'s1','enter':.6,'exit':.1,'align':'center'},
                {'id':'label','type':'text','box':[65,305,520,40],'text':'数据仅为渲染测试示例',
                 'font_size':20,'fill':'#666666','enter':.2,'exit':.1},
            ]}


class VibeSceneTests(unittest.TestCase):
    def test_native_text_graph_and_time(self):
        plan=ScenePlan.model_validate(example())
        scene=Scene(plan,{},dict(s1=.2,s2=1.0),2.5)
        self.assertEqual(scene.size,(640,360))
        self.assertEqual(scene.count,75)
        self.assertIsNotNone(ImageChops.difference(scene.frame(10),scene.frame(40)).getbbox())
        self.assertLess(inspect_scene(scene)['max_blank_seconds'],.75)

    def test_rejects_paths_code_unknown_types_and_outside_canvas(self):
        for alteration in [dict(type='html'),dict(asset_id='../secret'),dict(script='alert(1)'),
                           dict(box=[630,0,50,50]),dict(box=[0,0,-1,12]),dict(at=float('nan'))]:
            data=example();data['elements'][0].update(alteration)
            with self.subTest(alteration=alteration),self.assertRaises(ValidationError):
                ScenePlan.model_validate(data)

    def test_schema_explains_flat_elements_boxes_and_line_constraints(self):
        fields=ScenePlan.model_json_schema()['$defs']['SceneElement']['properties']
        self.assertIn('do not inherit',fields['type']['description'])
        self.assertIn('NOT [left, top, right, bottom]',fields['box']['description'])
        self.assertIn('draw is valid ONLY for line/polyline',fields['motion']['description'])
        self.assertIn('exactly 2',fields['points']['description'])
        self.assertIn('2–512',fields['points']['description'])
        self.assertIn('LOCAL to box top-left',fields['points']['description'])
        self.assertEqual(fields['role']['default'],'auto')

    def test_draw_and_points_constraints_match_schema(self):
        valid={'id':'axis','type':'line','box':[100,200,300,1],
               'points':[[0,0],[300,0]],'motion':'draw','enter':.3,'exit':.1}
        data=example();data['elements']=[valid]
        Scene(ScenePlan.model_validate(data),{},{},3)
        invalid=[dict(type='rect'),dict(points=[[0,0],[100,0],[300,0]]),
                 dict(type='polyline',points=[[0,0]]),dict(points=[[100,200],[400,200]])]
        for alteration in invalid:
            data=example();data['elements']=[{**valid,**alteration}]
            with self.subTest(alteration=alteration),self.assertRaises(ValidationError):
                ScenePlan.model_validate(data)

    def test_unknown_cues_and_overlong_entry_rejected(self):
        data=example();data['elements'][0]['cue']='unknown'
        with self.assertRaisesRegex(ValueError,'不存在'):
            Scene(ScenePlan.model_validate(data),{},dict(s1=0,s2=1),2.5)
        data=example();data['elements'][0]['enter']=3
        with self.assertRaisesRegex(ValueError,'进出场'):
            Scene(ScenePlan.model_validate(data),{},dict(s1=0,s2=1),2.5)

    def test_long_empty_intro_caught_before_encoding(self):
        data=example()
        data['elements']=[{'id':'late','type':'text','box':[20,20,500,60],'text':'不允许迟迟才出现',
                           'font_size':24,'at':3,'enter':.3,'exit':.1}]
        scene=Scene(ScenePlan.model_validate(data),{}, {},5)
        with self.assertRaisesRegex(ValueError,'没有可见内容'):
            inspect_scene(scene)

    def test_decorative_background_cannot_hide_long_empty_intro(self):
        for explicit in (False,True):
            data=example()
            decor={'id':'backplate','type':'rect','box':[0,0,640,360],
                   'fill':'#2B5278','enter':0,'exit':0,'motion':'none'}
            if explicit:
                # Explicit roles also cover smaller decorative panels that
                # cannot safely be inferred from their geometry alone.
                decor.update(box=[40,40,240,220],role='background')
            data['elements']=[decor,{'id':'late','type':'text','box':[20,20,500,60],
                'text':'实质内容不能迟到','fill':'#FFFFFF','font_size':24,'at':8,'enter':.3,'exit':.1}]
            with self.subTest(explicit=explicit),self.assertRaisesRegex(ValueError,'没有可见内容'):
                inspect_scene(Scene(ScenePlan.model_validate(data),{},{},12))

    def test_vector_only_diagram_and_explicit_large_content_are_not_blank(self):
        data=example();data['elements']=[
            {'id':'left','type':'rect','box':[60,80,180,200],'fill':'#267D8C','enter':.3,'exit':.1,'motion':'grow'},
            {'id':'right','type':'ellipse','box':[360,80,180,200],'fill':'#C27839','enter':.4,'exit':.1},
            {'id':'connection','type':'line','box':[240,180,120,1],
             'points':[[0,0],[120,0]],'fill':'#143047','motion':'draw','enter':.4,'exit':.1},
        ]
        self.assertEqual(inspect_scene(Scene(ScenePlan.model_validate(data),{},{},3))['max_blank_seconds'],0)
        data['elements']=[{'id':'large_content','type':'rect','role':'content','box':[0,0,640,360],
                           'fill':'#267D8C','enter':.3,'exit':.1,'motion':'grow'}]
        self.assertEqual(inspect_scene(Scene(ScenePlan.model_validate(data),{},{},3))['max_blank_seconds'],0)

    def test_invisible_same_colour_rejected(self):
        data=example();data['elements']=[{'id':'blank','type':'rect','box':[20,20,500,100],
                                         'fill':data['background'],'enter':0,'exit':0}]
        with self.assertRaisesRegex(ValueError,'没有可见内容'):
            inspect_scene(Scene(ScenePlan.model_validate(data),{},{},3))

    def test_overflow_not_silently_truncated(self):
        data=example();data['elements'][0].update(box=[0,0,25,20],text='这段文字放不下')
        with self.assertRaisesRegex(ValueError,'超出文本框'):
            Scene(ScenePlan.model_validate(data),{},dict(s1=0,s2=1),3)

    def test_single_wide_character_cannot_clip_horizontally(self):
        data=example();data['elements'][0].update(box=[0,0,15,100],text='宽',font_size=40)
        with self.assertRaisesRegex(ValueError,'title.*超出文本框'):
            Scene(ScenePlan.model_validate(data),{},dict(s1=0,s2=1),3)

    def test_image_preserves_aspect_ratio_and_missing_asset_fails(self):
        data=example();data['elements']=[{'id':'image','type':'image','asset_id':'original',
                                         'box':[50,50,200,200],'enter':0,'exit':0,'motion':'none'}]
        plan=ScenePlan.model_validate(data)
        with self.assertRaisesRegex(ValueError,'缺少素材'):
            Scene(plan,{},{},3)
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'source.png';Image.new('RGB',(200,100),'red').save(file)
            scene=Scene(plan,{'original':file},{},3)
            frame=scene.frame(30)
            self.assertEqual(frame.getpixel((150,70)),(250,248,240))
            self.assertEqual(frame.getpixel((150,150)),(255,0,0))
            self.assertEqual(frame.getpixel((150,230)),(250,248,240))

    def test_asset_id_is_map_key_not_path_and_alpha_not_applied_twice(self):
        data=example();data['fade_in']=data['fade_out']=0
        data['elements']=[{'id':'pic','type':'image','asset_id':'scene:original',
                           'box':[50,50,100,100],'enter':0,'exit':0,'motion':'none'}]
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'source.png';Image.new('RGBA',(100,100),(255,0,0,128)).save(file)
            scene=Scene(ScenePlan.model_validate(data),{'scene:original':file},{},3)
            red,green,blue=scene.frame(30).getpixel((75,75))
            self.assertAlmostEqual(green,124,delta=1)
            self.assertAlmostEqual(blue,120,delta=1)

    def test_polyline_grows_by_distance_and_exits(self):
        data=example();data['fade_in']=data['fade_out']=0
        data['elements']=[{'id':'curve','type':'polyline','box':[20,20,200,200],
                           'points':[[0,100],[50,50],[200,0]],'motion':'draw','enter':1,'exit':.3,
                           'end':2,'stroke':'#FF0000','stroke_width':8}]
        scene=Scene(ScenePlan.model_validate(data),{},{},3)
        self.assertIsNotNone(ImageChops.difference(scene.frame(15),scene.frame(35)).getbbox())
        self.assertIsNone(ImageChops.difference(scene.frame(70),scene.flat).getbbox())

    def test_real_encode_has_native_frame_count_and_proof(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);audio=root/'audio.wav';out=root/'out';out.mkdir()
            with wave.open(str(audio),'wb') as f:
                f.setparams((1,2,16000,0,'NONE','not compressed'));f.writeframes(b'\x18\x00'*48000)
            data=example();data['canvas']=[640,360]
            result=render_scene(plan=ScenePlan.model_validate(data),assets={},cues=dict(s1=.2,s2=1),
                audio=audio,start=0,duration=2.5,output_dir=out,ffmpeg=ffmpeg_binary(),timeout=60)
            self.assertEqual(result['frames'],75)
            self.assertEqual(result['duration_seconds'],2.5)
            self.assertGreater(len(result['proof_times']),2)
            for name in ['preview.mp4','silent.mp4']:
                self.assertIn(b'ftyp',(out/name).read_bytes()[:32])
            Image.open(out/'proof.jpg').verify()
            scene=Scene(ScenePlan.model_validate(data),{},dict(s1=.2,s2=1),2.5)
            self.assertEqual(set(result['detail_times']),{'early','middle','end'})
            self.assertAlmostEqual(result['detail_times']['early'],.5,delta=1/30)
            self.assertAlmostEqual(result['detail_times']['middle'],1.25,delta=1/30)
            self.assertAlmostEqual(result['detail_times']['end'],2.1,delta=1/30)
            for name,timestamp in result['detail_times'].items():
                with Image.open(out/f'detail_{name}.png') as image:
                    self.assertEqual(image.size,(640,360))
                    self.assertEqual(image.format,'PNG')
                    self.assertIsNone(ImageChops.difference(image,scene.frame(round(timestamp*30))).getbbox())
                    self.assertIsNotNone(ImageChops.difference(image,scene.flat).getbbox())

    def test_cancellation_prevents_encoding_and_cleans_partial_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);audio=root/'audio.wav';out=root/'out';out.mkdir()
            with wave.open(str(audio),'wb') as f:
                f.setparams((1,2,16000,0,'NONE','not compressed'));f.writeframes(b'\x00\x00'*48000)
            args=dict(plan=ScenePlan.model_validate(example()),assets={},cues=dict(s1=.2,s2=1),
                      audio=audio,start=0,duration=2.5,output_dir=out,ffmpeg='unused')
            with patch('backend.app.vibe_scene._encode_frames') as encode:
                with self.assertRaises(InterruptedError): render_scene(**args,cancelled=lambda:True)
                encode.assert_not_called()
            stopped=[False]
            def halfway(cmd,scene,deadline,proof):
                (out/'silent.mp4').write_bytes(b'partial')
                stopped[0]=True
                scene.frame(10)
            with patch('backend.app.vibe_scene._encode_frames',side_effect=halfway):
                with self.assertRaises(InterruptedError): render_scene(**args,cancelled=lambda:stopped[0])
            self.assertEqual(list(out.iterdir()),[])
            stopped[0]=False
            def encoded(cmd,scene,deadline,proof):
                (out/'silent.mp4').write_bytes(b'encoded')
                for index in sorted(proof.indices):proof.accept(index,scene.frame(index))
            def cancel_after_details(*unused):
                self.assertTrue((out/'detail_early.png').is_file())
                self.assertTrue((out/'detail_middle.png').is_file())
                self.assertTrue((out/'detail_end.png').is_file())
                stopped[0]=True
            with patch('backend.app.vibe_scene._encode_frames',side_effect=encoded), \
                 patch('backend.app.vibe_scene._mux',side_effect=cancel_after_details):
                with self.assertRaises(InterruptedError): render_scene(**args,cancelled=lambda:stopped[0])
            self.assertEqual(list(out.iterdir()),[])


if __name__=='__main__':unittest.main()
