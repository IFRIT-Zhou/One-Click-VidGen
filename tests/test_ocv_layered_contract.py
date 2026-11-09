"""Offline MCP layered consent/budget contracts; no actual OCV/model requests."""
import copy
from pathlib import Path
import tempfile
import time
import unittest

from test_ocv_shotcraft_contract import cloud_schema, FakeBackend, OPERATION, ROUTE
from ocv_mcp.adapter import Adapter
from ocv_mcp.contracts import bind


def layered_schema():
    document=cloud_schema()
    props=document['paths'][ROUTE]['post']['requestBody']['content']['application/json']['schema']['properties']
    props['template_mode']['enum'].append('layered')
    props['image_route_key']={'anyOf':[{'type':'string','pattern':'^[a-f0-9]{64}$'},{'type':'null'}],'default':None}
    props['confirm_new_image_task']={'type':'boolean','default':False}
    props['advanced_subject_separation']={'type':'boolean','default':False,
        'description':'Layered only; explicit advanced subject separation.'}
    path='/api/video-studio/{identity}/shots/{shot_id}/vibe-image-route'
    document['paths'][path]={'get':{'parameters':copy.deepcopy(document['paths'][ROUTE]['post']['parameters'])}}
    return document


class LayeredMcpContractTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.backend=FakeBackend(layered_schema())
        self.adapter=Adapter('http://127.0.0.1:8010',self.root/'state',
            transport_factory=lambda:self.backend,allow_generation=True)
        self.path={'identity':'a'*32,'shot_id':'s1'}

    def prepare(self, body, request='layered_test'):
        return self.adapter.prepare(OPERATION,{'path':self.path,'body':body},request)

    def test_layered_explicit_zero_and_one_budget_are_preserved_with_correct_cost(self):
        for budget in (0,1):
            with self.subTest(budget=budget):
                body={'template_mode':'layered','confirm_cloud':True,'max_image_requests':budget}
                plan=self.prepare(body,f'layered_budget_{budget}')
                self.assertEqual(plan['arguments']['body'],body)
                self.assertTrue(plan['requires_confirmation'])
                for phrase in ('独立 Vibe API','最终字幕时间戳','RMBG','最多一次视觉返工','不自动采用',f'图像请求上限为 {budget}'):
                    self.assertIn(phrase,plan['cost'])
                self.assertIn('本项目图像模型' if budget else '未授权背景出图',plan['cost'])
        self.assertFalse(self.backend.calls)

    def test_non_layered_modes_cannot_spend_image_budget(self):
        for mode in ('shotcraft','semantic',None):
            body={'confirm_cloud':True,'max_image_requests':1}
            if mode:body['template_mode']=mode
            with self.subTest(mode=mode),self.assertRaises(ValueError):self.prepare(body)
        self.assertFalse(self.backend.calls)

    def test_subject_separation_uses_live_default_and_is_layered_only(self):
        bound, _ = bind(self.backend.schema)
        schema = bound[OPERATION][1]['properties']['body']
        self.assertIs(schema['properties']['advanced_subject_separation']['default'], False)
        for advanced in (False, True):
            body = dict(template_mode='layered', confirm_cloud=True, max_image_requests=0,
                        advanced_subject_separation=advanced)
            plan = self.prepare(body, f'separation_{advanced}')
            self.assertEqual(plan['arguments']['body'], body)
            self.assertIn('已启用高级主体分离' if advanced else '默认稳定保留主体于底板', plan['cost'])
        for mode in ('semantic', 'shotcraft', None):
            body = dict(confirm_cloud=True, max_image_requests=0, advanced_subject_separation=True)
            if mode: body['template_mode'] = mode
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.prepare(body)
        for value in (1, 0, 'true', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.prepare(dict(template_mode='layered', confirm_cloud=True,
                                  max_image_requests=0, advanced_subject_separation=value))
        self.assertFalse(self.backend.calls)

    def test_old_layered_service_does_not_gain_unsupported_policy_field(self):
        document = layered_schema()
        props = document['paths'][ROUTE]['post']['requestBody']['content']['application/json']['schema']['properties']
        props.pop('advanced_subject_separation')
        self.backend.schema = document
        bound, _ = bind(document)
        self.assertNotIn('advanced_subject_separation', bound[OPERATION][1]['properties']['body']['properties'])
        body = dict(template_mode='layered', confirm_cloud=True, max_image_requests=0)
        plan = self.prepare(body, 'legacy_policy')
        self.assertIn('当前服务未提供主体策略开关', plan['cost'])
        self.assertNotIn('默认稳定保留主体', plan['cost'])
        with self.assertRaises(ValueError):
            self.prepare(dict(template_mode='layered', confirm_cloud=True, max_image_requests=0,
                              advanced_subject_separation=True))
        self.assertFalse(self.backend.calls)

    def test_layered_requires_concrete_bounded_budget_and_cloud_consent(self):
        base={'template_mode':'layered','confirm_cloud':True,'max_image_requests':1}
        bad=[{k:v for k,v in base.items() if k!='max_image_requests'}]
        bad.extend({**base,'max_image_requests':v} for v in (None,True,-1,2,4,'1'))
        bad.extend({**base,'confirm_cloud':v} for v in (False,1,None))
        for body in bad:
            with self.subTest(body=body),self.assertRaises(ValueError):self.prepare(body)
        self.assertFalse(self.backend.calls)

    def test_live_route_fields_are_frozen_and_commit_remains_confirmation_gated(self):
        body=dict(template_mode='layered',confirm_cloud=True,max_image_requests=1,
                  image_route_key='c'*64,confirm_new_image_task=True)
        plan=self.prepare(body)
        self.assertIn('旧渠道任务可能仍在运行或计费',plan['cost'])
        with self.assertRaisesRegex(ValueError,'明确授权'):self.adapter.commit(plan['plan_id'])
        self.assertFalse(self.backend.calls)
        self.adapter.commit(plan['plan_id'],confirmed=True)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if not self.adapter.receipt(plan['plan_id'])['adapter_request_running']:break
            time.sleep(.01)
        self.assertEqual(self.adapter.receipt(plan['plan_id'])['status'],'submitted')
        self.assertEqual(self.backend.calls[0][2]['json'],body)
        self.adapter.commit(plan['plan_id'],confirmed=True)
        self.assertEqual(len(self.backend.calls),1)

    def test_old_service_cannot_receive_unsupported_layered_or_new_fields(self):
        self.backend.schema=cloud_schema()
        for body in ({'confirm_cloud':True,'max_image_requests':1,'template_mode':'layered'},
                     {'confirm_cloud':True,'max_image_requests':0,'image_route_key':'d'*64}):
            with self.subTest(body=body),self.assertRaises(ValueError):self.prepare(body)
        self.assertFalse(self.backend.calls)

    def test_native_zero_only_budget_is_not_expanded(self):
        document=layered_schema()
        props=document['paths'][ROUTE]['post']['requestBody']['content']['application/json']['schema']['properties']
        props['max_image_requests']['maximum']=0
        self.backend.schema=document
        with self.assertRaises(ValueError):self.prepare({'template_mode':'layered','confirm_cloud':True,'max_image_requests':1})

    def test_route_read_is_available_without_generation_permission(self):
        adapter=Adapter('http://127.0.0.1:8010',self.root/'read',read_only=True,
            transport_factory=lambda:self.backend)
        adapter.read('ocv_video_vibe_image_route',{'path':self.path})
        self.assertEqual(self.backend.calls[0][:2],('GET','/api/video-studio/'+'a'*32+'/shots/s1/vibe-image-route'))
        with self.assertRaisesRegex(ValueError,'授权'):
            adapter.prepare(OPERATION,{'path':self.path,'body':dict(template_mode='layered',confirm_cloud=True,max_image_requests=1)},'not_authorized')
        self.assertEqual(len(self.backend.calls),1)


if __name__=='__main__':unittest.main()
