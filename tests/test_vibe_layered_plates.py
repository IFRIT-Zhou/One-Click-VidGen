"""Independent backing plates and explicit model repair-round diagnostics."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.app import vibe_layered as layered, vibe_cloud as cloud
from backend.app.vibe_cloud_client import CloudCallError


class IndependentPlates(unittest.TestCase):
    def design(self):
        return dict(background='solid',background_is_uniform=True,background_color='#031A33',subjects=[],labels=[
            dict(box=[.03,.32,.33,.62],at=.45,enter=.55,motion='slide_left',text='',
                 plate=dict(color='#06203A',gradient_to='#0A4E7A',gradient_angle=180,
                     stroke='#4DB6E8',stroke_width=2,radius=28,opacity=.9,glow=8)),
            dict(box=[.04,.33,.32,.47],at=1,enter=.6,text='中国科学技术大学',color='#FFFFFF',font_size=50),
            dict(box=[.04,.47,.32,.60],at=1.5,enter=.6,text='附属第一医院',color='#FFFFFF',font_size=50)])

    def compile(self,raw):
        return layered.compile_plan(layered.Design.model_validate(raw),{'duration':12,'canvas':[1920,1080]}, {})

    def test_plate_keeps_style_timing_order_and_emits_no_empty_text(self):
        raw=self.design();before=copy.deepcopy(raw)
        plan=self.compile(raw);elements=plan['elements']
        self.assertEqual([e['type'] for e in elements],['image','rect','text','text'])
        plate=elements[1]
        for key,value in dict(at=.45,enter=.55,motion='slide_left',radius=28,opacity=.9,glow=8,
                              fill='#06203A',stroke='#4DB6E8',stroke_width=2).items():
            self.assertEqual(plate[key],value)
        self.assertEqual(plate['gradient'],{'color':'#0A4E7A','angle':180})
        self.assertEqual(elements[2]['at'],1)
        self.assertEqual(elements[3]['at'],1.5)
        self.assertEqual(raw,before)

    def test_old_response_with_text_styles_and_distinct_plate_box_is_supported(self):
        raw=self.design();raw['labels'][0].update(color='#FFFFFF',font_size=52)
        raw['labels'][0]['plate']['box']=[.02,.30,.34,.64]
        plate=self.compile(raw)['elements'][1]
        self.assertEqual(plate['box'],[38,324,615,367])

    def test_blank_label_without_plate_and_text_without_styles_remain_errors(self):
        for changes in ({'text':'','plate':None},{'text':'  \n','plate':None},
                        {'text':'非空文字','color':None,'font_size':50},
                        {'text':'非空文字','color':'#FFFFFF','font_size':None}):
            raw=self.design();raw['labels'][0].update(changes)
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.compile(raw)

    def test_plate_does_not_falsely_duplicate_raster_text(self):
        raw=self.design();raw['labels']=raw['labels'][:1]
        raw['subjects']=[dict(box=[0,0,.9,.9],at=0,source='photo',treatment='preserve',embedded_text=['机构名称'])]
        plan=layered.compile_plan(layered.Design.model_validate(raw),{'duration':12,'canvas':[1920,1080]}, {'photo':'offline.png'})
        self.assertEqual([e['type'] for e in plan['elements']],['image','image','rect'])

    def test_plate_bad_coordinates_and_late_timing_still_rejected(self):
        for change in ({'box':[.5,.1,.5,.8]},{'at':12}):
            raw=self.design();raw['labels'][0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):self.compile(raw)
        raw=self.design();raw['labels'][0]['plate']['box']=[.8,.2,.1,.3]
        with self.assertRaises(ValueError):self.compile(raw)


class RoundDiagnostics(unittest.TestCase):
    def test_timeout_identifies_initial_and_both_repair_rounds_without_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'task.json'
            for attempt,label in enumerate(('初次编排','第 1 轮修正','第 2 轮修正')):
                cloud._write(path,dict(stage='design',attempt=attempt,usage={'model_calls':[]}))
                with patch.object(cloud,'chat_json',side_effect=CloudCallError('调用超过本次 180 秒时间预算',{'error_code':'total_timeout'})) as call:
                    with self.assertRaises(CloudCallError) as failure:
                        cloud._call(path,{'model':'test'},'system',{}, {},lambda:False)
                call.assert_called_once()
                self.assertIn('【编排动效方案 · '+label+'】',str(failure.exception))
                usage=json.loads(path.read_text(encoding='utf-8'))['usage']['model_calls'][0]
                self.assertEqual(usage['attempt'],attempt)
                self.assertEqual(usage['round_label'],label)
                self.assertEqual(usage['error_code'],'total_timeout')

    def test_success_receipt_also_records_round(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'task.json'
            cloud._write(path,dict(stage='design',attempt=1,usage={'model_calls':[]}))
            with patch.object(cloud,'chat_json',return_value=({'valid':True},{'seconds':2})):
                self.assertEqual(cloud._call(path,{'model':'test'},'system',{}, {},lambda:False),{'valid':True})
            usage=json.loads(path.read_text(encoding='utf-8'))['usage']['model_calls'][0]
            self.assertEqual(usage['round_label'],'第 1 轮修正')


if __name__=='__main__':unittest.main()
