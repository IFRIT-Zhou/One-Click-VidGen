"""Material inventory contracts: no renderer, network, or model calls."""
import copy
import unittest

from backend.app.vibe_layered_inventory import Inventory, DIRECTOR
from backend.app.vibe_layered_limits import MAX_INVENTORY_TEXTS


class V2InventoryTests(unittest.TestCase):
    def setUp(self):
        self.context = {'canvas': [1920, 1080], 'duration': 12}
        self.sources = {'core': 'core.png', 'ref_03': 'photo.png', 'background_ref': 'back.png'}
        self.raw = dict(background='extract', background_color='#153047',
            background_notes='保留渐变、粒子和底部光带；排除主体与文字', background_is_uniform=False,
            foreground_summary=dict(has_nontext_subjects=True, subject_count=2,
                description='左侧为完整语义图标组，右侧为独立照片，应分别提取'),
            subjects=[dict(name='左侧图标组', source='core', source_crop=[.04, .2, .35, .8],
                           treatment='cutout', embedded_text=[]),
                      dict(name='右侧研究照片', source='ref_03', source_crop=[0, 0, 1, 1],
                           treatment='preserve', embedded_text=[], evidence_note='原始照片必须保真')],
            texts=[dict(text='两组研究结果', source_box=[.2, .02, .8, .15]),
                   dict(text='32 对样本', source_box=[.4, .4, .6, .6])],
            decorations=[dict(kind='arrow', description='中央两侧各一条细双向指示线', source_box=[.35, .45, .65, .5])])

    def test_multiple_subject_inventory_adapts_to_material_design_without_changing_sources(self):
        raw=copy.deepcopy(self.raw)
        inventory=Inventory.model_validate(raw)
        design=inventory.to_design(self.context,self.sources)
        self.assertEqual(raw,self.raw)
        self.assertEqual([s.source for s in design.subjects],['core','ref_03'])
        self.assertEqual(design.subjects[0].crop,(.04,.2,.35,.8))
        self.assertEqual(design.subjects[0].box,design.subjects[0].crop)
        self.assertEqual(design.subjects[1].treatment,'preserve')
        self.assertTrue(all(s.at==0 for s in design.subjects))
        self.assertEqual([label.text for label in design.labels],['两组研究结果','32 对样本'])
        self.assertTrue(all(label.font_size==32 and label.at==0 for label in design.labels))
        self.assertEqual(inventory.decorations[0].kind,'arrow')
        self.assertEqual(inventory.model_dump()['subjects'][1]['evidence_note'],'原始照片必须保真')

    def test_explicit_text_only_inventory_is_allowed_without_invented_subject(self):
        raw=copy.deepcopy(self.raw);raw['subjects']=[]
        raw['foreground_summary']=dict(has_nontext_subjects=False,subject_count=0,
            description='原图只有标题、数字及抽象光带',text_only_reason='确为纯文字和抽象装饰，没有需独立保留的照片、语义图标、人物或物件')
        design=Inventory.model_validate(raw).to_design(self.context,self.sources)
        self.assertEqual(design.subjects,[])
        self.assertEqual(len(design.labels),2)

    def test_dense_poster_preserves_every_text_and_region_through_adapter(self):
        # More than 16 is normal for bilingual/data posters. Inventory must
        # not force a model repair that merges unrelated positions or drops
        # labels simply to fit the old prototype capacity.
        for count in (17, 21, MAX_INVENTORY_TEXTS):
            raw=copy.deepcopy(self.raw)
            raw['texts']=[dict(text=f'指标 {i+1}: {i+2}.5%',
                source_box=[.4, .02+i*.025, .6, .04+i*.025]) for i in range(count)]
            before=copy.deepcopy(raw)
            with self.subTest(count=count):
                inventory=Inventory.model_validate(raw)
                design=inventory.to_design(self.context,self.sources)
                self.assertEqual([label.text for label in design.labels],
                                 [row['text'] for row in raw['texts']])
                self.assertEqual([list(label.box) for label in design.labels],
                                 [row['source_box'] for row in raw['texts']])
                self.assertEqual(len(design.labels),count)
                self.assertEqual(raw,before)

    def test_text_capacity_is_advertised_and_excess_is_rejected_not_truncated(self):
        schema=Inventory.model_json_schema()['properties']['texts']
        self.assertEqual(schema['maxItems'],MAX_INVENTORY_TEXTS)
        self.assertIn(str(MAX_INVENTORY_TEXTS),schema['description'])
        self.assertIn('不得为了凑数量',schema['description'])
        raw=copy.deepcopy(self.raw)
        raw['texts']=[dict(text=f'原始指标{i}',source_box=[.1,.1,.2,.2])
                      for i in range(MAX_INVENTORY_TEXTS+1)]
        before=copy.deepcopy(raw)
        with self.assertRaisesRegex(ValueError,'too_long'):
            Inventory.model_validate(raw)
        self.assertEqual(raw,before)

    def test_empty_or_inconsistent_foreground_cannot_silently_drop_visible_subjects(self):
        cases=[]
        missing=copy.deepcopy(self.raw);missing['subjects']=[];cases.append(missing)
        wrong=copy.deepcopy(self.raw);wrong['foreground_summary']['subject_count']=1;cases.append(wrong)
        false=copy.deepcopy(self.raw);false['foreground_summary']['has_nontext_subjects']=False;cases.append(false)
        no_reason=copy.deepcopy(self.raw);no_reason['subjects']=[]
        no_reason['foreground_summary']=dict(has_nontext_subjects=False,subject_count=0,description='没有独立物件')
        cases.append(no_reason)
        bad_reason=copy.deepcopy(self.raw);bad_reason['foreground_summary']['text_only_reason']='有图标但想省步骤';cases.append(bad_reason)
        for raw in cases:
            with self.subTest(raw=raw['foreground_summary']),self.assertRaises(ValueError):Inventory.model_validate(raw)
        for value in ('true',1,None):
            raw=copy.deepcopy(self.raw);raw['foreground_summary']['has_nontext_subjects']=value
            with self.subTest(value=value),self.assertRaises(ValueError):Inventory.model_validate(raw)

    def test_source_identity_is_bounded_to_available_assets_and_core_poster_is_not_a_foreground(self):
        for source in ('ref_04','../../other.png','https://example.invalid/other.png'):
            raw=copy.deepcopy(self.raw);raw['subjects'][0]['source']=source
            with self.subTest(source=source),self.assertRaisesRegex(ValueError,'未绑定本镜素材'):
                Inventory.model_validate(raw).to_design(self.context,self.sources)
        raw=copy.deepcopy(self.raw);raw['subjects'][0].update(source='core',source_crop=[0,0,1,1],treatment='preserve')
        with self.assertRaisesRegex(ValueError,'整张 core'):
            Inventory.model_validate(raw).to_design(self.context,self.sources)
        with self.assertRaisesRegex(ValueError,'缺少 core'):
            Inventory.model_validate(self.raw).to_design(self.context,{'ref_03':'photo.png'})

    def test_background_resolution_keeps_decorations_and_uses_only_available_source(self):
        raw=copy.deepcopy(self.raw);raw['background']='background_ref'
        self.assertEqual(Inventory.model_validate(raw).to_design(self.context,self.sources).background,'background_ref')
        raw['background']='missing'
        with self.assertRaisesRegex(ValueError,'独立背景未绑定'):
            Inventory.model_validate(raw).to_design(self.context,self.sources)
        for choice in ('core','solid'):
            raw['background']=choice;raw['background_is_uniform']=True
            self.assertEqual(Inventory.model_validate(raw).to_design(self.context,self.sources).background,'extract')
        raw.update(background='solid',background_is_uniform=True,background_notes='均匀纯色')
        self.assertEqual(Inventory.model_validate(raw).to_design(self.context,self.sources).background,'solid')

    def test_texts_and_drawn_decoration_are_distinct_without_exempting_math_characters(self):
        for symbol in ('«','»','← →','━━━','↔'):
            raw=copy.deepcopy(self.raw);raw['texts'][0]['text']=symbol
            with self.subTest(symbol=symbol),self.assertRaisesRegex(ValueError,'装饰箭头/分隔线'):
                Inventory.model_validate(raw)
        for text in ('BMI ≥ 24','P < 0.05','1∶1匹配'):
            raw=copy.deepcopy(self.raw);raw['texts'][0]['text']=text
            self.assertEqual(Inventory.model_validate(raw).texts[0].text,text)
        raw=copy.deepcopy(self.raw);raw['decorations'][0]['points']=[[0,0],[1,1]]
        with self.assertRaises(ValueError):Inventory.model_validate(raw)

    def test_embedded_text_is_counted_once_in_same_core_region_but_distinct_occurrences_remain(self):
        raw=copy.deepcopy(self.raw)
        raw['subjects'][0]['embedded_text']=['图标组']
        raw['texts'].append(dict(text='图标组',source_box=[.1,.3,.2,.4]))
        with self.assertRaisesRegex(ValueError,'同一core区域重复计入'):
            Inventory.model_validate(raw)
        raw['texts'][-1]['source_box']=[.7,.85,.9,.95]
        inventory=Inventory.model_validate(raw)
        design=inventory.to_design(self.context,self.sources)
        self.assertIn('图标组',design.subjects[0].embedded_text)
        self.assertIn('图标组',[label.text for label in design.labels])
        raw['texts'].pop()
        design=Inventory.model_validate(raw).to_design(self.context,self.sources)
        self.assertNotIn('图标组',[label.text for label in design.labels])

    def test_reference_embedded_text_and_same_core_title_have_different_coordinate_spaces(self):
        raw=copy.deepcopy(self.raw)
        raw['subjects'][1].update(source='ref_03',source_crop=[.05,.1,.9,.85],
                                  embedded_text=['研究团队'])
        raw['texts'].append(dict(text='研究团队',source_box=[.1,.02,.8,.15]))
        # One occurrence is baked into the reference photo, the other is the
        # core poster title. Numeric box overlap is not semantic duplication:
        # source_crop is reference-space, source_box is core-space.
        before=copy.deepcopy(raw)
        inventory=Inventory.model_validate(raw)
        design=inventory.to_design(self.context,self.sources)
        self.assertEqual(design.subjects[1].source,'ref_03')
        self.assertEqual(design.subjects[1].crop,(.05,.1,.9,.85))
        self.assertEqual(design.subjects[1].embedded_text,['研究团队'])
        label=next(item for item in design.labels if item.text=='研究团队')
        self.assertEqual(label.box,(.1,.02,.8,.15))
        self.assertEqual(raw,before)

    def test_schema_never_asks_inventory_model_for_final_layout_font_or_timing(self):
        schema=Inventory.model_json_schema()
        for name in ('InventorySubject','InventoryText','InventoryDecoration','ForegroundSummary'):
            self.assertFalse(schema['$defs'][name]['additionalProperties'])
            for key in ('font_size','box','at','enter','exit','motion','points','group'):
                self.assertNotIn(key,schema['$defs'][name]['properties'])
        raw=copy.deepcopy(self.raw);raw['subjects'][0]['at']=0
        with self.assertRaises(ValueError):Inventory.model_validate(raw)
        raw=copy.deepcopy(self.raw);raw['texts'][0]['font_size']=48
        with self.assertRaises(ValueError):Inventory.model_validate(raw)

    def test_transport_marker_is_only_ignored_at_exact_top_level_without_mutating_input(self):
        raw={**copy.deepcopy(self.raw),'type':'json_object'};before=copy.deepcopy(raw)
        self.assertNotIn('type',Inventory.model_validate(raw).model_dump())
        self.assertEqual(raw,before)
        for value in ('json_schema','JSON_OBJECT',None):
            with self.subTest(value=value),self.assertRaises(ValueError):
                Inventory.model_validate({**copy.deepcopy(self.raw),'type':value})
        for key in ('subjects','texts','decorations'):
            raw=copy.deepcopy(self.raw);raw[key][0]['type']='json_object'
            with self.subTest(key=key),self.assertRaises(ValueError):Inventory.model_validate(raw)

    def test_all_source_regions_must_be_positive_normalized_rectangles(self):
        for key,coordinate in (('subjects','source_crop'),('texts','source_box'),('decorations','source_box')):
            for rectangle in ([.7,0,.2,1],[0,.5,1,.5],[0,0,1.2,1]):
                raw=copy.deepcopy(self.raw);raw[key][0][coordinate]=rectangle
                with self.subTest(key=key,rectangle=rectangle),self.assertRaises(ValueError):Inventory.model_validate(raw)
        raw=copy.deepcopy(self.raw);raw['subjects'][1]['evidence_note']=' '
        with self.assertRaisesRegex(ValueError,'不能为空|不能仅为空白'):Inventory.model_validate(raw)

    def test_director_requires_full_foreground_and_title_inventory_in_one_bounded_call(self):
        for instruction in ('不是排版师或动画导演','逐项清点','成组图标','没有独立参考','texts逐字记录',
                            '漏主标题','preserve保真','decorations','不要发起额外自检调用'):
            self.assertIn(instruction,DIRECTOR)


if __name__=='__main__':unittest.main()
