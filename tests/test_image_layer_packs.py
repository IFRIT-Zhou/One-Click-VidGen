import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageChops, ImageDraw

from backend.app import image_layer_packs as layers


class LayerPackTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.pid = 'f' * 32
        self.project = self.root / '1' / self.pid
        self.project.mkdir(parents=True)
        image = Image.new('RGBA', (160, 90), (21, 45, 58, 255))
        draw = ImageDraw.Draw(image)
        draw.ellipse((20, 20, 49, 49), fill=(176, 24, 32, 255))
        draw.rectangle((60, 20, 75, 35), fill=(80, 120, 255, 185))
        draw.line((80, 30, 90, 30), fill='white')
        self.source = self.project / 'core.png'
        image.save(self.source)
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.data = dict(source_sha256=self.sha, canvas=[80, 45],
            elements=[dict(shape='ellipse', name='s1', box=[9, 9, 26, 26]),
                      dict(shape='rect', name='s2', box=[30, 10, 38, 18])],
            cleanup=[dict(shape='rect', box=[39, 14, 46, 16])])
        self.record = self.project / 'record.json'
        self.record.write_text(json.dumps(dict(id=self.pid, revision=7, shots=[
            dict(id='s-1', image='core.png'), dict(id='s-2', image='core.png')], status='planning')))
        for p in [patch.object(layers.studio, 'ROOT', self.root),
                  patch.object(layers,'require_user',side_effect=lambda r:{'id':int(r.headers.get('x-user','1'))})]:
            p.start()
            self.addCleanup(p.stop)
        api = FastAPI()
        api.include_router(layers.router)
        self.client = TestClient(api)
        self.addCleanup(self.client.close)
        self.url = f'/api/video-studio/{self.pid}/shots/s-1/layer-packs'

    def test_complete_pack_rgba_clean_plate_ownership_and_immutable_source(self):
        before = self.record.read_bytes()
        response = self.client.post(self.url, json=self.data)
        self.assertEqual(response.status_code, 200, response.text)
        pack = response.json()
        self.assertFalse(pack['may_charge'])
        self.assertEqual(pack['elements'][0]['box_px'], [18,18,52,52])
        self.assertEqual(pack['source_size'], [160,90])
        self.assertEqual(pack['source_sha256'], self.sha)
        folder = self.project/'assets'/'layer_packs'/pack['pack_id']
        def downloaded(url):
            r = self.client.get(url)
            self.assertEqual(r.headers['content-type'],'image/png')
            with Image.open(io.BytesIO(r.content)) as im:
                return im.copy()
        first = downloaded(pack['elements'][0]['url'])
        self.assertEqual(first.mode,'RGBA')
        self.assertEqual(first.getpixel((0,0))[3],0)
        self.assertEqual(first.getpixel((17,17)),(176,24,32,255))
        self.assertEqual(downloaded(pack['elements'][1]['url']).getpixel((3,3)),(80,120,255,185))
        plate = downloaded(pack['clean_plate']['url'])
        for point in [(35,35),(65,25),(84,30)]:
            self.assertEqual(plate.getpixel(point),(21,45,58,255))
        with Image.open(folder/'plate-mask.png') as mask, Image.open(self.source) as orig:
            self.assertEqual(sorted(mask.getcolors()),sorted([(n,k) for n,k in mask.getcolors() if k in (0,255)]))
            self.assertEqual(mask.getpixel((84,30)),255)
            self.assertEqual(mask.getpixel((12,12)),0)
            outside = ImageChops.invert(mask)
            for channel in ImageChops.difference(plate,orig).split():
                self.assertIsNone(ImageChops.multiply(channel,outside).getbbox())
        self.assertEqual(self.client.get(pack['clean_plate']['url'], headers={'x-user':'2'}).status_code,404)
        self.assertEqual(self.client.post(self.url,json=self.data).json(),pack)
        other=self.client.post(self.url.replace('s-1','s-2'),json=self.data).json()
        self.assertNotEqual(other['pack_id'],pack['pack_id'])
        self.assertEqual(self.record.read_bytes(),before)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),self.sha)

    def test_stale_and_bad_requests_do_not_publish(self):
        samples = [
            (dict(source_sha256='0'*64),409),
            (dict(elements=[dict(name='x', shape='triangle',box=[0,0,3,3])]),422),
            (dict(elements=[dict(name='x', shape='rect',box=[10,4,0,8])]),422),
            (dict(elements=[dict(name='x', shape='rect',box=[False,4,9,8])]),422),
            (dict(canvas=[80,44]),422),
            (dict(elements=[dict(name='x',shape='rect',box=[0,0,80,45])]),422),
            (dict(local_path='../../secret'),422),
        ]
        for update,status in samples:
            with self.subTest(update=update):
                self.assertEqual(self.client.post(self.url,json={**self.data,**update}).status_code,status)
        self.assertFalse(list(self.project.glob('assets/layer_packs/*')))

    def test_escape_missing_asset_and_foreign_source(self):
        pack = self.client.post(self.url,json=self.data).json()
        base = pack['clean_plate']['url'].rsplit('/',1)[0]
        for asset in ['plate-mask.png','manifest.json','layer_09','layer_01.png','layer_99','..%2Frecord.json']:
            self.assertEqual(self.client.get(base+'/'+asset).status_code,404)
        foreign=self.project.parent/'other.png'
        foreign.write_bytes(self.source.read_bytes())
        self.record.write_text(json.dumps({'shots':[{'id':'s-1','image':'../other.png'}]}))
        self.assertEqual(self.client.post(self.url,json=self.data).status_code,404)


if __name__=='__main__':
    unittest.main()
