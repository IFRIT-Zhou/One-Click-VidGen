"""Reference fidelity is an optional constraint, not a suggestion in the summary."""
from copy import deepcopy
import unittest

try:
    from tools import vibe_reference_layout as ref
    from tools import vibe_layered_trial as trial
except ImportError:
    raise unittest.SkipTest('Private reference-layout prototype is not distributed')


class ReferenceLayoutTests(unittest.TestCase):
    def setUp(self):
        self.ctx={'canvas':[1920,1080],'fps':30,'duration':10,'verified_text':['署名','甲机构','乙机构','结语'],
                  'assets':{n:n+'.png' for n in ('background','professor','gold_patch')}}
        self.layout={'version':ref.VERSION,'exit':.35,'ink':'#123456','background_color':'#EEF8FA',
                     'subjects':[{'asset_id':'professor','slot':'hero','box':[50,50,700,900],'edge_fade_right':.18},
                                 {'asset_id':'gold_patch','slot':'chip','box':[690,420,440,310]}],
                     'contact':{'back':'hero','front':'chip','zone':[675,400,100,400],'min_width':20,'max_width':85},
                     'labels':[{'slot':'name','text':'署名','box':[20,830,350,120],'font_size':50,'align':'left'},
                               {'slot':'endline','text':'结语','box':[1200,820,510,95],'font_size':40,'align':'center','follows':'b'}],
                     'rows':{'first_box':[1210,320,530,96],'gap':20,'items':[{'slot':'a','text':'甲机构'},{'slot':'b','text':'乙机构'}],
                             'ink':'#123456','fill':'#DFEFFF','stroke':'#C0D8E9','radius':14,'font_size':38,'opacity':.92,'padding':12}}
        self.ctx['reference_layout']=self.layout
        self.proposal={'summary':'按语义入场','cues':[{'slot':s['slot'],'at':idx,'enter':.7,'motion':'slide_up'}
                                             for idx,s in enumerate(ref.required_cues(self.layout))]}

    def test_compiles_immutable_plates_exact_copy_and_one_local_alpha_edge(self):
        spec,prop=deepcopy(self.ctx),deepcopy(self.proposal)
        plan=trial.validate_proposal(prop,spec)['plan']; elements={e['id']:e for e in plan['elements']}
        self.assertEqual(spec,self.ctx); self.assertEqual(prop,self.proposal)
        self.assertEqual([e['text'] for e in plan['elements'] if e['type']=='text'], ['署名','结语','甲机构','乙机构'])
        for sid in ('a','b'):
            plate,text=elements['plate_'+sid],elements[sid]
            self.assertEqual(plate['fill'],'#DFEFFF'); self.assertEqual(plate['radius'],14)
            self.assertEqual([plate[k] for k in ('at','end','enter','exit','motion')],[text[k] for k in ('at','end','enter','exit','motion')])
            self.assertLess(plan['elements'].index(plate),plan['elements'].index(text))
            self.assertLess(plate['box'][0],text['box'][0]); self.assertGreater(plate['box'][2],text['box'][2])
        self.assertEqual(elements['endline']['at'],elements['b']['at'])
        self.assertEqual(elements['hero']['edge_fade_right'],.18)
        self.assertNotIn('edge_fade_right',elements['chip'])
        self.assertEqual(elements['background']['exit'],0)

    def test_wrong_order_missing_duplicate_late_or_spatial_cues_rejected(self):
        for op in ('reversed','missing','duplicate','late','geometry','summary-only'):
            with self.subTest(op=op), self.assertRaises(ValueError):
                p=deepcopy(self.proposal); c=p['cues']
                if op=='reversed': c[-1]['at']=2.9
                if op=='missing': c.pop()
                if op=='duplicate': c[-1]['slot']=c[-2]['slot']
                if op=='late': c[-1]['at']=9.6
                if op=='geometry': c[-1]['box']=[0,0,1,1]
                if op=='summary-only': del p['cues']
                ref.compile_cues(p,self.ctx)

    def test_unknown_text_out_of_canvas_unsafe_overlap_or_zero_alpha_rejected(self):
        for op in ('copy','canvas','face','no-contact','excess','reverse-z','transparent-plate','fade','slot','link'):
            with self.subTest(op=op), self.assertRaises(ValueError):
                ctx=deepcopy(self.ctx); l=ctx['reference_layout']
                if op=='copy': l['rows']['items'][0]['text']='虚构单位'
                if op=='canvas': l['rows']['gap']=800
                if op=='face': l['subjects'][1]['box'][1]=140
                if op=='no-contact': l['subjects'][1]['box'][0]=760
                if op=='excess': l['subjects'][1]['box'][0]=550
                if op=='reverse-z': l['subjects'].reverse()
                if op=='transparent-plate': l['rows']['opacity']=0
                if op=='fade': l['subjects'][0]['edge_fade_right']=1
                if op=='slot': l['rows']['items'][1]['slot']='plate_a'
                if op=='link': l['labels'][1]['follows']='missing'
                ref.check_layout(l,ctx)

    def test_reuses_prior_by_asset_or_copy_identity_only_not_index(self):
        original=trial.validate_proposal(self.proposal,self.ctx)['plan']
        # A full legacy proposal is never accepted from a reference-mode cloud call.
        with self.assertRaises(ValueError): trial.validate_proposal(original,self.ctx)
        original['elements'].reverse()
        one=trial.validate_proposal(original,self.ctx,allow_prior_timing=True)
        self.assertEqual(one['plan']['elements'][1]['at'],0)
        temp=deepcopy(original)
        next(e for e in temp['elements'] if e.get('text')=='甲机构')['text']='不同'
        with self.assertRaisesRegex(ValueError,'uniquely bind'): trial.validate_proposal(temp,self.ctx,allow_prior_timing=True)

    def test_model_contract_only_exposes_bound_cues_not_positions(self):
        # DeepSeek JSON Output rejects requests with no literal 'json' in the messages.
        self.assertIn('json',ref.SYSTEM.lower())
        self.assertIn('"cues":[{',ref.SYSTEM)
        schema=trial.proposal_schema(self.ctx)
        self.assertEqual(set(schema['properties']),{'summary','cues'})
        slots=schema['properties']['cues']['items']['properties']['slot']['enum']
        self.assertEqual(slots,['hero','chip','name','a','b'])
        self.assertNotIn('box',schema['properties']['cues']['items']['properties'])


if __name__=='__main__':
    unittest.main()
