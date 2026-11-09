"""Isolated from production projects, owner identities, providers and network."""
import copy
from contextlib import nullcontext
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import image_layer_packs as packs, vibe_preview as pre, vibe_adoption as choice
from backend.app import cloud_video, video_generation as vg, video_studio as studio, video_export as export
from backend.app.video_audio_sync import sync_shot_timings


class VibeAdoptionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root=Path(tmp.name).resolve()
        self.id='7'*32
        self.p=root/'1'/self.id
        self.p.mkdir(parents=True)
        im=self.p/'card.png';Image.new('RGB',(160,90),'#166f67').save(im)
        with wave.open(str(self.p/'voice.wav'),'wb') as f:
            f.setparams((1,2,16000,0,'NONE','not compressed'))
            cycle=struct.pack('<40h',*[round(8000*math.sin(2*math.pi*i/40)) for i in range(40)])
            f.writeframes(cycle*1200)
        self.cues=[dict(slide_id='A',text='甲',start=0,end=.5),dict(slide_id='B',text='乙',start=.5,end=1),
                   dict(slide_id='C',text='丙',start=1,end=3)]
        self.shot=dict(id='s1',kind='static',image='card.png',image_status='completed',start=0,end=1,duration=1,
                       source_subtitles=self.cues[:2],slide_ids=['A','B'],video_prompt='')
        self.other=dict(id='s2',kind='static',image='card.png',image_status='completed',start=1,end=3,duration=2,
                        source_subtitles=self.cues[2:],slide_ids=['C'])
        self.rec=dict(id=self.id,status='image_review',revision=1,shots=[self.shot,self.other],scenes=self.cues,
                      audio='voice.wav',subtitles='sub.srt',settings={'ratio':'16:9','name':'Synthetic'},
                      creation_parameters={},logs=[])
        self.persist(self.rec)
        (self.p/'sub.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\nA\n\n2\n00:00:01,000 --> 00:00:03,000\nB\n',encoding='utf-8')
        for m in (patch.object(studio,'ROOT',root),patch.object(studio,'require_user',return_value={'id':1}),
                  patch.object(packs,'require_user',side_effect=lambda req:{'id':req.headers.get('user','1')})):
            m.start();self.addCleanup(m.stop)
        app=FastAPI()
        for router in (packs.router,pre.router,choice.router,studio.router,export.router):
            app.include_router(router)
        self.client=TestClient(app);self.addCleanup(self.client.close)
        self.base='/api/video-studio/'+self.id+'/shots/s1'
        pack=self.client.post(self.base+'/layer-packs',json=dict(source_sha256=hashlib.sha256(im.read_bytes()).hexdigest(),
                            canvas=[160,90],elements=[dict(name='A',shape='rect',box=[10,12,48,60])])).json()
        self.plan=dict(pack_id=pack['pack_id'],source_sha256=pack['source_sha256'],
                       plan={'sequence':[dict(asset_id='layer_01',cue='B',seconds=.25)]})
        resp=self.client.post(self.base+'/vibe-previews',json=self.plan)
        self.assertEqual(resp.status_code,200,resp.text)
        self.preview=resp.json()

    def persist(self,record):
        (self.p/'record.json').write_text(json.dumps(record),encoding='utf8')

    def post(self,action='adopt',revision=1,**extra):
        return self.client.post(self.base+'/vibe-'+action,json=dict(revision=revision,preview_id=self.preview['preview_id']),**extra)

    def test_static_adoption_exports_silent_with_project_audio_and_undo_restores_static(self):
        updated=self.post();self.assertEqual(updated.status_code,200,updated.text)
        first=updated.json();shot=first['shots'][0]
        self.assertEqual((first['status'],first['revision']),('image_review',2))
        self.assertEqual((shot['video_status'],shot['kind'],shot['video_backend']),('completed','video','vibe'))
        self.assertEqual(first['shots'][1],self.other)
        clip=export._source(self.p,shot)
        self.assertFalse(export._has_audio_stream(clip))
        self.assertEqual(clip.read_bytes(),self.client.get(self.preview['silent_url']).content)
        # Evicting a preview must not invalidate a formal video.
        (pre._store(self.p)/self.preview['preview_id']/'silent.mp4').unlink()
        self.assertTrue(clip.is_file())
        self.assertEqual(studio._project_video_duration_blockers(dict(creation_parameters={'video_generation_backend':'cloud_h3'}),
                        [dict(shot,duration=19)]),[])
        with patch.object(export,'PROJECT_ROOT',self.p/'export-root'),patch.object(export,'_size',return_value=(160,90)):
            _,raw,sub=export._render(self.p,first)
        self.assertTrue(export._has_audio_stream(raw));self.assertTrue(sub.is_file())
        self.assertAlmostEqual(export._video_duration(raw),3,delta=.034)
        samples=subprocess.check_output([export.ffmpeg_binary(),'-loglevel','error','-i',str(raw),
                 '-map','0:a:0','-ac','1','-ar','16000','-f','s16le','pipe:1'])
        middle=struct.unpack('<16000h',samples[32000:64000])
        rms=math.sqrt(sum(v*v for v in middle)/len(middle))
        self.assertAlmostEqual(rms/(8000/math.sqrt(2)),1.0,delta=.04, msg='synthetic silence must not lower narration gain')
        back=self.post('undo',2);self.assertEqual(back.status_code,200,back.text)
        row=back.json()['shots'][0]
        self.assertEqual({k:v for k,v in row.items() if k!='video_history'},self.shot)
        self.assertEqual(export._source(self.p,row),self.p/'card.png')
        self.assertTrue(clip.is_file())

    def test_preserve_paid_original_and_optimistic_and_ownership_guards(self):
        old=self.p/'original.mp4'
        old.write_bytes(self.client.get(self.preview['silent_url']).content)
        self.shot.update(kind='video',video='original.mp4',video_attempt=2,video_backend='cloud_h3',
                         video_status='completed',video_request=dict(backend='cloud_h3',task_id='previous-paid-task'))
        self.rec.update(status='completed',export={'raw':'old.mp4'})
        self.persist(self.rec)
        before=(self.p/'record.json').read_bytes()
        self.assertEqual(self.post(headers={'user':'2'}).status_code,404)
        self.assertEqual(self.post(revision=9).status_code,409)
        self.assertEqual((self.p/'record.json').read_bytes(),before)
        result=self.post();self.assertEqual(result.status_code,200,result.text);applied=result.json()
        self.assertEqual(applied['status'],'video_review');self.assertNotIn('export',applied)
        self.assertEqual(applied['shots'][0]['video_history'][0]['video'],'original.mp4')
        self.assertEqual(self.post(revision=2).json()['revision'],2)
        with patch.object(cloud_video,'cloud_client_for',side_effect=AssertionError('must not contact provider')):
            with self.assertRaisesRegex(Exception,'撤回'):
                vg._generate_for_user(self.id,vg.GenerateClips(revision=2,shot_ids=['s1'],regenerate_completed=True),1)
        back=self.post('undo',2);self.assertEqual(back.status_code,200,back.text)
        restored=back.json()['shots'][0]
        self.assertEqual({k:v for k,v in restored.items() if k!='video_history'},self.shot)
        self.assertEqual(export._source(self.p,restored),old)

    def test_stale_timeline_changed_audio_running_and_tampered_source_fail_closed(self):
        self.shot['video_queued']=True;self.persist(self.rec)
        self.assertEqual(self.post().status_code,409)
        self.shot.pop('video_queued')
        self.cues[1]['text']='已改';self.persist(self.rec)
        self.assertEqual(self.post().status_code,409)
        self.cues[1]['text']='乙';self.persist(self.rec)
        before=(self.p/'record.json').read_bytes()
        wrong=pre._store(self.p)/self.preview['preview_id']/'silent.mp4'
        wrong.write_bytes(self.client.get(self.preview['preview_url']).content)
        self.assertEqual(self.post().status_code,409)
        self.assertEqual((self.p/'record.json').read_bytes(),before)

    def test_review_only_preview_cannot_be_adopted_or_mutate_formal_record(self):
        receipt_path=pre._store(self.p)/self.preview['preview_id']/'receipt.json'
        original=json.loads(receipt_path.read_text(encoding='utf-8'))
        before=(self.p/'record.json').read_bytes()
        variants=[
            {'quality_status':'needs_review'},
            {'metrics':{**original['metrics'],'quality_status':'needs_review'}},
            {'quality_status':'passed','metrics':{**original['metrics'],'quality_status':'needs_review'}},
            {'workflow_version':'material-aware-layered-v2'},
            {'workflow_version':'material-aware-layered-v2','quality_status':'pending'},
        ]
        for changes in variants:
            with self.subTest(changes=changes):
                receipt_path.write_text(json.dumps({**original,**changes}),encoding='utf-8')
                with patch.object(choice,'_verify_silent',side_effect=AssertionError('cannot enter adoption')):
                    response=self.post()
                self.assertEqual(response.status_code,409,response.text)
                self.assertIn('视觉核查',response.text)
                self.assertEqual((self.p/'record.json').read_bytes(),before)
                self.assertFalse((self.p/'assets'/'videos').exists())
                self.assertEqual(self.client.get(self.preview['preview_url']).status_code,200,
                                 'review-only artifacts must remain inspectable')

    def test_passed_layered_review_is_eligible_and_undo_is_not_review_gated(self):
        receipt_path=pre._store(self.p)/self.preview['preview_id']/'receipt.json'
        receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
        receipt.update(workflow_version='material-aware-layered-v2',quality_status='passed',quality_issues=[])
        receipt['metrics']['quality_status']='passed'
        receipt_path.write_text(json.dumps(receipt),encoding='utf-8')
        response=self.post()
        self.assertEqual(response.status_code,200,response.text)
        # Withdrawing an existing formal result must not be blocked by a later
        # review flag on its disposable preview cache.
        receipt['quality_status']='needs_review'
        receipt_path.write_text(json.dumps(receipt),encoding='utf-8')
        response=self.post('undo',2)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['shots'][0]['kind'],'static')

    def test_local_cue_change_flags_but_offset_only_reuses_identical_preview(self):
        first=copy.deepcopy(self.cues)
        stamp=pre._snapshot(self.p,'s1')[0]
        # Some WAV variants are hashed whole and decoded by FFmpeg at a given
        # seek point. For those the offset must be part of the identity.
        with patch.object(pre,'_audio',side_effect=lambda _f,s,e:('whole-file',s)):
            whole0=pre._snapshot(self.p,'s1')[0]
        for entry in self.rec['scenes'][:2]:
            entry['start']+=.5;entry['end']+=.5
        self.shot.update(start=.5,end=1.5)
        self.persist(self.rec)
        self.assertEqual(pre._snapshot(self.p,'s1')[0],stamp)
        with patch.object(pre,'_audio',side_effect=lambda _f,s,e:('whole-file',s)):
            self.assertNotEqual(pre._snapshot(self.p,'s1')[0],whole0)
        changed=copy.deepcopy(first);changed[0]['end']=.7;changed[1]['start']=.7
        self.shot.update(start=0,end=1,video_request={'backend':'vibe'},kind='video',video_status='completed')
        self.rec['scenes']=first
        sync_shot_timings(self.rec,changed,self.p/'voice.wav',self.p/'voice.wav',new_duration=3)
        self.assertTrue(self.rec['shots'][0]['audio_timing_adjustment']['needs_review'])
        self.assertNotIn('audio_timing_adjustment',self.rec['shots'][1])

    def test_missing_clip_recovery_does_not_break_undo_or_reuse_a_stale_preference(self):
        first=self.post().json()
        (self.p/first['shots'][0]['video']).unlink()
        second=self.post(revision=2)
        self.assertEqual(second.status_code,200,second.text)
        rerendered=second.json()
        self.assertNotEqual(rerendered['shots'][0]['video'],first['shots'][0]['video'])
        back=self.post('undo',3)
        self.assertEqual(back.status_code,200,back.text)
        self.assertEqual(back.json()['shots'][0]['kind'],'static')
        nextplan=copy.deepcopy(self.plan);nextplan['plan']['sequence'][0]['seconds']=.19
        newer=self.client.post(self.base+'/vibe-previews',json=nextplan)
        self.assertEqual(newer.status_code,200,newer.text)
        self.assertEqual(self.post(revision=4).status_code,409)

    def intent(self,rev,enabled=True,**kw):
        return self.client.post(self.base+'/vibe-intent',json={'revision':rev,'enabled':enabled},**kw)

    def test_early_long_shot_reservation_saved_in_review_no_model_or_static_export(self):
        original=dict(kind='video',action='变化',video_prompt='参考图1持续变化',generation_duration=17)
        self.shot.update(original,intent='说明',image_prompt='证据',image='',image_status='pending')
        self.other.update(intent='说明',image_prompt='证据')
        self.cues[1]['end']=17; self.cues[2].update(start=17,end=19)
        self.shot.update(end=17,duration=17)
        self.other.update(start=17,end=19)
        self.rec.update(status='storyboard_review',references=[],creation_parameters={'video_generation_backend':'cloud_h3'})
        self.persist(self.rec)
        self.assertEqual(self.intent(1,headers={'user':'2'}).status_code,404)
        self.assertEqual(self.intent(42).status_code,409)
        response=self.intent(1);self.assertEqual(response.status_code,200,response.text)
        record=response.json();self.assertEqual(record['shots'][0]['kind'],'static')
        self.assertEqual(record['shots'][0]['vibe_reserved']['original_kind'],'video')
        self.assertEqual(self.intent(2).json()['revision'],2)
        project='/api/video-studio/'+self.id
        resp=self.client.put(project,json={'revision':2,'shots':record['shots']})
        self.assertEqual(resp.status_code,200,resp.text)
        resp=self.client.post(project+'/review',json={'revision':3})
        self.assertEqual(resp.status_code,200,resp.text)
        record=resp.json();self.assertEqual(record['status'],'generation_ready')
        self.assertTrue(record['shots'][0]['vibe_reserved'])
        with patch.object(cloud_video,'cloud_client_for',side_effect=AssertionError('provider must not be contacted')):
            with self.assertRaisesRegex(Exception,'预留'):
                vg._generate_for_user(self.id,vg.GenerateClips(revision=4,shot_ids=['s1']),1)
        record['status']='video_generation_ready';self.persist(record)
        denied=self.client.post(project+'/export',json={'revision':4})
        self.assertEqual(denied.status_code,409);self.assertIn('第 01 镜已预留',denied.text)
        self.assertFalse((self.p/'export_work').exists())
        resp=self.intent(4,False);self.assertEqual(resp.status_code,200,resp.text)
        restored=resp.json()['shots'][0]
        self.assertEqual({k:restored[k] for k in original},original)
        self.assertNotIn('vibe_reserved',restored)
        self.assertTrue(studio._project_video_duration_blockers(self.rec,[restored]))

    def test_reserved_original_clip_survives_adopt_undo_and_cancel(self):
        self.shot.update(kind='video',action='主体转身',video='old.mp4',video_status='completed',
                         video_attempt=9,video_backend='cloud_h3',video_request={'backend':'cloud_h3'})
        old=self.p/'old.mp4'
        old.write_bytes(self.client.get(self.preview['silent_url']).content)
        self.persist(self.rec)
        self.assertEqual(self.intent(1).status_code,200)
        a=self.post(revision=2);self.assertEqual(a.status_code,200,a.text)
        self.assertNotIn('vibe_reserved',a.json()['shots'][0])
        self.assertIn('撤回',self.intent(3,False).text)
        u=self.post('undo',3);self.assertEqual(u.status_code,200,u.text)
        self.assertTrue(u.json()['shots'][0]['vibe_reserved'])
        un=self.intent(4,False);self.assertEqual(un.status_code,200,un.text)
        ended=un.json()['shots'][0]
        self.assertEqual((ended['kind'],ended['video'],ended['action']),('video','old.mp4','主体转身'))
        self.assertEqual(ended['video_request'],self.shot['video_request'])

    def test_fixed_replan_keeps_reserved_core_static_regroup_stops_before_llm(self):
        from unittest.mock import MagicMock
        self.rec.update(status='storyboard_review',references=[])
        self.rec['settings'].update(style='',characters='',world='')
        self.shot.update(kind='video',action='抬手',video_prompt='参考图1抬手')
        self.persist(self.rec)
        self.assertEqual(self.intent(1).status_code,200)
        base='/api/video-studio/'+self.id
        self.assertEqual(self.client.post(base+'/plan?fresh=true&regroup=true&revision=2').status_code,409)
        def planner(*args, fixed_shots=None, **kwargs):
            self.assertEqual(fixed_shots[0]['kind'],'static')
            return {}, [{k:v for k,v in row.items() if k in {'id','kind','start','end','duration','slide_ids'}}
                        | {'image_prompt':'新方案','intent':'解释'} for row in fixed_shots]
        def inline(target,daemon):
            return MagicMock(start=target)
        with patch.object(studio,'project_language_scope',return_value=nullcontext()),patch.object(studio,'plan_storyboard',side_effect=planner),patch.object(studio.threading,'Thread',side_effect=inline):
            # Direct call avoids patching the TestClient's own HTTP thread.
            saved=studio.plan(self.id,None,fresh=True,revision=2)
        self.assertEqual(saved['status'],'storyboard_review',saved.get('error'))
        self.assertFalse(saved.get('error'))
        first=saved['shots'][0]
        self.assertEqual(first['image_prompt'],'新方案')
        self.assertEqual(first['vibe_reserved']['original_kind'],'video')
        self.assertEqual(first['image'],self.shot['image'])

    def test_redraw_preserves_local_route_and_blocks_export_without_review(self):
        applied=self.post().json()
        target=applied['shots'][0];original=target['video']
        studio._invalidate_shot_video(applied,target,'核心分镜图已重绘')
        self.assertTrue(studio.has_vibe_output(target));self.assertTrue(target['design_needs_review'])
        self.assertEqual(target['video'],original);self.assertTrue((self.p/original).is_file())

    def test_narration_insert_does_not_clone_immutable_vibe_attempt_into_new_shot(self):
        from backend.app.narration_structure import tag
        rec=self.post().json()
        changed=[tag(x) for x in self.cues]
        changed[0]['end']=.35
        changed.insert(1,dict(slide_id='NEW',text='插入',start=.35,end=.5,narration_origin='inserted',
                              narration_unit_id='added',narration_structure=True))
        sync_shot_timings(rec,changed,self.p/'voice.wav',self.p/'voice.wav',new_duration=3)
        suffix=next(s for s in rec['shots'] if s.get('narration_parent_visual_id')=='s1')
        self.assertEqual(suffix['slide_ids'],['B'])
        self.assertEqual(suffix['vibe_reserved']['original_kind'],'static')
        self.assertNotIn('video_request',suffix);self.assertNotIn('video',suffix)
        self.assertTrue(studio.has_vibe_output(rec['shots'][0]))

    def test_adopted_studio_draft_and_review_preserve_media_reject_spoof_unknown_and_model_toggle(self):
        self.shot.update(image_prompt='画面',intent='解释')
        self.other.update(image_prompt='图',intent='说明')
        self.rec['references']=[]
        self.persist(self.rec)
        rec=self.post().json()
        origin=copy.deepcopy(rec['shots'][0])
        rec.update(status='storyboard_review');self.persist(rec)
        payload=copy.deepcopy(rec['shots']);payload[0].update(image='bad',video='fake',video_request={})
        base='/api/video-studio/'+self.id
        resp=self.client.put(base,json={'revision':2,'shots':payload})
        self.assertEqual(resp.status_code,200,resp.text)
        reviewed=self.client.post(base+'/review',json={'revision':3})
        self.assertEqual(reviewed.status_code,200,reviewed.text)
        saved=reviewed.json();active=saved['shots'][0]
        self.assertEqual((active['video'],active['image'],active['video_request']),(origin['video'],origin['image'],origin['video_request']))
        self.assertTrue(active['audit']['ready'])
        saved.update(status='video_review');active['audio_timing_adjustment']={'needs_review':True};self.persist(saved)
        self.assertIn('先试看',self.client.post(base+'/export',json={'revision':4}).text)
        self.assertEqual(saved['shots'][1]['image'],self.other['image'])
        switched=self.client.post(self.base+'/motion',json={'revision':4,'kind':'static'})
        self.assertEqual(switched.status_code,409,switched.text)
        self.assertEqual(self.client.post(self.base+'/confirm-audio-timing',json={'revision':4}).status_code,200)

if __name__=='__main__':
    unittest.main()
