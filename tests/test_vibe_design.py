"""Fresh synthetic artwork and final audio; no cloud, no production project."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

from fastapi import HTTPException
from PIL import Image,ImageDraw
from starlette.requests import Request

from backend.app import vibe_design as design
from backend.app import vibe_preview as preview


class TestVibeDesign(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup)
        self.id='a'*32
        root=Path(d.name).resolve()
        self.project=root/'1'/self.id;self.project.mkdir(parents=True)
        self.request=Request({'type':'http','headers':[]})
        self.pic=Image.new('RGB',(480,270),'#10283c')
        draw=ImageDraw.Draw(self.pic)
        draw.rectangle((20,75,190,126),fill='#e5e2d4')
        draw.rectangle((239,76,385,101),fill='#36b09b')
        draw.rectangle((242,160,406,183),fill='#abc008')
        self.pic.save(self.project/'slide.png')
        with wave.open(str(self.project/'vo.wav'),'wb') as f:
            f.setparams((1,2,8000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*8000*3)
        self.record=dict(audio='vo.wav',settings={},creation_parameters={'language_route':'personal'},
                         shots=[dict(id='s1',start=0,end=3,slide_ids=['cue-A','cue-B'],image='slide.png',intent='')],
                         scenes=[dict(slide_id='cue-A',start=0,end=1.3,text='介绍甲楼和乙楼'),dict(slide_id='cue-B',start=1.3,end=3,text='丙单位发布研究')])
        self.write()
        for obj,key,val in [(design.studio,'ROOT',root),(design.packs,'require_user',lambda r:{'id':1})]:
            p=patch.object(obj,key,val);p.start();self.addCleanup(p.stop)
        self.ocr=patch.object(design,'_offline_words',return_value=[
            {'text':t,'box':[x,y,x+18,y+9]} for t,x,y in [('甲楼',40,85),('乙楼',246,83),('丙单位',244,166)]
        ])
        self.words=self.ocr.start();self.addCleanup(self.ocr.stop)

    def write(self):
        (self.project/'record.json').write_text(json.dumps(self.record,ensure_ascii=False),encoding='utf8')

    def make(self,**kwargs):
        return design.design(self.id,'s1',design.DesignRequest(**kwargs),self.request)

    def test_free_initial_design_preserves_bytes_finds_caption_cues_and_reuses(self):
        before=(self.project/'record.json').read_bytes()
        with patch.object(design,'_model',side_effect=AssertionError('unexpected model call')):
            a=self.make()
            self.assertFalse(a['may_charge'] or a['model_called'])
            self.assertEqual({el['name']:el['cue'] for el in a['layers']},{'甲楼':'cue-A','乙楼':'cue-A','丙单位':'cue-B'})
            req=a['preview_request']
            self.assertEqual(req['input_stamp'],preview._snapshot(self.project,'s1')[0])
            self.assertTrue(self.make()['cached'])
            b=self.make(pace='slow')
            self.assertEqual(b['preview_request']['pack_id'],req['pack_id'])
            self.assertGreater(b['preview_request']['plan']['sequence'][0]['seconds'],req['plan']['sequence'][0]['seconds'])
            self.assertEqual(before,(self.project/'record.json').read_bytes())
            self.words.assert_called()

    def test_consent_invalid_boxes_cue_and_time_fail_before_writing(self):
        with patch.object(design,'_model') as cloud:
            with self.assertRaises(HTTPException) as error:self.make(engine='model')
            self.assertEqual(error.exception.status_code,422);cloud.assert_not_called()
            ok=dict(name='甲',box=[20,75,190,126],cue='cue-A')
            for layers in ([dict(ok,cue='missing')],[dict(ok,box=[10,12,1220,22])],[dict(ok,seconds=12)],
                           [ok,dict(ok,name='乙')],[dict(ok,offset=[2000,1])]):
                cloud.return_value=(layers,'模型输出')
                with self.assertRaises(HTTPException):self.make(engine='model',confirm_model=True)
                self.assertFalse((self.project/'assets'/'layer_packs').exists())

    def test_real_model_boundary_only_allowed_data_and_no_repeat_call(self):
        params={'language_route':'personal'}
        with patch.object(design,'project_language_scope') as scope, patch.object(design,'generate_gemini_text',
                return_value=json.dumps(dict(elements=[dict(name='甲楼',box=[20,75,190,126],cue='cue-B')],summary='第2句进入'))) as cloud:
            out=self.make(engine='model',confirm_model=True)
            self.assertTrue(out['model_called'] and out['may_charge'])
            self.assertEqual(out['layers'][0]['cue'],'cue-B')
            scope.assert_called_once_with('1',params)
            sent=cloud.call_args.kwargs
            self.assertEqual(sent['image_data']['mime_type'],'image/jpeg')
            metadata=json.loads(sent['user_prompt'])
            self.assertNotIn('audio',metadata);self.assertNotIn(str(self.project),sent['user_prompt'])
            self.assertEqual([r['id'] for r in metadata['subtitles']],['cue-A','cue-B'])
            saved=self.make(engine='model',confirm_model=True)
            self.assertTrue(saved['cached']);self.assertFalse(saved['may_charge'] or saved['model_called'])
            self.assertEqual(cloud.call_count,1)
            # Same file path but different final words is a different design request.
            self.record['scenes'][1]['text']='丁单位';self.write()
            newer=self.make(engine='model',confirm_model=True)
            self.assertNotEqual(newer['preview_request']['input_stamp'],saved['preview_request']['input_stamp'])
            self.assertEqual(cloud.call_count,2)

    def test_text_only_project_model_never_sends_pixels_and_has_separate_cache(self):
        self.record['creation_parameters']={'use_cloud_image_pool':True}
        self.record['shots'][0]['image_prompt']='sensitive-private-image-description'
        self.write()
        def answer(**kw):
            p=json.loads(kw['user_prompt'])
            if kw['image_data'] is None:
                self.assertEqual(set(p),{'canvas','subtitles','instruction','pace','fixed_layers'})
                self.assertNotIn('sensitive-private',kw['user_prompt'])
                self.assertNotIn('base64',kw['user_prompt'])
                self.assertEqual({e['name'] for e in p['fixed_layers']},{'甲楼','乙楼','丙单位'})
                out=[{'asset_id':e['asset_id'],'cue':'cue-A'} for e in p['fixed_layers']]
            else:
                out=[dict(name='甲楼',cue='cue-B',box=[20,75,190,126])]
            return json.dumps({'elements':out})
        with patch.object(design,'project_language_scope') as scope,patch.object(design,'generate_gemini_text',side_effect=answer) as fake:
            with self.assertRaises(HTTPException):self.make(engine='model',model_input='layout')
            fake.assert_not_called()
            a=self.make(engine='model',model_input='layout',confirm_model=True)
            b=self.make(engine='model',model_input='vision',confirm_model=True)
            self.assertFalse(a['cached'] or b['cached'])
            self.assertTrue(self.make(engine='model',model_input='layout',confirm_model=True)['cached'])
            self.assertEqual(scope.call_args.args,('1',{'use_cloud_image_pool':True}))
            self.assertEqual(fake.call_count,2)

    def test_cloud_and_personal_make_at_most_one_model_post_no_policy_leak(self):
        from backend.app import gemini_client as client
        from backend.app import language_routing
        failed=Mock(ok=False,status_code=500);failed.json.return_value={'error':{'message':'temporary'}}
        pool=Mock()
        pool.image_pool_runtime.return_value={'base_url':'https://pool.invalid','access_token':'mock'}
        settings={'LANGUAGE_PROVIDER':'gemini','GEMINI_API_BASE':'https://example.invalid/v1',
                  'GEMINI_API_KEY':'mock','GEMINI_MODEL':'first','GEMINI_FALLBACK_MODELS':'second,third',
                  'GEMINI_RETRY_COUNT':'3','GEMINI_READ_TIMEOUT_SECONDS':'120'}
        with patch.dict(client.os.environ,settings,clear=True), patch.object(language_routing,'cloud_client_for',return_value=pool), \
             patch.object(client.requests,'post',return_value=failed) as post, patch.object(client.time,'sleep',side_effect=AssertionError('no billed retries')):
            for route in ('personal','cloud'):
                self.record['creation_parameters']={'language_route':route};self.write()
                with self.assertRaises(HTTPException) as e:self.make(engine='model',confirm_model=True)
                self.assertEqual(e.exception.status_code,502)
                self.assertEqual(post.call_count,1)
                self.assertEqual(post.call_args.kwargs['timeout'],(8,75))
                post.reset_mock()
            self.assertEqual(client._gemini_models(),['first','second','third'])
            self.assertEqual(client._gemini_retry_count(),3)
            self.assertEqual(client._gemini_timeout(),(10,120))

    def test_revise_immutable_pack_and_refuse_wrong_shot_or_stale_input(self):
        data=self.make()['preview_request']
        # Create a real receipt through the render route; no formal record state changes.
        received=preview.create(self.id,'s1',preview.PreviewRequest.model_validate(data),self.request)
        with patch.object(design,'_new_pack',side_effect=AssertionError('must not re-extract for motion edits')):
            edited=self.make(previous_preview_id=received['preview_id'],instruction='左边慢一点，最后多留半秒')
            self.assertEqual(data['pack_id'],edited['preview_request']['pack_id'])
            a=data['plan']['sequence'];b=edited['preview_request']['plan']['sequence']
            left=min(edited['layers'],key=lambda e:e['box'][0])['asset_id']
            self.assertGreater(next(m for m in b if m['asset_id']==left)['seconds'],next(m for m in a if m['asset_id']==left)['seconds'])
            with self.assertRaises(HTTPException):self.make(instruction='换一个新的主角',previous_preview_id=received['preview_id'])
            self.record['scenes'][0]['text']='语义已变';self.write()
            with self.assertRaises(HTTPException) as e:self.make(previous_preview_id=received['preview_id'])
            self.assertEqual(e.exception.status_code,409)

    def test_evidence_is_not_recreated_or_cut_by_optional_model(self):
        self.record['shots'][0]['intent']='保持原始切片和比例尺完整';self.write()
        with patch.object(design,'_model',return_value=([dict(name='新框',box=[20,75,190,126],cue='cue-A')],'乱选')):
            with self.assertRaises(HTTPException):self.make(engine='model',confirm_model=True)
        self.words.return_value=[]
        with patch.object(design,'_model',side_effect=AssertionError('must decline before charge')):
            with self.assertRaises(HTTPException) as e:self.make(engine='model',confirm_model=True)
            self.assertIn('保留静态',e.exception.detail)

    def test_flat_photo_has_actionable_no_fit_and_group_label_uses_actual_neighbor(self):
        self.assertEqual(design._bounds(Image.new('RGB',(240,180),'white')),[])
        fixed=[(10,60,100,90),(10,110,80,123),(10,129,130,152)]
        rows=[dict(text='第一医院'),dict(text='赵晓研究员与林清研究员')]
        words=[dict(text=t,box=[12,y,44,y+3]) for t,y in [('第一医院',65),('共同第一作者',114),('赵晓',133)]]
        with patch.object(design,'_bounds',return_value=fixed):
            found,_=design._local_regions(self.pic,words,{},rows)
        self.assertEqual([entry[2][0] for entry in found],[0,1,1])


if __name__=='__main__':unittest.main()
