"""Single-frame poster inspection: local renderer only, no audio or providers."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from backend.app import vibe_remotion as renderer


class LayeredStillTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.out=self.root/'poster';self.out.mkdir()
        self.background=self.root/'background.png';Image.new('RGB',(640,360),'#031A33').save(self.background)
        self.plan=dict(version='ocv-layered-v1',canvas=[640,360],background='#031A33',elements=[
            dict(id='background',type='image',role='background',asset_id='background',fit='cover',
                 box=[0,0,640,360],at=0,end=12,enter=0,exit=0,motion='none'),
            dict(id='title',type='text',text='独立静态海报核查',fill='#FFFFFF',font_size=34,
                 box=[40,40,560,100],at=0,end=12,enter=0,exit=0,motion='none')])

    def invoke(self, **kwargs):
        return renderer.render_layered_still(self.plan,[640,360],{'background':self.background},self.out,**kwargs)

    def fake_run(self, command, **kwargs):
        self.assertIn('--still-only',command)
        self.assertNotIn('--stills-only',command)
        self.assertEqual(command[command.index('--concurrency')+1],'1')
        source=Path(command[command.index('--input')+1])
        raw=json.loads(source.read_text(encoding='utf-8'))
        self.assertEqual(raw['duration'],12)
        self.assertEqual(raw['fps'],30)
        self.assertEqual(raw['assets']['background'],str(self.background.resolve()))
        self.assertNotIn('audio',raw)
        Image.new('RGB',(640,360),'#031A33').save(self.out/'poster.png')
        (self.out/'metadata.json').write_text(json.dumps(dict(
            renderer='ocv-remotion-layered-v1',width=640,height=360,frames=1,still_only=True,
            checks={'layout':'passed'})),encoding='utf-8')
        return ''

    def test_one_process_one_still_no_audio_or_mux(self):
        before=copy.deepcopy(self.plan)
        with patch.object(renderer,'_runtime_paths',return_value=(Path('node'),Path('chrome'))), \
             patch.object(renderer,'_run',side_effect=self.fake_run) as run, \
             patch.object(renderer,'_audio_length') as audio:
            self.assertEqual(self.invoke(),self.out/'poster.png')
        run.assert_called_once();audio.assert_not_called()
        self.assertFalse((self.out/'silent.mp4').exists())
        self.assertEqual(self.plan,before)

    def test_nonstatic_or_invalid_timing_rejects_before_process(self):
        for field,value in [('at',1),('enter',.5),('exit',.3),('end',float('nan'))]:
            with self.subTest(field=field):
                original=self.plan['elements'][1][field];self.plan['elements'][1][field]=value
                with patch.object(renderer,'_run') as run:
                    with self.assertRaises(ValueError):self.invoke()
                run.assert_not_called();self.plan['elements'][1][field]=original

    def test_missing_assets_nonempty_output_and_cancel_do_not_run(self):
        with patch.object(renderer,'_run') as run:
            with self.assertRaises(InterruptedError):self.invoke(cancelled=lambda:True)
            self.background.unlink()
            with self.assertRaisesRegex(ValueError,'文件不存在'):self.invoke()
            (self.out/'keep.txt').write_text('do not overwrite',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'空目录'):self.invoke()
        run.assert_not_called()
        self.assertEqual((self.out/'keep.txt').read_text(encoding='utf-8'),'do not overwrite')

    def test_wrong_output_size_or_missing_layout_pass_is_rejected(self):
        for wrong in ('dimensions','audit'):
            with self.subTest(wrong=wrong):
                def fake(command,**kwargs):
                    self.fake_run(command,**kwargs)
                    if wrong=='dimensions':Image.new('RGB',(320,180)).save(self.out/'poster.png')
                    else:
                        p=self.out/'metadata.json';data=json.loads(p.read_text(encoding='utf-8'))
                        data['checks']={};p.write_text(json.dumps(data),encoding='utf-8')
                with patch.object(renderer,'_runtime_paths',return_value=(Path('node'),Path('chrome'))), \
                     patch.object(renderer,'_run',side_effect=fake):
                    with self.assertRaises(ValueError):self.invoke()
                for p in self.out.iterdir():p.unlink()

    @unittest.skipUnless(os.environ.get('OCV_RUN_LAYERED_STILL_SMOKE')=='1','requires installed local Remotion browser')
    def test_real_browser_single_frame_and_overflow_audit(self):
        root=Path(tempfile.mkdtemp(prefix='layered-still-smoke-',dir=renderer.ROOT/'runtime'/'temp'))
        source=root/'background.png';Image.new('RGB',(640,360),'#031A33').save(source)
        out=root/'valid';out.mkdir()
        poster=renderer.render_layered_still(self.plan,[640,360],{'background':source},out)
        self.assertEqual(set(p.name for p in out.iterdir()),{'poster.png','metadata.json'})
        with Image.open(poster) as image:
            self.assertEqual(image.size,(640,360))
            self.assertGreater(len(image.convert('RGB').getcolors(1_000_000)),2)
        invalid=copy.deepcopy(self.plan)
        invalid['elements'][1].update(text='文字不能被静默截断。'*30,box=[40,40,100,30],font_size=40)
        bad=root/'invalid';bad.mkdir()
        with self.assertRaisesRegex(ValueError,'OCV_LAYOUT_OVERFLOW'):
            renderer.render_layered_still(invalid,[640,360],{'background':source},bad)
        print('LAYERED_STILL_SMOKE_OUTPUT='+str(root))


if __name__=='__main__':unittest.main()
