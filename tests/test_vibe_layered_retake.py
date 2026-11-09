"""Local, bounded material retakes: real cropping; no model or network calls."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from PIL import Image

from backend.app import vibe_layered_retake as retake
from backend.app.vibe_layered import Design


class SourceRetakeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.core = self.root/'core.png'
        self.reference = self.root/'reference.png'
        self.old = self.root/'old.png'
        Image.new('RGBA', (200, 120), (30, 80, 150, 255)).save(self.core)
        Image.new('RGBA', (160, 100), (120, 80, 50, 255)).save(self.reference)
        Image.new('RGBA', (20, 20), (10, 20, 30, 255)).save(self.old)
        self.sources = {'core':self.core, 'ref_7':self.reference}
        self.assets = {'background':self.core, 'subject_0':self.old, 'subject_0_original':self.old,
                       'subject_1':self.old, 'subject_1_original':self.old}
        self.design = Design.model_validate(dict(background='extract', background_color='#123456',
            subjects=[dict(source='core', crop=[.2,.2,.4,.6], box=[.1,.2,.4,.6], treatment='cutout', at=0,
                           embedded_text=['甲组']),
                      dict(source='ref_7', crop=[.2,.2,.8,.8], box=[.5,.2,.9,.7], treatment='preserve', at=0)],
            labels=[dict(text='标题 32', box=[.1,.1,.9,.2], at=0, color='#FFFFFF', font_size=32)]))
        self.request = dict(asset_id='subject_0', source_crop=[.1,.1,.5,.7], reason='原裁片遗漏两侧的主体边缘')

    def test_strict_schema_rejects_paths_identity_changes_and_bad_coordinates(self):
        cases = [dict(self.request, source='ref_7'), dict(self.request, local_path='other.png'),
                 dict(self.request, asset_id='subject_8'), dict(self.request, asset_id='../core'),
                 dict(self.request, source_crop=[.5,.1,.1,.7]),
                 dict(self.request, source_crop=[0,0,1,1.1]),
                 dict(self.request, source_crop=[False,0,1,1]), dict(self.request, reason='  ')]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                retake.SourceRetake.model_validate(raw)

    def test_manifest_fixes_source_identity_and_reports_real_dimensions(self):
        manifest = retake.source_manifest(self.design, self.sources)
        self.assertEqual(manifest, [
            dict(asset_id='subject_0', source_id='core', source_image_id='core',
                 current_source_crop=[.2,.2,.4,.6], treatment='cutout', source_size=[200,120]),
            dict(asset_id='subject_1', source_id='ref_7', source_image_id='retake_source_ref_7',
                 current_source_crop=[.2,.2,.8,.8], treatment='preserve', source_size=[160,100])])

    def test_validation_is_read_only_and_rejects_unknown_duplicate_unchanged_or_missing(self):
        before = sorted(self.root.iterdir())
        retake.validate_retakes([self.request], self.design, self.sources)
        self.assertEqual(before, sorted(self.root.iterdir()))
        for requests in ([self.request, self.request], [dict(self.request,asset_id='subject_2')],
                         [dict(self.request,source_crop=[.2,.2,.4,.6])],
                         [dict(self.request,source_crop=[.20001,.20001,.39999,.59999])]):
            with self.subTest(requests=requests), self.assertRaises(ValueError):
                retake.validate_retakes(requests, self.design, self.sources)
        with self.assertRaisesRegex(ValueError,'缺失'):
            retake.validate_retakes([self.request],self.design,{'ref_7':self.reference})
        with self.assertRaisesRegex(ValueError,'数量'):
            retake.validate_retakes([self.request]*3,self.design,self.sources)

    def test_preserve_uses_original_pixels_and_leaves_every_other_field_untouched(self):
        request = dict(self.request,asset_id='subject_1',source_crop=[.1,.1,.9,.9])
        before = self.design.model_dump()
        input_bytes = {path:path.read_bytes() for path in self.sources.values()}
        old_bytes = self.old.read_bytes()
        with patch.object(retake.rmbg,'run_cutouts') as rmbg:
            updated, assets, audit = retake.apply_retakes([request],self.design,self.sources,self.assets,self.root/'new',lambda:False)
        rmbg.assert_not_called()
        self.assertEqual(self.design.model_dump(),before)
        self.assertEqual(updated.labels,self.design.labels)
        self.assertEqual(updated.subjects[0],self.design.subjects[0])
        expected = dict(before['subjects'][1],crop=(.1,.1,.9,.9))
        self.assertEqual(updated.subjects[1].model_dump(),expected)
        self.assertEqual(assets['subject_0'],self.old)
        self.assertEqual(assets['subject_1'],assets['subject_1_original'])
        self.assertEqual(self.assets['subject_1'],self.old)
        with Image.open(assets['subject_1']) as actual,Image.open(self.reference) as original:
            self.assertEqual(actual.tobytes(),original.crop((16,10,144,90)).tobytes())
        self.assertEqual(self.old.read_bytes(),old_bytes)
        for path, original in input_bytes.items():self.assertEqual(path.read_bytes(),original)
        self.assertEqual(audit[0]['source_id'],'ref_7')
        self.assertFalse(audit[0]['rmbg_used'])

    def _cutout(self,sources,folder,**kwargs):
        self.assertIsNone(kwargs['trim_padding'])
        result = {}
        for name,path in sources.items():
            with Image.open(path) as source:
                rgba = source.convert('RGBA');rgba.putpixel((0,0),(0,0,0,0))
                output = Path(folder)/(name+'-rgba.png');rgba.save(output)
                result[name] = output
        return result

    def test_cutout_keeps_raw_and_masked_variants_at_identical_pixel_bounds(self):
        callback = lambda:False
        with patch.object(retake.rmbg,'run_cutouts',side_effect=self._cutout) as rmbg:
            updated, assets, audit = retake.apply_retakes([self.request],self.design,self.sources,self.assets,self.root/'new',callback)
        rmbg.assert_called_once()
        self.assertIs(rmbg.call_args.kwargs['should_stop'],callback)
        with Image.open(assets['subject_0']) as cutout,Image.open(assets['subject_0_original']) as original:
            self.assertEqual(cutout.size,original.size)
            self.assertEqual(cutout.size,(80,72))
            self.assertEqual(cutout.getpixel((2,2)),original.getpixel((2,2)))
        self.assertEqual(updated.subjects[0].embedded_text,['甲组'])
        self.assertEqual(updated.subjects[0].source,'core')
        self.assertTrue(audit[0]['rmbg_used'])

    def test_existing_alpha_needs_no_second_rmbg_and_empty_crop_fails_preflight(self):
        with Image.open(self.core) as source:
            rgba=source.convert('RGBA');rgba.putpixel((20,12),(30,80,150,0));rgba.save(self.core)
        with patch.object(retake.rmbg,'run_cutouts') as rmbg:
            retake.apply_retakes([self.request],self.design,self.sources,self.assets,self.root/'alpha')
        rmbg.assert_not_called()
        Image.new('RGBA',(200,120),(0,0,0,0)).save(self.core)
        with self.assertRaisesRegex(ValueError,'完全透明'):
            retake.apply_retakes([self.request],self.design,self.sources,self.assets,self.root/'empty')
        self.assertFalse((self.root/'empty').exists())

    def test_bad_rmbg_result_is_not_returned_or_committed_to_inputs(self):
        for kind in ('missing','wrong_size','empty'):
            def bad(sources,folder,**kwargs):
                if kind=='missing':return {}
                output=Path(folder)/'subject_0-rgba.png'
                Image.new('RGBA',(2,2) if kind=='wrong_size' else (80,72),(0,0,0,0)).save(output)
                return {'subject_0':output}
            with self.subTest(kind=kind),patch.object(retake.rmbg,'run_cutouts',side_effect=bad),self.assertRaises(ValueError):
                retake.apply_retakes([self.request],self.design,self.sources,self.assets,self.root/kind)
            self.assertEqual(self.design.subjects[0].crop,(.2,.2,.4,.6))
            self.assertEqual(self.assets['subject_0'],self.old)

    def test_reused_destination_is_rejected_and_cancel_never_returns_a_new_binding(self):
        folder=self.root/'existing';folder.mkdir()
        target=folder/'subject_0-original.png';target.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            retake.apply_retakes([self.request],self.design,self.sources,self.assets,folder)
        self.assertEqual(target.read_bytes(),b'keep')
        with self.assertRaises(InterruptedError):
            retake.apply_retakes([self.request],self.design,self.sources,self.assets,self.root/'cancelled',lambda:True)
        self.assertFalse((self.root/'cancelled').exists())
        stopped=[False]
        def cancelled_after_cutout(*args,**kwargs):
            result=self._cutout(*args,**kwargs);stopped[0]=True;return result
        with patch.object(retake.rmbg,'run_cutouts',side_effect=cancelled_after_cutout),self.assertRaises(InterruptedError):
            retake.apply_retakes([self.request],self.design,self.sources,self.assets,self.root/'stopped',lambda:stopped[0])
        self.assertEqual(self.assets['subject_0'],self.old)

    def test_core_cannot_be_replaced_with_whole_poster_in_preserve_mode(self):
        original=self.design.model_copy(update={'subjects':[
            self.design.subjects[0].model_copy(update={'treatment':'preserve'})]})
        with self.assertRaisesRegex(ValueError,'整张 core'):
            retake.validate_retakes([dict(self.request,source_crop=[0,0,1,1])],original,self.sources)


if __name__=='__main__':unittest.main()
