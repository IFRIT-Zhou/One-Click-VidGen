"""Deterministic source anchors; no model, network, rendering, or GPU calls."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from backend.app.vibe_layered_geometry import geometry_manifest, resolve_source_anchors
from backend.app.vibe_layered_assembly import Assembly, materialize_subjects, compile_assembly
from backend.app.vibe_materials import crop_asset


class LayeredGeometryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def fixture(self, size=(101,73), crop=(.101,.127,.803,.819), source_id='core'):
        source = self.root/f'{source_id}-{size[0]}-{size[1]}.png'
        Image.new('RGBA', size, (10,20,30,255)).save(source)
        sources = {source_id:source}
        if source_id != 'core':
            core = self.root/'core.png'
            Image.new('RGBA',(640,360),(20,30,40,255)).save(core)
            sources['core'] = core
        prepared = crop_asset(sources,source_id,crop,self.root/'crops',f'crop{len(list((self.root).glob("crops/*")))}')
        inventory = SimpleNamespace(subjects=[SimpleNamespace(source=source_id,crop=crop)])
        assets = {'subject_0':prepared,'subject_0_original':prepared}
        return inventory,sources,assets

    def assembly(self, placement='source_anchor', crop=(0,0,1,1), variant='cutout', box=None):
        return SimpleNamespace(subjects=[SimpleNamespace(asset_id='subject_0',placement=placement,
            prepared_crop=crop,prepared_variant=variant,box=box,group='subject')])

    def test_manifest_matches_actual_floor_ceil_crop_for_varied_sizes(self):
        for size,crop in [((101,73),(.101,.127,.803,.819)),((2048,1152),(.03,.31,.31,.77)),
                          ((127,233),(0,0,1,1)),((320,180),(.125,.25,.875,.75))]:
            with self.subTest(size=size,crop=crop):
                inventory,sources,assets=self.fixture(size,crop)
                manifest=geometry_manifest(inventory,sources,assets)
                row=manifest['subjects'][0]
                self.assertTrue(row['anchor_available'])
                with Image.open(assets['subject_0']) as image:
                    self.assertEqual(row['prepared_size'],list(image.size))
                pixel=row['source_pixel_window']
                self.assertEqual(row['source_anchor_box'],[pixel[0]/size[0],pixel[1]/size[1],pixel[2]/size[0],pixel[3]/size[1]])

    def test_trimmed_piece_is_mapped_not_stretched_using_real_round_pixels(self):
        inventory,sources,assets=self.fixture()
        manifest=geometry_manifest(inventory,sources,assets)
        # Actual source is [10,9,82,60], hence prepared 72x51. Python round of
        # 2.5 -> 2 and 50.5 -> 50 must match the existing materializer exactly.
        row=manifest['subjects'][0]
        self.assertEqual(row['source_pixel_window'],[10,9,82,60])
        original=self.assembly(crop=(2.5/72,1.5/51,50.5/72,40.5/51),box=(.8,.8,.9,.9))
        result=resolve_source_anchors(original,manifest)
        self.assertEqual(result.subjects[0].box,(12/101,11/73,60/101,49/73))
        self.assertEqual(original.subjects[0].box,(.8,.8,.9,.9))
        self.assertEqual(result.subjects[0].prepared_crop,original.subjects[0].prepared_crop)

    def test_reference_crop_is_never_an_anchor_even_when_size_matches(self):
        inventory,sources,assets=self.fixture(source_id='reference_1')
        manifest=geometry_manifest(inventory,sources,assets)
        row=manifest['subjects'][0]
        self.assertFalse(row['anchor_available'])
        self.assertIsNone(row['source_anchor_box'])
        with self.assertRaisesRegex(ValueError,'独立参考图'):
            resolve_source_anchors(self.assembly(),manifest)
        manual=self.assembly('manual',box=(.1,.2,.3,.4))
        self.assertEqual(resolve_source_anchors(manual,manifest).subjects[0].box,manual.subjects[0].box)

    def test_exif_oriented_dimensions_match_crop_asset(self):
        source=self.root/'oriented.jpg'
        exif=Image.Exif();exif[274]=6
        Image.new('RGB',(120,80),(20,30,40)).save(source,exif=exif)
        crop=(.125,.25,.875,.75)
        prepared=crop_asset({'core':source},'core',crop,self.root/'exif','crop')
        inventory=SimpleNamespace(subjects=[SimpleNamespace(source='core',source_crop=crop)])
        manifest=geometry_manifest(inventory,{'core':source},{'subject_0':prepared})
        self.assertEqual(manifest['core_size'],[80,120])
        self.assertEqual(manifest['subjects'][0]['source_pixel_window'],[10,30,70,90])
        self.assertEqual(resolve_source_anchors(self.assembly(),manifest).subjects[0].box,crop)

    def test_retake_refreshes_anchor_and_does_not_reuse_previous_pixel_window(self):
        inventory,sources,assets=self.fixture(size=(200,120),crop=(.2,.2,.4,.6))
        old=geometry_manifest(inventory,sources,assets)
        updated=copy.deepcopy(inventory);updated.subjects[0].crop=(.1,.1,.5,.7)
        prepared=crop_asset(sources,'core',updated.subjects[0].crop,self.root/'retake','crop')
        current=geometry_manifest(updated,sources,{'subject_0':prepared,'subject_0_original':prepared})
        self.assertEqual(old['subjects'][0]['source_pixel_window'],[40,24,80,72])
        self.assertEqual(current['subjects'][0]['source_pixel_window'],[20,12,100,84])
        self.assertEqual(resolve_source_anchors(self.assembly(),current).subjects[0].box,(.1,.1,.5,.7))
        self.assertEqual(inventory.subjects[0].crop,(.2,.2,.4,.6))

    def test_resized_old_asset_is_explicitly_unanchorable_without_breaking_manual(self):
        inventory,sources,assets=self.fixture()
        resized=self.root/'resized.png';Image.new('RGBA',(30,30),(20,30,40,255)).save(resized)
        manifest=geometry_manifest(inventory,sources,{'subject_0':resized})
        self.assertFalse(manifest['subjects'][0]['anchor_available'])
        with self.assertRaisesRegex(ValueError,'实际 prepared 为 30x30'):
            resolve_source_anchors(self.assembly(),manifest)
        manual=self.assembly('manual',box=(.2,.3,.6,.7))
        self.assertEqual(resolve_source_anchors(manual,manifest).subjects[0].box,manual.subjects[0].box)

    def test_mismatched_variants_or_missing_bound_material_are_not_guessed(self):
        inventory,sources,assets=self.fixture()
        bad=self.root/'bad.png';Image.new('RGBA',(10,10)).save(bad)
        with self.assertRaisesRegex(ValueError,'像素边界不一致'):
            geometry_manifest(inventory,sources,{**assets,'subject_0_original':bad})
        with self.assertRaisesRegex(ValueError,'文件缺失'):
            geometry_manifest(inventory,sources,{})
        with self.assertRaisesRegex(ValueError,'未绑定'):
            geometry_manifest(SimpleNamespace(subjects=[SimpleNamespace(source='absent',crop=(0,0,1,1))]),sources,assets)

    def test_original_variant_has_identical_mapping_and_inputs_are_not_mutated(self):
        inventory,sources,assets=self.fixture()
        manifest=geometry_manifest(inventory,sources,assets)
        before=copy.deepcopy(manifest)
        result=resolve_source_anchors(self.assembly(variant='original'),manifest)
        self.assertEqual(list(result.subjects[0].box),manifest['subjects'][0]['source_anchor_box'])
        self.assertEqual(manifest,before)
        result.subjects[0].group='changed'
        raw={'subjects':[{'asset_id':'subject_0','prepared_variant':'original','prepared_crop':[0,0,1,1],
                          'placement':'source_anchor','box':None,'group':'old'}]}
        copied=resolve_source_anchors(raw,manifest)
        self.assertIsNone(raw['subjects'][0]['box'])
        self.assertEqual(copied['subjects'][0]['box'],tuple(manifest['subjects'][0]['source_anchor_box']))

    def test_legacy_manual_needs_no_anchor_and_invalid_crop_is_rejected(self):
        legacy=SimpleNamespace(subjects=[SimpleNamespace(box=(.1,.2,.3,.4))])
        self.assertEqual(resolve_source_anchors(legacy,None).subjects[0].box,(.1,.2,.3,.4))
        inventory,sources,assets=self.fixture()
        manifest=geometry_manifest(inventory,sources,assets)
        for crop in [(0,0,.00001,.00001),(.8,0,.2,1),(False,0,1,1),(0,0,float('nan'),1)]:
            with self.subTest(crop=crop),self.assertRaises(ValueError):
                resolve_source_anchors(self.assembly(crop=crop),manifest)
        with self.assertRaisesRegex(ValueError,'当前版本'):
            resolve_source_anchors(self.assembly(),{})

    def test_real_assembly_materializer_and_compiler_use_the_same_trimmed_pixels(self):
        inventory,sources,assets=self.fixture(size=(200,120),crop=(.1,.1,.5,.7))
        assets['background']=sources['core']
        manifest=geometry_manifest(inventory,sources,assets)
        assembly=Assembly.model_validate(dict(background_color='#102030',subjects=[dict(
            asset_id='subject_0',placement='source_anchor',prepared_variant='original',
            prepared_crop=[.125,.25,.875,.75],group='cohort')]))
        resolved=resolve_source_anchors(assembly,manifest)
        placed=materialize_subjects(resolved,assets,self.root/'placed')
        with Image.open(placed['placed_subject_0']) as image:
            self.assertEqual(image.size,(60,36))
        compiled=compile_assembly(resolved,{'canvas':[1000,600],'duration':12},placed)
        subject=next(node for node in compiled['elements'] if node['type']=='image' and node.get('role')!='background')
        self.assertEqual(subject['box'],[150,150,300,180])
        self.assertIsNone(assembly.subjects[0].box)
        self.assertEqual(assembly.subjects[0].prepared_crop,(.125,.25,.875,.75))

    def test_canvas_scaling_preserves_anchors_when_aspect_ratio_matches(self):
        for source_size,canvas in [((1600,900),(1920,1080)),((900,1600),(720,1280)),
                                   ((1024,1024),(900,900))]:
            with self.subTest(source_size=source_size,canvas=canvas):
                inventory,sources,assets=self.fixture(size=source_size)
                manifest=geometry_manifest(inventory,sources,assets,canvas=canvas)
                self.assertEqual(manifest['canvas'],list(canvas))
                self.assertTrue(manifest['subjects'][0]['anchor_available'])
                resolved=resolve_source_anchors(self.assembly(),manifest)
                self.assertEqual(list(resolved.subjects[0].box),manifest['subjects'][0]['source_anchor_box'])

    def test_landscape_portrait_and_different_ratio_disable_anchor_but_keep_manual(self):
        for source_size,canvas in [((1600,900),(1080,1920)),((900,1600),(1920,1080)),
                                   ((1600,900),(1000,1000))]:
            with self.subTest(source_size=source_size,canvas=canvas):
                inventory,sources,assets=self.fixture(size=source_size)
                manifest=geometry_manifest(inventory,sources,assets,canvas=canvas)
                self.assertFalse(manifest['subjects'][0]['anchor_available'])
                self.assertIsNone(manifest['subjects'][0]['source_anchor_box'])
                self.assertIn('宽高比不一致',manifest['subjects'][0]['anchor_unavailable_reason'])
                with self.assertRaisesRegex(ValueError,'宽高比不一致'):
                    resolve_source_anchors(self.assembly(),manifest)
                manual=self.assembly('manual',box=(.1,.2,.7,.8))
                self.assertEqual(resolve_source_anchors(manual,manifest).subjects[0].box,manual.subjects[0].box)

    def test_canvas_ratio_allows_only_one_pixel_short_edge_rounding(self):
        for source_size,near,far in [((1600,900),(1920,1081),(1920,1082)),
                                    ((900,1600),(1081,1920),(1082,1920))]:
            with self.subTest(source_size=source_size):
                inventory,sources,assets=self.fixture(size=source_size)
                self.assertTrue(geometry_manifest(inventory,sources,assets,near)['subjects'][0]['anchor_available'])
                self.assertFalse(geometry_manifest(inventory,sources,assets,far)['subjects'][0]['anchor_available'])
        inventory,sources,assets=self.fixture()
        for canvas in [(0,100),(True,100),(100.0,100),(100,),[100,100,100]]:
            with self.subTest(canvas=canvas),self.assertRaisesRegex(ValueError,'canvas'):
                geometry_manifest(inventory,sources,assets,canvas)


if __name__=='__main__':unittest.main()
