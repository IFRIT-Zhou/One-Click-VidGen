import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

from PIL import Image
from pydantic import ValidationError

from backend.app import vibe_render as vibe


class RendererTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.out = self.root/'output'
        self.out.mkdir()
        self.assets = {key:self.root/(key+'.png') for key in ('plate','layer_01','layer_02')}
        Image.new('RGB',(80,48),'#102030').save(self.assets['plate'])
        first = Image.new('RGBA',(20,20),(180,43,77,255))
        first.putpixel((0,0),(254,9,0,0))
        first.save(self.assets['layer_01'])
        Image.new('RGBA',(20,20),'#008899').save(self.assets['layer_02'])
        self.audio = self.root/'audio.wav'
        with wave.open(str(self.audio),'wb') as f:
            f.setparams((1,2,8000,0,'NONE','not compressed'))
            f.writeframes(b'\0\0'*8000)
        self.manifest=dict(canvas=[160,96],source_size=[80,48],elements=[
            dict(asset_id='layer_01',box_px=[4,10,24,30]),dict(asset_id='layer_02',box_px=[40,10,60,30])])
        self.seq=[dict(asset_id='layer_01',cue='intro',seconds=.1),
                  dict(asset_id='layer_02',cue='after',seconds=.2,scale_from=.85,offset=[8,8],
                       settle=dict(cue='after',delay=.1,seconds=.1))]
        self.plan=vibe.VibePlan(sequence=self.seq,fade_in=0,fade_out=0)
        self.cues=dict(intro=0,after=.3)

    def scene(self, plan=None):
        return vibe._Scene(plan or self.plan,self.manifest,self.assets,self.cues,.75)

    def test_real_png_alpha_cue_and_final_geometry(self):
        scene=self.scene()
        self.assertEqual(scene.size,(80,48))
        first=scene.frame(6)
        self.assertEqual(first.getpixel((4,10)),(16,32,48))  # fully transparent
        self.assertEqual(first.getpixel((10,20)),(180,43,77))
        self.assertEqual(first.getpixel((53,24)),(16,32,48)) # not yet visible
        self.assertEqual(scene.frame(20).getpixel((53,24)),(0,136,153))

    def test_marker_relative_to_layer_and_global_fades(self):
        seq=[dict(**self.seq[0],markers=[dict(cue='intro',until='after',at=[24,40])]),self.seq[1]]
        scene=self.scene(vibe.VibePlan(sequence=seq,fade_in=0,fade_out=.2))
        self.assertEqual(scene.frame(5).getpixel((12,20)),(89,222,207))
        self.assertEqual(scene.frame(12).getpixel((12,20)),(180,43,77))
        self.assertEqual(scene.frame(scene.count-1).tobytes(),scene.flat.tobytes())
        self.assertEqual(vibe._output_size((2160,3840)),(1080,1920))

    def test_invalid_schema_cue_and_asset_fail_before_encoding(self):
        for bad in [dict(cue='x',asset_id='../a'),dict(**self.seq[0],code='x'),dict(self.seq[0],scale_from=float('nan'))]:
            with self.subTest(bad=bad), self.assertRaises(ValidationError): vibe.VibePlan(sequence=[bad])
        with self.assertRaises(ValidationError): vibe.VibePlan(sequence=[self.seq[0]]*2)
        for bad in [dict(cue='missing'),dict(delay=3),dict(seconds=5),dict(markers=[dict(cue='after',until='intro',at=[4,4])]),
                    dict(markers=[dict(cue='after',at=[5000,4])])]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.scene(vibe.VibePlan(sequence=[self.seq[0]|bad,self.seq[1]]))
        with self.assertRaises(ValueError): self.scene(vibe.VibePlan(sequence=self.seq[:1]))
        self.manifest['elements'][1]['box_px'][2]+=1
        with self.assertRaises(ValueError): self.scene()

    def run_render(self,ff='nonexistent-ocv-test-ffmpeg',start=.1):
        return vibe.render_preview(plan=self.plan,manifest=self.manifest,assets=self.assets,cues=self.cues,
                                   audio=self.audio,start=start,duration=.75,output_dir=self.out,ffmpeg=ff)

    def test_reject_short_audio_and_clean_failed_encoder(self):
        with self.assertRaisesRegex(ValueError,'配音未覆盖'): self.run_render(start=.9)
        with self.assertRaises(OSError): self.run_render()
        self.assertEqual(list(self.out.iterdir()),[])
        contents=self.audio.read_bytes()
        self.audio.write_bytes(contents[:-2])
        with self.assertRaisesRegex(ValueError,'不完整'): self.run_render()

    def test_watchdog_reaps_child_even_when_blocked_in_pipe(self):
        scene=self.scene()
        # More than any pipe buffer: child neither drains stdin nor exits.
        scene.count=10000
        t=time.monotonic()
        with self.assertRaises(TimeoutError):
            vibe._encode_frames([sys.executable,'-c','import time; time.sleep(20)'], scene, t+.6)
        self.assertLess(time.monotonic()-t,5)

    def test_contact_sheet_deduplicates_content_and_avoids_faded_tail(self):
        scene=self.scene(vibe.VibePlan(sequence=self.seq,fade_out=.15))
        proof=vibe._ProofSheet(scene)
        self.assertLessEqual(len(proof.indices),4)
        self.assertLessEqual(max(proof.indices),(scene.count-1)-.15*30)
        # Identical portrait frames during a long hold appear only once.
        frame=Image.new('RGB',(270,480),'red')
        for i in sorted(proof.indices): proof.accept(i,frame)
        times=proof.save(self.root/'proof.jpg')
        self.assertEqual(times,[round(min(proof.indices)/vibe.FPS,6)])
        with Image.open(self.root/'proof.jpg') as image: self.assertEqual(image.size,(270,500))

    def test_mux_timeout_discards_contact_sheet_and_partial_videos(self):
        def encode(cmd,scene,deadline,proof):
            Path(cmd[-1]).write_bytes(b'encoded')
            idx=min(proof.indices)
            proof.accept(idx,scene.frame(idx))
        def mux(cmd,**kwargs):
            self.assertTrue((self.out/'proof.jpg').is_file())
            Path(cmd[-1]).write_bytes(b'partial')
            raise subprocess.TimeoutExpired(cmd,1)
        with patch.object(vibe,'_encode_frames',encode),patch.object(vibe.subprocess,'run',mux),self.assertRaises(TimeoutError):
            self.run_render()
        self.assertEqual(list(self.out.iterdir()),[])

    def test_real_local_encode_and_mux(self):
        bundled=Path(__file__).resolve().parents[1]/'tools'/'ffmpeg'/'bin'
        def exe(name):
            path=bundled/(name+'.exe')
            return str(path) if path.is_file() else shutil.which(name)
        ff,probe=exe('ffmpeg'),exe('ffprobe')
        if not ff or not probe: self.skipTest('requires FFmpeg + ffprobe')
        orig=vibe._Scene.frame
        visits=[]
        def observe(scene, index):
            visits.append(index)
            return orig(scene,index)
        with patch.object(vibe._Scene,'frame',observe): metrics=self.run_render(ff)
        self.assertEqual(visits,list(range(metrics['frames'])), 'must not rerender frames for contact sheet')
        times=metrics['proof_times']
        self.assertEqual(times,sorted(set(times)))
        self.assertTrue(1<=len(times)<=4)
        self.assertTrue(all(abs(round(t*vibe.FPS)-t*vibe.FPS)<.001 for t in times))
        with Image.open(self.out/'proof.jpg') as sheet: self.assertEqual(sheet.format,'JPEG')
        for filename in ('silent','preview'):
            streams=json.loads(subprocess.check_output([probe,'-v','error','-show_streams','-of','json',
                                              str(self.out/(filename+'.mp4'))]))['streams']
            self.assertEqual(len(streams), 1 if filename=='silent' else 2)
            self.assertEqual(streams[0]['codec_name'],'h264')
            self.assertEqual(int(streams[0]['nb_frames']),metrics['frames'])
            if filename=='preview':
                self.assertEqual(streams[1]['codec_name'],'aac')
                self.assertAlmostEqual(float(streams[1]['duration']),.75,delta=.01)


if __name__=='__main__':
    unittest.main()
