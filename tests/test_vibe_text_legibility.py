"""Synthetic layouts use arbitrary subjects/sides/colours; no private media."""
import copy
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw
try:
    from tools.vibe_text_legibility import protect_text_layers
except ImportError:
    raise unittest.SkipTest('Private text-layer prototype is not distributed; production checks have separate tests')
from backend.app.vibe_text_legibility import _project, _text_roi


class TextLegibilityTests(unittest.TestCase):
    def test_vertical_alignment_and_fitted_text_region(self):
        label=dict(box=[0,0,200,100],text='示例',font_size=20,line_height=1.2,align='left',valign='middle')
        self.assertEqual(_text_roi(label,1),(0,38,40,62))
        label['valign']='bottom'
        self.assertEqual(_text_roi(label,1),(0,76,40,100))
        label['min_font_size']=16
        self.assertEqual(_text_roi(label,1),(0,0,200,100))

    def test_gradient_plate_sampling_keeps_mask_and_stroke(self):
        plate=dict(type='rect',box=[0,0,100,100],fill='#FF0000',
                   gradient={'color':'#0000FF','angle':180},stroke='#00FFFF',stroke_width=2,radius=12)
        img,_,_=_project(plate,1,{})
        self.assertEqual(img.getpixel((0,0))[3],0)
        self.assertEqual(img.getpixel((0,50)),(0,255,255,255))
        self.assertGreater(img.getpixel((50,10))[0],img.getpixel((50,10))[2])
        self.assertGreater(img.getpixel((50,90))[2],img.getpixel((50,90))[0])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def case(self, *, size=(400,200), bg='#E1E7D9', ink='#132919', fore='#566C53',
             label=(25,129,200,33), subject=(0,0,260,200), texture=True):
        w,h=size
        uniform=Image.new('RGB',size,bg)
        uniform.save(self.root/'paper.png')
        subject_img=Image.new('RGBA',(260,200),(0,0,0,0))
        if texture:
            d=ImageDraw.Draw(subject_img)
            for x in range(0,260,16): d.line((x-50,200,x+25,103),fill=fore,width=3)
        subject_img.save(self.root/'arbitrary.png')
        return {'plan':{'canvas':[w,h],'background':bg,'elements':[
            {'id':'paper0','type':'image','asset_id':'p','role':'background','fit':'cover','box':[0,0,w,h]},
            {'id':'equipment_alpha','type':'image','asset_id':'object','box':list(subject),'at':0,'enter':0},
            {'id':'callout_X','type':'text','text':'材料结构与表面','font_size':25,'fill':ink,'align':'center',
             'box':list(label),'at':1,'enter':.4,'end':6}]},
            'assets':{'p':str(self.root/'paper.png'),'object':str(self.root/'arbitrary.png')},'fps':30,'duration':6}

    def test_opaque_edges_over_light_object_selected_unrelated_identity(self):
        view=self.case(); saved=copy.deepcopy(view)
        out,report=protect_text_layers(view)
        self.assertEqual(view,saved)
        self.assertEqual(report['protected'],['callout_X'])
        fix=out['plan']['elements'][-1]['readability_halo']
        self.assertEqual(fix['color'],'#E1E7D9')
        self.assertGreaterEqual(fix['opacity'],.72)
        self.assertEqual(protect_text_layers(out)[0],out)

    def test_centre_below_text_dark_portrait_canvas_uses_navy_not_white(self):
        view=self.case(size=(200,360),bg='#121D31',ink='#FCF7DA',fore='#FEBB5A',
                       label=(4,206,192,40),subject=(0,97,200,170))
        out,report=protect_text_layers(view)
        self.assertEqual(report['protected'],['callout_X'])
        self.assertEqual(out['plan']['elements'][-1]['readability_halo']['color'],'#121D31')

    def test_transparent_gutters_nonoverlapping_or_future_object_no_wash(self):
        for kind in ('blank_alpha','spatial','time','z_index','letterbox'):
            with self.subTest(kind=kind):
                volume=self.case(texture=kind!='blank_alpha')
                es=volume['plan']['elements']
                if kind=='spatial': es[1]['box']=[250,0,120,192]
                if kind=='time': es[1].update(at=3,end=6); es[2]['end']=2.5
                if kind=='z_index': es[1],es[2]=es[2],es[1]
                if kind=='letterbox':
                    es[1]['box']=[0,45,300,142]
                    # Opaque source, but alpha-free contain gutter before x=145
                    Image.new('RGBA',(10,150),'#122010').save(self.root/'arbitrary.png')
                    es[2]['box']=[10,125,115,33]
                out,report=protect_text_layers(volume)
                self.assertEqual(report['protected'],[])
                self.assertEqual(out,volume)

    def test_existing_card_between_subject_and_label_prevents_double_backing(self):
        view=self.case()
        view['plan']['elements'].insert(-1,{'id':'original_plate','type':'rect','box':[10,116,220,65],
            'fill':'#D7E7EA','radius':6,'at':1,'end':6,'enter':.4})
        out,report=protect_text_layers(view)
        self.assertEqual(report['protected'],[]); self.assertEqual(view,out)

    def test_textured_clean_plate_without_separate_subject_also_supported(self):
        view=self.case(texture=False)
        bg=Image.open(self.root/'paper.png').copy(); d=ImageDraw.Draw(bg)
        for x in range(25,260,8): d.line((x,125,x+5,167),fill='#574635',width=3)
        bg.save(self.root/'paper.png')
        self.assertEqual(protect_text_layers(view)[1]['protected'],['callout_X'])

    def test_uniform_dark_over_dark_is_not_safe_merely_because_texture_is_low(self):
        view=self.case(texture=False,bg='#111C31',ink='#1F243A')
        out,report=protect_text_layers(view)
        self.assertEqual(report['protected'],['callout_X'])
        self.assertNotEqual(out['plan']['elements'][-1]['readability_halo']['color'],'#111C31')


if __name__=='__main__':
    unittest.main()
