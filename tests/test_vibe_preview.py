"""Isolated native preview endpoints. No live OCV state, external models or uploads."""
import hashlib
import json
import os
from pathlib import Path
from io import BytesIO
import tempfile
import unittest
from unittest.mock import patch
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import image_layer_packs as packs
from backend.app import vibe_preview as api


class VibePreviewTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name).resolve(); self.identity='8'*32
        self.project=self.root/'1'/self.identity
        self.project.mkdir(parents=True)
        self.image=self.project/'core.png'
        Image.new('RGB',(160,90),'#205163').save(self.image)
        self.audio=self.project/'narration.wav'
        self.wav()
        self.cues=[dict(slide_id='s2',text='二',start=1,end=1.5),dict(slide_id='s3',text='三',start=1.5,end=2.2)]
        self.shot=dict(id='shot2',image='core.png',start=1,end=2.2,slide_ids=['s2','s3'],source_subtitles=self.cues)
        self.record=dict(audio='narration.wav',scenes=self.cues,revision=2,shots=[self.shot])
        self.persist()
        for p in [patch.object(packs.studio,'ROOT',self.root),
                  patch.object(packs,'require_user',side_effect=lambda req: {'id':req.headers.get('user','1')})]:
            p.start();self.addCleanup(p.stop)
        app=FastAPI();app.include_router(packs.router);app.include_router(api.router)
        self.client=TestClient(app)
        self.addCleanup(self.client.close)
        self.base=f'/api/video-studio/{self.identity}/shots/shot2'
        self.sha=hashlib.sha256(self.image.read_bytes()).hexdigest()
        p=self.client.post(self.base+'/layer-packs',json={'source_sha256':self.sha,'canvas':[160,90],
                'elements':[{'name':'示意图','shape':'ellipse','box':[10,15,48,58]}]})
        self.assertEqual(p.status_code,200,p.text)
        self.data=dict(pack_id=p.json()['pack_id'],source_sha256=self.sha,
                       plan={'sequence':[{'asset_id':'layer_01','cue':'s2','seconds':.4}]})

    def wav(self, sample=b'\x21\x00'):
        with wave.open(str(self.audio),'wb') as f:
            f.setparams((1,2,16000,0,'NONE','not compressed'))
            f.writeframes(sample*48000)

    def persist(self):
        (self.project/'record.json').write_text(json.dumps(self.record,ensure_ascii=False),encoding='utf-8')

    def post(self, data=None, **opts):
        return self.client.post(self.base+'/vibe-previews',json=self.data if data is None else data,**opts)

    def latest(self,**opts):
        return self.client.get(self.base+'/vibe-preview/latest',**opts)

    def test_actual_render_download_cache_and_project_immutability(self):
        before=(self.project/'record.json').read_bytes()
        self.assertFalse(self.latest().json()['available'])
        r=self.post()
        self.assertEqual(r.status_code,200,r.text)
        m=r.json()
        self.assertFalse(m['may_charge']);self.assertFalse(m['cached'])
        self.assertEqual(m['metrics']['frames'],36)
        for key in ('preview_url','silent_url'):
            media=self.client.get(m[key])
            self.assertEqual(media.headers['content-type'],'video/mp4')
            self.assertIn(b'ftyp',media.content[:32])
            self.assertEqual(self.client.get(m[key],headers={'user':'2'}).status_code,404)
        proof=self.client.get(m['proof_url'])
        self.assertEqual(proof.headers['content-type'],'image/jpeg')
        with Image.open(BytesIO(proof.content)) as img:
            self.assertEqual(img.format,'JPEG');img.verify()
        self.assertEqual(self.client.get(m['proof_url'],headers={'user':'2'}).status_code,404)
        with patch.object(api,'render_preview',side_effect=AssertionError('cache hit encoded again')):
            hit=self.post().json()
            self.assertTrue(hit['cached'])
            self.assertEqual(hit['preview_id'],m['preview_id'])
        self.assertEqual(self.latest().json()['plan'],m['plan'])
        self.assertEqual(self.latest(headers={'user':'2'}).status_code,404)
        self.assertEqual((self.project/'record.json').read_bytes(),before)

    @staticmethod
    def encoder(**k):
        for name in ['preview','silent']: (k['output_dir']/(name+'.mp4')).write_bytes(b'mocked')
        Image.new('RGB',(10,10)).save(k['output_dir']/'proof.jpg')
        # Route supplies the precise selected PCM segment, not the whole recording.
        with wave.open(str(k['audio']),'rb') as f:
            assert k['start']==0 and f.getnframes()==19200
        return {'frames':36}

    def test_cache_invalidated_by_saved_subtitles_and_audio_but_not_unrelated_revision(self):
        with patch.object(api,'render_preview',side_effect=self.encoder) as run:
            first=self.post().json()['preview_id']
            self.record['revision']+=7;self.persist()
            self.assertTrue(self.latest().json()['available'])
            self.assertTrue(self.post().json()['cached'])
            self.cues[0]['text']='定稿文字';self.persist()
            self.assertFalse(self.latest().json()['available'])
            second=self.post().json()['preview_id'];self.assertNotEqual(first,second)
            self.wav(b'\x53\x00')   # same filename, byte length & actual duration
            self.assertFalse(self.latest().json()['available'])
            third=self.post().json()['preview_id'];self.assertNotEqual(second,third)
            self.assertEqual(run.call_count,3)

    def test_selection_timestamp_tracks_latest_pointer_and_cached_reselection(self):
        before=(self.project/'record.json').read_bytes()
        with patch.object(api,'render_preview',side_effect=self.encoder) as render:
            first=self.post().json()
            self.assertGreater(first['selected_at'],0)
            pointer=api._pointer(self.project,'shot2')
            os.utime(pointer,(1000,1000))
            selected=self.latest().json()
            self.assertEqual(selected['selected_at'],1000)
            self.assertEqual(selected['preview_id'],first['preview_id'])
            # Reselecting cached media is new intent, not a newly rendered clip.
            second=self.post().json()
            self.assertTrue(second['cached'])
            self.assertGreater(second['selected_at'],1000)
            self.assertEqual(self.latest().json()['selected_at'],second['selected_at'])
            self.assertEqual(render.call_count,1)
        self.assertEqual((self.project/'record.json').read_bytes(),before)

    def test_rejects_unknown_cue_stale_pack_wrong_owner_extra_fields_and_traversal(self):
        for updates,status in [
                ({'pack_id':'b'*32},404),({'pack_id':'../other'},422),({'source_sha256':'1'*64},409),
                ({'output':'../other'},422),({'plan':{'sequence':[{'asset_id':'layer_01','cue':'foreign'}]}},422)]:
            with self.subTest(updates=updates):
                self.assertEqual(self.post({**self.data,**updates}).status_code,status)
        self.assertEqual(self.post(headers={'user':'2'}).status_code,404)
        prev=self.shot.copy();prev['id']='other';self.record['shots'].append(prev);self.persist()
        self.assertEqual(self.client.post(self.base.replace('shot2','other')+'/vibe-previews',json=self.data).status_code,409)
        self.record['audio']='../../../foreign.wav';self.persist()
        self.assertEqual(self.post().status_code,404)

    def test_designed_against_old_timing_rejected_before_encoding(self):
        with patch.object(api,'render_preview',side_effect=self.encoder) as run:
            original=self.post().json()
            stamped={**self.data,'input_stamp':original['input_stamp']}
            self.assertTrue(self.post(stamped).json()['cached'])
            self.cues[0]['text']='改过的新句子';self.persist()
            self.assertEqual(self.post(stamped).status_code,409)
            self.assertEqual(run.call_count,1)

    def test_not_published_when_input_changes_during_render_or_encoder_fails(self):
        def change(**k):
            result=self.encoder(**k)
            self.cues[0]['end']=1.4;self.persist()
            return result
        with patch.object(api,'render_preview',side_effect=change):
            self.assertEqual(self.post().status_code,409)
        for err,code in [(TimeoutError(),504),(RuntimeError(),500)]:
            with patch.object(api,'render_preview',side_effect=err):
                self.assertEqual(self.post().status_code,code)
        self.assertFalse(self.latest().json()['available'])
        self.assertEqual(list((self.project/'assets'/'vibe_previews').iterdir()),[])
        self.assertTrue(api._slot.acquire(blocking=False))
        try:
            self.assertEqual(self.post().status_code,429)
        finally:
            api._slot.release()


if __name__ == '__main__': unittest.main()
