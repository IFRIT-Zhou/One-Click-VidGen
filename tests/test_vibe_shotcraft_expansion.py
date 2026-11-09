import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from backend.app import vibe_shotcraft_plan as plans
from backend.app import vibe_shotcraft as worker
from test_vibe_shotcraft_pipeline import proposal


class ExpandedTemplatesTests(unittest.TestCase):
    def test_twelve_recipes_are_exposed_in_schema(self):
        self.assertEqual(len(plans.RECIPES), 12)
        self.assertEqual(set(plans.Stage.model_json_schema()['properties']['recipe']['enum']), set(plans.RECIPES))

    def test_every_recipe_exact_timing_and_counts(self):
        for recipe, rule in plans.RECIPE_RULES.items():
            with self.subTest(recipe=recipe):
                raw = proposal()['scene'];stage = raw['stages'][0]
                stage.update(recipe=recipe, headline='真实信息')
                stage['items'] = [dict(id=f'item{i}', title='原始材料', body='保持真实依据', motif='none',
                    **({'asset_id':'ref_03'} if rule.get('image_required') else {})) for i in range(rule['min_items'])]
                plan = plans.ShotcraftPlan.model_validate(raw)
                duration = plans.minimum_frames(recipe,len(stage['items']))/30
                # Global single-shot lower bound is 2.5 s, independent of stage limits.
                duration = max(2.5,duration)
                plans.compile_shotcraft_plan(plan, [], duration, {'ref_03'})
                stage['items'] *= 5
                with self.assertRaises(ValueError):plans.ShotcraftPlan.model_validate(raw)

    def test_neighbor_history_is_read_only_and_skips_failed_tasks(self):
        project=Path('project')
        read=Mock(side_effect=[{'shots':[{'id':'left'},{'id':'current'},{'id':'right'}]}, {'task_id':'a'}, {'task_id':'b'}])
        api=SimpleNamespace(previews=SimpleNamespace(_read_json=read,_hash=lambda x:x,_safe=lambda p,q,**kw:q),
            _read_job=Mock(side_effect=[{'status':'completed','shot_id':'left','template_ids':['card-stack','unsupported']},
                                       {'status':'failed','shot_id':'right','template_ids':['carousel-3d']}]))
        self.assertEqual(worker.neighbor_templates(api,project,'current'),[{'shot_id':'left','position':'previous','recipes':['card-stack'],'source':'completed_preview'}])

    def test_bad_history_does_not_block_generation(self):
        api=SimpleNamespace(previews=SimpleNamespace(_read_json=Mock(side_effect=ValueError('bad history'))))
        self.assertEqual(worker.neighbor_templates(api,Path('project'),'current'),[])

    def test_malformed_history_does_not_block_generation(self):
        for record in ([], {'shots':None}, {'shots':[None]}, {'shots':[{'id':'current'}, None]}):
            api=SimpleNamespace(previews=SimpleNamespace(_read_json=Mock(return_value=record)))
            self.assertEqual(worker.neighbor_templates(api,Path('project'),'current'),[])


if __name__=='__main__':unittest.main()
