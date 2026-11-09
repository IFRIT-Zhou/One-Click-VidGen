"""Offline multimodal-image alpha regressions; no model/network requests."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw, ImageOps

from backend.app import vibe_cloud_client as client


def encoded(image, **save_options):
    source=io.BytesIO()
    image.save(source, format='PNG', **save_options)
    source.seek(0)
    return source


def decode(block):
    assert block['type']=='image_url' and block['image_url']['detail']=='high'
    prefix, payload=block['image_url']['url'].split(',',1)
    assert prefix=='data:image/jpeg;base64'
    with Image.open(io.BytesIO(base64.b64decode(payload))) as image:
        image.load()
        return image.copy()


def legacy_block(source):
    with Image.open(source) as image:
        rgb=ImageOps.exif_transpose(image).convert('RGB')
        rgb.thumbnail((2000,2000),Image.Resampling.LANCZOS)
        output=io.BytesIO()
        rgb.save(output,format='JPEG',quality=91)
    return output.getvalue()


class VibeImageAlphaTests(unittest.TestCase):
    def assert_pixel_close(self, actual, expected, tolerance=3):
        for component, wanted in zip(actual, expected):
            self.assertLessEqual(abs(component-wanted), tolerance, (actual, expected))

    def test_hidden_rgb_never_reappears_even_when_resampled(self):
        blocks=[]
        for hidden in [(255,0,0,0),(0,255,0,0),(255,0,255,0)]:
            image=Image.new('RGBA',(2400,160),hidden)
            ImageDraw.Draw(image).rectangle((100,30,900,130),fill=(30,90,180,255))
            blocks.append(client.image_block(encoded(image)))
        self.assertEqual(blocks[0],blocks[1])
        self.assertEqual(blocks[0],blocks[2])
        result=decode(blocks[0])
        self.assertEqual(result.size,(2000,133))
        r,g,b=result.getpixel((1500,60))
        self.assertLessEqual(max(r,g,b)-min(r,g,b),2)
        self.assertGreater(r,205)

    def test_half_transparent_pixels_blend_with_actual_checkered_background(self):
        result=decode(client.image_block(encoded(Image.new('RGBA',(64,64),(255,0,0,128)))))
        for point, base in [((8,8),236),((24,8),216)]:
            expected=tuple(round((foreground*128+base*127)/255) for foreground in (255,0,0))
            self.assert_pixel_close(result.getpixel(point),expected)

    def test_palette_transparency_hides_palette_rgb_and_preserves_opaque_subject(self):
        image=Image.new('P',(64,64),0)
        image.putpalette([255,0,255, 20,100,180]+[0]*762)
        ImageDraw.Draw(image).rectangle((32,0,63,63),fill=1)
        result=decode(client.image_block(encoded(image,transparency=0)))
        self.assert_pixel_close(result.getpixel((8,8)),(236,236,236))
        self.assert_pixel_close(result.getpixel((24,8)),(216,216,216))
        self.assert_pixel_close(result.getpixel((48,32)),(20,100,180))

    def test_palette_partial_alpha_and_luminance_alpha_are_honored(self):
        palette=Image.new('P',(64,64),0)
        palette.putpalette([255,0,0]+[0]*765)
        partial=decode(client.image_block(encoded(palette,transparency=bytes([128]+[255]*255))))
        self.assert_pixel_close(partial.getpixel((8,8)),(246,118,118))
        luminance=decode(client.image_block(encoded(Image.new('LA',(64,64),(40,128)))))
        self.assert_pixel_close(luminance.getpixel((8,8)),(138,138,138))

    def test_rgb_png_transparency_key_is_honored(self):
        result=decode(client.image_block(encoded(Image.new('RGB',(64,64),(255,0,255)),transparency=(255,0,255))))
        self.assert_pixel_close(result.getpixel((8,8)),(236,236,236))

    def test_rgb_and_fully_opaque_rgba_retain_exact_legacy_jpeg_bytes(self):
        for mode,color in [('RGB',(21,84,173)),('RGBA',(21,84,173,255)),('L',83)]:
            with self.subTest(mode=mode):
                image=Image.new(mode,(96,64),color)
                source=encoded(image).getvalue()
                result=client.image_block(io.BytesIO(source))
                data=base64.b64decode(result['image_url']['url'].split(',',1)[1])
                self.assertEqual(data,legacy_block(io.BytesIO(source)))

    def test_fully_transparent_image_displays_neutral_checker_not_black_or_hidden_color(self):
        result=decode(client.image_block(encoded(Image.new('RGBA',(64,64),(1,255,37,0)))))
        self.assert_pixel_close(result.getpixel((8,8)),(236,236,236))
        self.assert_pixel_close(result.getpixel((24,8)),(216,216,216))
        self.assertNotEqual(result.getpixel((8,8)),result.getpixel((24,8)))
        for r,g,b in result.getdata():
            self.assertLessEqual(max(r,g,b)-min(r,g,b),2)
            self.assertGreaterEqual(min(r,g,b),210)

    def test_exif_orientation_and_thumbnail_limit_are_unchanged(self):
        exif=Image.Exif()
        exif[274]=6
        source=encoded(Image.new('RGBA',(2400,1200),(40,80,160,180)),exif=exif)
        result=decode(client.image_block(source))
        self.assertEqual(result.size,(1000,2000))
        self.assertIsNone(result.getexif().get(274))

    def test_source_file_bytes_and_metadata_are_never_modified(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'transparent.png'
            Image.new('RGBA',(64,64),(240,10,120,0)).save(path)
            original=path.read_bytes()
            stat=path.stat()
            client.image_block(path)
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),hashlib.sha256(original).digest())
            self.assertEqual(path.stat().st_mtime_ns,stat.st_mtime_ns)
            self.assertEqual(path.stat().st_size,stat.st_size)
            with Image.open(path) as source:
                self.assertEqual(source.mode,'RGBA')
                self.assertEqual(source.getpixel((0,0)),(240,10,120,0))

    def test_existing_pixel_budget_is_checked_before_transparency_conversion(self):
        image=MagicMock()
        image.width,image.height=10_000,4_001
        image.__enter__.return_value=image
        with patch.object(client.Image,'open',return_value=image), \
             patch.object(client.ImageOps,'exif_transpose') as transpose:
            with self.assertRaisesRegex(client.CloudCallError,'像素过大'):
                client.image_block('not-opened.png')
            transpose.assert_not_called()

    def test_chat_explains_inspection_checker_once_without_rereading_images(self):
        response=MagicMock()
        response.status_code=200
        response.__enter__.return_value=response
        response.iter_lines.return_value=[
            ('data: '+json.dumps({'choices':[{'delta':{'content':'{}'},'finish_reason':'stop'}]})).encode(),
            b'data: [DONE]',
        ]
        block={'type':'image_url','image_url':{'url':'data:image/jpeg;base64,offline','detail':'high'}}
        config=dict(model='offline',base_url='https://example.invalid',api_key='never-display-this')
        with patch.object(client.requests,'post',return_value=response) as post, \
             patch.object(client,'image_block',return_value=block) as prepare:
            client.chat_json(config,'system',{},dict(first='a.png',second='b.png'),lambda:False)
        content=post.call_args.kwargs['json']['messages'][1]['content']
        self.assertEqual(sum(item.get('text')==client.TRANSPARENCY_VIEW_NOTE for item in content),1)
        self.assertEqual(prepare.call_count,2)
        self.assertIn('不是素材内容',client.TRANSPARENCY_VIEW_NOTE)
        self.assertNotIn('never-display-this',json.dumps(content))


if __name__=='__main__':
    unittest.main()
