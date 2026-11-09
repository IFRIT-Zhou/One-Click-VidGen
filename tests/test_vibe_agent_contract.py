"""Offline task-agent contracts: capability interfaces, no provider calls."""
import copy
import json
import unittest

from pydantic import ValidationError

from backend.app import vibe_agent_contract as contract


def call(tool, arguments, **extra):
    return dict(tool=tool, summary='检查当前素材并决定下一步', arguments=arguments, **extra)


def inventory():
    return dict(background='solid', background_color='#102030', background_is_uniform=True,
        foreground_summary=dict(has_nontext_subjects=False, subject_count=0,
            description='仅有原文与均匀底色', text_only_reason='原图只有标题和背景，没有独立图像主体'),
        subjects=[], texts=[dict(text='测试标题', source_box=[.1,.1,.8,.3])], decorations=[])


class AgentContractTests(unittest.TestCase):
    def test_model_output_example_is_complete_executable_json_not_a_forced_step(self):
        example = json.loads(contract.TOOL_RESPONSE_EXAMPLE_JSON)
        name, arguments = contract.parse_tool_call(example)
        self.assertEqual(name, 'inspect_image')
        self.assertEqual(arguments.asset_id, 'core')
        self.assertIn(contract.TOOL_RESPONSE_EXAMPLE_JSON, contract.TOOL_RESPONSE_GUIDE)
        self.assertIn('不要求先调用此工具', contract.TOOL_RESPONSE_GUIDE)
        self.assertIn('自主选择', contract.TOOL_RESPONSE_GUIDE)
        self.assertNotIn('"arguments":{...}', contract.GUIDE)
        self.assertNotIn('"tool":"工具名"', contract.GUIDE)

    def test_tool_catalog_and_dispatch_cover_exactly_same_capabilities(self):
        self.assertEqual(contract.VERSION, 'tool-agent-layered-v4')
        names=[item['name'] for item in contract.TOOL_CATALOG]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), set(contract.ARGUMENT_MODELS))
        self.assertEqual(set(names),set(contract.ToolCall.model_json_schema()['properties']['tool']['enum']))
        for row in contract.TOOL_CATALOG:
            self.assertTrue(row['description'])
            self.assertEqual(row['parameters']['additionalProperties'],False)

    def test_each_tool_parses_independently_without_prescribed_sequence(self):
        examples={
            'inspect_image':dict(asset_id='core',region=[.1,.2,.8,.9]),
            'set_inventory':dict(inventory=inventory()),
            'prepare_subject':dict(subject_id='subject_2',source_crop=[0,.1,1,.9],treatment='preserve'),
            'edit_background':dict(prompt='保留原位反光，移除标题后自然补全'),
            'select_background':dict(color='#102030'),
            'measure_text':dict(probes=[dict(id='title',text='测试标题',font_size=72)]),
            'preview_scene':dict(code='function Scene(ctx) { return <Text id="title">测试标题</Text>; }'),
            'review_evidence':dict(preview_id='preview_3',explanation='核对引导线来源',element_ids=['title']),
            'finish':dict(preview_id='preview_3'),
            'report_blocker':dict(message='源图数据看不清，需要提供清晰原图。'),
        }
        # Reverse order deliberately: parsing is not a workflow state machine.
        for tool in reversed(list(examples)):
            raw=call(tool,examples[tool]);before=copy.deepcopy(raw)
            with self.subTest(tool=tool):
                actual,args=contract.parse_tool_call(raw)
                self.assertEqual(actual,tool)
                self.assertIsInstance(args,contract.ARGUMENT_MODELS[tool])
                self.assertEqual(raw,before)

    def test_unknown_tool_and_empty_envelope_are_actionable_errors(self):
        with self.assertRaisesRegex(ValueError,'未知Vibe工具.*inspect_image'):
            contract.parse_tool_call(call('run_shell',{'command':'echo no'}))
        for value in ({},None,[],call('finish',{'preview_id':'preview_1'},untrusted=True)):
            with self.subTest(value=value),self.assertRaises(ValidationError):
                contract.parse_tool_call(value)
        with self.assertRaisesRegex(ValueError,'summary'):
            contract.parse_tool_call(dict(tool='inspect_image',summary='  ',arguments={'asset_id':'core'}))

    def test_review_evidence_has_bounded_measured_anchors_not_a_self_approval_flag(self):
        base=dict(preview_id='preview_1',explanation='对照实际底图核实',text_queries=['研究结果'])
        for changes in ({'passed':True},{'approved':True},{'region':[.5,0,.4,1]},
                        {'region':[0,0,True,1]},{'region':[0,0,float('nan'),1]},
                        {'element_ids':['../private']},{'text_queries':[' ']},
                        {'text_queries':['word']*5},{'element_ids':['title']*7},
                        {'explanation':' '},{'preview_id':'G:/file.png'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                contract.parse_tool_call(call('review_evidence',{**base,**changes}))
        with self.assertRaises(ValueError):
            contract.parse_tool_call(call('review_evidence',dict(preview_id='preview_1',explanation='没有定位依据')))
        _,args=contract.parse_tool_call(call('review_evidence',dict(preview_id='preview_1',
            explanation='只给粗略搜索窗口，不能作为核实坐标',region=[.1,.2,.7,.8])))
        self.assertEqual(list(args.region),[.1,.2,.7,.8])
        _,args=contract.parse_tool_call(call('finish',dict(preview_id='preview_1',review_evidence_id='review_evidence_a')))
        self.assertEqual(args.review_evidence_id,'review_evidence_a')

    def test_only_exact_top_level_json_object_marker_is_accepted_without_mutation(self):
        raw=call('inspect_image',{'asset_id':'core'},type='json_object')
        before=copy.deepcopy(raw)
        self.assertEqual(contract.parse_tool_call(raw)[0],'inspect_image')
        self.assertEqual(raw,before)
        for changed in (call('inspect_image',{'asset_id':'core'},type='other'),
                        call('inspect_image',{'asset_id':'core','type':'json_object'})):
            with self.assertRaises(ValidationError):contract.parse_tool_call(changed)

    def test_asset_ids_are_not_paths_urls_or_shell_arguments(self):
        for value in ('../core.png','G:/secrets.png','https://example.invalid/a','a b','a;cmd','a'*81):
            with self.subTest(value=value),self.assertRaises(ValidationError):
                contract.parse_tool_call(call('inspect_image',{'asset_id':value}))
        for value in ('core','ref_a','scene:abc','subject_2_original','preview_1'):
            self.assertEqual(contract.parse_tool_call(call('inspect_image',{'asset_id':value}))[1].asset_id,value)

    def test_local_locator_is_bounded_read_only_and_does_not_accept_model_coordinates_as_truth(self):
        tool, args = contract.parse_tool_call(call('locate_elements', {}))
        self.assertEqual(tool, 'locate_elements')
        self.assertEqual(args.asset_id, 'core')
        self.assertIsNone(args.region)
        for extra in ({'asset_id':'G:/private.png'}, {'url':'https://example.invalid/a'},
                      {'verified':True}, {'text':' '}, {'region':[.5,.1,.2,.6]},
                      {'region':[0,0,float('nan'),1]}, {'text':'a'*181}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                contract.parse_tool_call(call('locate_elements', extra))
        _, args = contract.parse_tool_call(call('locate_elements',
            {'asset_id':'background','region':[.1,.2,.9,.8],'text':'0.043'}))
        self.assertEqual(args.text, '0.043')

    def test_regions_reject_reversed_empty_nonfinite_and_non_numeric_coordinates(self):
        for tool,key,base in (
            ('inspect_image','region',{'asset_id':'core'}),
            ('prepare_subject','source_crop',{'subject_id':'subject_0'})):
            for box in ([.8,0,.2,1],[0,.5,1,.5],[0,0,1.1,1],[0,0,float('nan'),1],
                        [False,0,1,1],['0',0,1,1],[0,0,1]):
                with self.subTest(tool=tool,box=box),self.assertRaises(ValueError):
                    contract.parse_tool_call(call(tool,{**base,key:box}))
        self.assertIsNone(contract.parse_tool_call(call('inspect_image',{'asset_id':'core'}))[1].region)
        self.assertEqual(contract.parse_tool_call(call('prepare_subject',{'subject_id':'subject_0'}))[1].source_crop,None)

    def test_select_background_requires_exactly_one_bound_id_or_color(self):
        for args in ({},{'asset_id':'core','color':'#ffffff'},{'color':'white'},
                     {'asset_id':'http://example.invalid'}):
            with self.subTest(args=args),self.assertRaises(ValueError):
                contract.parse_tool_call(call('select_background',args))
        self.assertEqual(contract.parse_tool_call(call('select_background',{'asset_id':'base_1'}))[1].asset_id,'base_1')

    def test_prepare_subject_keeps_route_choice_optional_but_cannot_expand_identity_namespace(self):
        args=contract.parse_tool_call(call('prepare_subject',{'subject_id':'subject_0'}))[1]
        self.assertIsNone(args.treatment)
        for value in ('subject_8','subject_0_original','../../secret'):
            with self.assertRaises(ValidationError):
                contract.parse_tool_call(call('prepare_subject',{'subject_id':value}))
        with self.assertRaises(ValidationError):
            contract.parse_tool_call(call('prepare_subject',{'subject_id':'subject_0','treatment':'invent'}))

    def test_edit_background_accepts_optional_intent_not_channel_or_spending_override(self):
        self.assertEqual(contract.parse_tool_call(call('edit_background',{}))[1].prompt,'')
        self.assertEqual(contract.parse_tool_call(call('edit_background',{'prompt':'  保留反光  '}))[1].prompt,'保留反光')
        for extra in ('api_key','image_route','max_image_requests','endpoint'):
            with self.assertRaises(ValidationError):
                contract.parse_tool_call(call('edit_background',{extra:'model supplied'}))
        with self.assertRaises(ValidationError):
            contract.parse_tool_call(call('edit_background',{'prompt':'x'*2001}))

    def test_edit_background_separates_validated_removals_and_preservation(self):
        arguments = dict(prompt='去掉残留数据色带',
            remove_regions=[dict(description='  完全移除渐变条及边缘  ', region=[.12,.3,.71,.43])],
            preserve_details=['  保留远景建筑与地面反光  '])
        original = copy.deepcopy(arguments)
        parsed = contract.parse_tool_call(call('edit_background', arguments))[1]
        self.assertEqual(arguments, original)
        self.assertEqual(parsed.remove_regions[0].description, '完全移除渐变条及边缘')
        self.assertEqual(parsed.preserve_details, ['保留远景建筑与地面反光'])
        for box in ([.8,0,.2,1], [0,.5,1,.5], [0,0,1.1,1], [0,0,float('nan'),1],
                    [False,0,1,1], ['0',0,1,1], [0,0,1], None):
            with self.subTest(box=box), self.assertRaises(ValueError):
                contract.EditBackgroundArgs(remove_regions=[dict(description='需移除的元素',region=box)])
        for changes in (dict(remove_regions=[dict(description=' ',region=[0,0,1,1])]),
                        dict(preserve_details=[' ']), dict(preserve_details=['x'*401]),
                        dict(remove_regions=[dict(description='条带',region=[0,0,1,1])]*25)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                contract.EditBackgroundArgs(**changes)

    def test_blocker_reason_is_optional_but_cannot_increase_budget(self):
        self.assertEqual(contract.ReportBlockerArgs(message='需要更清晰的来源图').reason, 'other')
        self.assertEqual(contract.ReportBlockerArgs(message='质量不足，选择停止',reason='quality').reason, 'quality')
        self.assertEqual(contract.ReportBlockerArgs(message='还需处理三张不同底图',reason='image_budget',
            required_image_requests=3).required_image_requests,3)
        for changes in (dict(reason='auto_retry'), dict(max_image_requests=10),
                        dict(required_image_requests=3),
                        dict(reason='image_budget',required_image_requests=True),
                        dict(reason='image_budget',required_image_requests=0)):
            with self.assertRaises(ValueError):
                contract.ReportBlockerArgs(message='失败说明', **changes)

    def test_measure_and_preview_share_existing_tool_payload_rules(self):
        args=contract.parse_tool_call(call('measure_text',{
            'probes':[dict(id='title',text='标题',font_size=64)]}))[1]
        self.assertEqual(args.to_turn('测量标题').action,'measure')
        with self.assertRaisesRegex(ValueError,'id不能重复'):
            contract.parse_tool_call(call('measure_text',{'probes':[
                dict(id='same',text='甲',font_size=64),dict(id='same',text='乙',font_size=64)]}))
        with self.assertRaises(ValueError):contract.parse_tool_call(call('measure_text',{'probes':[]}))
        code='function Scene(ctx) { return <Text id="title">标题</Text>; }'
        args=contract.parse_tool_call(call('preview_scene',{'code':code}))[1]
        self.assertEqual(args.to_turn('预览构图').action,'preview')
        self.assertEqual(args.to_turn('预览构图').code,code)
        for bad in ({'code':'  '},{'code':code,'probes':[]},
                    {'code':code,'embedded_text':{f'asset_{i}':[] for i in range(25)}}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                contract.parse_tool_call(call('preview_scene',bad))

    def test_report_blocker_needs_real_message_but_is_not_forced_to_a_template(self):
        with self.assertRaises(ValueError):contract.parse_tool_call(call('report_blocker',{'message':'   '}))
        message='这一片区域看不清，需要更高分辨率的原图后继续。'
        self.assertEqual(contract.parse_tool_call(call('report_blocker',{'message':message}))[1].message,message)

    def test_preview_quality_is_an_optional_choice_not_a_new_fixed_sequence(self):
        code='function Scene(ctx) { return <Text id="title">标题</Text>; }'
        self.assertEqual(contract.PreviewSceneArgs(code=code).preview_level, 'layout')
        omitted=contract.parse_tool_call(call('preview_scene',dict(code=code)))[1]
        self.assertEqual(omitted.preview_level,'layout')
        schema=contract.PreviewSceneArgs.model_json_schema()['properties']['preview_level']
        self.assertEqual(schema['default'],'layout')
        for level in ('layout', 'full'):
            args=contract.parse_tool_call(call('preview_scene',dict(code=code,preview_level=level)))[1]
            self.assertEqual(args.preview_level,level)
            self.assertEqual(args.to_turn('自行选择校样深度').code,code)
        with self.assertRaises(ValidationError):
            contract.PreviewSceneArgs(code=code,preview_level='skip_validation')
        self.assertIn('layout通过不代表动效验收',schema['description'])
        for text in ('优先使用','可随时','不限制设计','finish会在交付前自动补齐'):
            self.assertIn(text,schema['description'])
        catalog=next(item for item in contract.tool_catalog() if item['name']=='preview_scene')
        self.assertEqual(catalog['parameters']['properties']['preview_level']['default'],'layout')
        self.assertIn('默认layout',catalog['description'])

    def test_preview_guidance_prefers_fast_layout_without_mandating_creative_sequence(self):
        for text in ('字号、位置、换行','默认layout快速校样','随时选full'):
            self.assertIn(text,contract.GUIDE)
        for text in ('默认preview_level="layout"','每次微调','可随时显式选preview_level="full"',
                     '不设固定模板、顺序或调用次数','同一代码自动补齐full','独立审核'):
            self.assertIn(text,contract.CODING_GUIDE)

    def test_poster_contract_explains_custom_animation_without_limiting_design(self):
        for text in ('ctx.progress(start,end)', 'ctx.poster=true且ctx.frame=0',
                     'ctx.poster=false且ctx.frame=0', '不能把ctx.frame===0当作海报标志',
                     '不会覆盖你写在Group或子元素上的opacity',
                     '不强制使用Group或固定动效', 'preview_level="layout"',
                     'finish前仍需完整检查'):
            self.assertIn(text, contract.CODING_GUIDE)
        self.assertNotIn('海报预览会自动显示全部Group，不用写ctx.poster分支', contract.CODING_GUIDE)

    def test_inventory_validates_content_but_does_not_execute_or_prepare_materials(self):
        raw=inventory()
        result=contract.parse_tool_call(call('set_inventory',{'inventory':raw}))[1]
        self.assertEqual(result.inventory.texts[0].text,'测试标题')
        invalid=copy.deepcopy(raw)
        invalid['foreground_summary']['subject_count']=1
        with self.assertRaises(ValueError):contract.parse_tool_call(call('set_inventory',{'inventory':invalid}))

    def test_source_identity_and_safe_string_helpers_are_explained(self):
        for text in ('image_id/source_sha256','原图轴线不等于底图残留'):
            self.assertIn(text,contract.GUIDE)
        for text in ('String(value)','两个参数必须是字符串','不支持正则或回调',
                     '一次文字出现','各自的原图文字框','不用SVG或删字规避核查'):
            self.assertIn(text,contract.CODING_GUIDE)

    def test_exported_catalog_is_detached_and_guides_promote_autonomous_work(self):
        before=copy.deepcopy(contract.TOOL_CATALOG)
        detached=contract.tool_catalog()
        detached[0]['parameters']['properties']['asset_id']['description']='changed'
        self.assertEqual(contract.TOOL_CATALOG,before)
        for text in ('不是强制顺序','工具可反复组合','原像素','正式采用由用户确认'):
            self.assertIn(text,contract.GUIDE)
        self.assertNotIn('最多3次preview',contract.CODING_GUIDE)
        self.assertNotIn('总模型预算8次',contract.GUIDE)
        self.assertIn('measurements',contract.CODING_GUIDE)
        self.assertIn('render_asset_id/original_render_asset_id',contract.CODING_GUIDE)
        self.assertIn('可选export default',contract.CODING_GUIDE)
        self.assertNotIn('不支持import/export',contract.CODING_GUIDE)
        for text in ('不是完整React','Object.assign','indexOf','不要再次添加<Asset id="background"/>',
                     'SVG的text/defs/filter','不要把普通容器都换成Group','保留已正确的内容与设计'):
            self.assertIn(text,contract.CODING_GUIDE)
        for text in ('material_status','missing','background_retained','select_background','主体source'):
            self.assertIn(text,contract.GUIDE)
        inventory_description=contract.SetInventoryArgs.model_json_schema()['properties']['inventory']['description']
        self.assertIn('本任务底图版本ID',inventory_description)


if __name__=='__main__':unittest.main()
