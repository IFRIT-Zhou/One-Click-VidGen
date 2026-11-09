"""Offline tests: model responses are fixtures. No login, upload or paid generation."""
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw
from pydantic import ValidationError

from backend.app import layer_image_studio as layer, image_studio as studio


class StudioLayersTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.source = self.folder/'source.png'
        self.rgba = Image.new('RGBA', (80,40), (77,43,10,255))
        ImageDraw.Draw(self.rgba).rectangle((0,0,19,39),fill=(255,0,0,0))
        self.rgba.save(self.source)
        self.bounds = layer._square_reference(self.source,self.folder/'ref.png')
        self.meta = {'content_box':self.bounds}
        self.ids = dict(project_id='1'*32,pack_id='2'*32)

    def test_crop_only_reference_and_model_alpha_not_model_color(self):
        self.assertEqual(self.bounds,[0,256,1024,768])
        with Image.open(self.folder/'ref.png') as ref:
            self.assertEqual(ref.getpixel((2,500)),(48,48,48))
        model = Image.new('RGB',(1024,1024),'black')
        ImageDraw.Draw(model).rectangle((0,256,511,767),fill='white')
        out = layer.process_proposal(self.folder,{'layer_matte':self.meta},model)
        self.assertEqual(out.size,self.rgba.size)
        self.assertEqual(out.convert('RGB').tobytes(),self.rgba.convert('RGB').tobytes())
        self.assertEqual(out.getpixel((2,20))[3],0)
        self.assertEqual(out.getpixel((30,20))[3],255)
        self.assertEqual(out.getpixel((70,20))[3],0)

    def test_malformed_mattes_fail_closed(self):
        for size,color in [((1024,1024),'white'),((1024,1024),'black'),((256,256),'red'),((512,256),'black')]:
            with self.subTest(size=size,color=color), self.assertRaises(ValueError):
                layer.process_proposal(self.folder,{'layer_matte':self.meta},Image.new('RGB',size,color))
        self.assertFalse((self.folder/'mask.png').exists())

    def test_plate_retains_every_pixel_outside_original_hole(self):
        mask = Image.new('L',self.rgba.size)
        ImageDraw.Draw(mask).rectangle((40,10,60,30),fill=255)
        mask.save(self.folder/'selection.png')
        out = layer.process_proposal(self.folder,{'layer_plate':self.meta},Image.new('RGB',(512,512),'cyan'))
        for y in range(out.height):
            for x in range(out.width):
                self.assertEqual(out.getpixel((x,y)),(0,255,255,255) if mask.getpixel((x,y)) else self.rgba.getpixel((x,y)))

    def test_explicit_consent_and_typed_ids(self):
        req=dict(**self.ids,asset_id='layer_01',subject='仅保留人体')
        for extra in [{},{'confirm_reference_upload':False},{'confirm_reference_upload':True,'asset_id':'../a'},
                      {'confirm_reference_upload':True,'subject':'  '},{'confirm_reference_upload':True,'local_path':'H:/other'}]:
            with self.subTest(extra=extra),self.assertRaises(ValidationError):
                layer.MatteInput(**(req|extra))
        with self.assertRaises(ValidationError):
            layer.PlateInput(**self.ids,confirm_reference_upload=True)

    def test_plate_protection_optional_bounded_and_passed_to_studio(self):
        valid = dict(**self.ids, confirm_plate_upload=True)
        self.assertEqual(layer.PlateInput(**valid).preserve_background, [])
        for value in [['  '], ['x' * 121], ['医院'] * 17]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                layer.PlateInput(**valid, preserve_background=value)
        args = layer.PlateInput(**valid, preserve_background=[' 原图可见的球棍连接 ', '左侧医院轮廓'])
        self.assertEqual(args.preserve_background[0], '原图可见的球棍连接')
        self.rgba.getchannel('A').save(self.folder/'plate-mask.png')
        with patch('backend.app.image_layer_packs.owned_project', return_value=self.folder), \
                patch('backend.app.image_layer_packs.pack_asset_path', return_value=self.source), \
                patch.object(layer, 'require_user', return_value={'id': 42}), \
                patch.object(studio, 'ROOT', self.folder/'studio'), \
                patch.object(studio, 'config_for', return_value=({}, None)) as cfg, patch.object(studio,'enqueue'):
            record = layer.submit(args, object())
        prompt = cfg.call_args.args[1].prompt
        self.assertEqual(record['prompt'], prompt)
        for phrase in [*args.preserve_background, '不得改变连接', '可编辑底框']:
            self.assertIn(phrase, prompt)
        self.assertIn('无缝底板', layer.build_plate_prompt(args.preserve_background, seamless=True))

    def test_submit_snapshots_only_owned_layer_and_pins_ocv_ican(self):
        args=layer.MatteInput(**self.ids,asset_id='layer_01',subject='肝脏',confirm_reference_upload=True)
        with patch('backend.app.image_layer_packs.owned_project',return_value=self.folder) as owner, \
                patch('backend.app.image_layer_packs.pack_asset_path',return_value=self.source) as resolve, \
                patch.object(layer,'require_user',return_value={'id':42}), patch.object(studio,'ROOT',self.folder/'studio'), \
                patch.object(studio,'config_for',return_value=({'method':'ican'},None)) as config, patch.object(studio,'enqueue') as queue:
            record=layer.submit(args,object())
            owner.assert_called_once()
            resolve.assert_called_once_with(self.folder,args.pack_id,'layer_01')
            request=config.call_args.args[1]
            self.assertEqual((request.provider,request.method,request.size),('pool','ican','1024x1024'))
            self.assertTrue(record['layer_matte']['requires_review'])
            self.assertEqual(record['references'],['ref_1.png'])
            self.assertNotIn('source.png',record['references'])
            self.assertTrue((queue.call_args.args[1]/'source.png').is_file())

    def test_native_queue_worker_uses_ican_and_saves_original_size_rgba(self):
        model=Image.new('RGB',(256,256),'black')
        ImageDraw.Draw(model).rectangle((0,64,127,191),fill='white')
        buffer=io.BytesIO()
        model.save(buffer,format='PNG')
        record=dict(id='a'*32,prompt='输出蒙版',ratio='1:1',resolution='1024x1024',references=['ref.png'],
                    layer_matte=self.meta)
        config=dict(cloud_pool='1',method='ican',size='1024x1024',api_key='mock',endpoint='https://example.invalid/g',
                    query_url='https://example.invalid/q',upload_url='https://example.invalid/u')
        session=MagicMock()
        session.__enter__.return_value=session
        upload,submit,query,download=[MagicMock(status_code=200) for _ in range(4)]
        upload.json.return_value={'data':{'download_url':'https://example.invalid/ref'}}
        submit.json.return_value={'data':{'taskId':'m1'}}
        query.json.return_value={'data':{'status':'SUCCESS','imageUrl':'https://example.invalid/result'}}
        download.content=buffer.getvalue()
        session.request.side_effect=[upload,submit,query,download]
        with patch.object(studio.requests,'Session',return_value=session):
            studio.execute(42,self.folder,record,config)
        self.assertEqual(record['status'],'completed',record.get('message'))
        self.assertEqual(session.request.call_count,4)
        payload=session.request.call_args_list[1].kwargs['json']
        self.assertEqual((payload['model'],payload['method'],payload['imageUrls']),('gpt-image-2.5','ican',['https://example.invalid/ref']))
        with Image.open(self.folder/'result.png') as out:
            self.assertEqual(out.mode,'RGBA')
            self.assertEqual(out.getpixel((30,20)),self.rgba.getpixel((30,20)))
            self.assertEqual(out.getpixel((70,20))[3],0)
        self.assertTrue((self.folder/'mask.png').is_file())
        self.assertEqual(json.loads((self.folder/'record.json').read_text(encoding='utf-8'))['width'],80)
