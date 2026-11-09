"""Offline capacity parity from material inventory through the layered renderer.

Synthetic content only: no model, image-generation, project, or network calls.
"""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image

from backend.app import vibe_layered as layered
from backend.app import vibe_layered_assembly as assembly
from backend.app.vibe_layered_inventory import Inventory
from backend.app.vibe_layered_limits import (
    MAX_INVENTORY_TEXTS, MAX_LABELS, MAX_SUBJECTS, MAX_DECORATIONS,
    MAX_LAYERS, MAX_MOTION_GROUPS,
)


ROOT = Path(__file__).resolve().parents[1]


class LayeredCapacityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.background = self.folder / 'background.png'
        self.subject = self.folder / 'subject.png'
        Image.new('RGB', (640, 360), '#102030').save(self.background)
        Image.new('RGBA', (32, 32), (30, 120, 180, 255)).save(self.subject)
        self.context = {'canvas': [640, 360], 'duration': 12}
        self.assets = {'background': self.background,
                       **{f'subject_{i}': self.subject for i in range(MAX_SUBJECTS)}}
        self.raw = {
            'background_color': '#102030',
            'subjects': [dict(asset_id=f'subject_{i}', prepared_crop=[0, 0, 1, 1],
                              box=[.02 + i * .12, .02, .10 + i * .12, .10],
                              group=f'subject_{i}') for i in range(MAX_SUBJECTS)],
            'labels': [dict(text=f'Block {i:02d}', color='#FFFFFF', font_size=12,
                            box=[.02 + (i % 8) * .12, .15 + (i // 8) * .12,
                                 .12 + (i % 8) * .12, .25 + (i // 8) * .12],
                            plate={'color': '#203040'}, group=f'text_{i}')
                       for i in range(MAX_LABELS)],
            'decorations': [dict(type='rect', box=[.01 + i * .06, .92, .04 + i * .06, .95],
                                 fill='#405060', group=f'decoration_{i}')
                            for i in range(MAX_DECORATIONS)],
        }

    def compile(self):
        value = assembly.Assembly.model_validate(self.raw)
        assets = assembly.materialize_subjects(value, self.assets, self.folder / 'placed')
        plan = assembly.compile_assembly(value, self.context, assets)
        return value, assets, plan

    def node_validate(self, plan, assets):
        bundled = ROOT / 'runtime/node/node.exe'
        node = str(bundled) if bundled.is_file() else shutil.which('node')
        if not node:
            self.skipTest('Node is unavailable for cross-runtime capacity validation')
        script = """
import {validateLayeredInput} from './lib/layered-contract.mjs';
let source = '';
for await (const chunk of process.stdin) source += chunk;
try {
  const value = validateLayeredInput(JSON.parse(source));
  console.log(JSON.stringify({ok:true, elements:value.plan.elements}));
} catch (error) {
  console.log(JSON.stringify({ok:false, error:String(error.message)}));
}
"""
        result = subprocess.run(
            [node, '--input-type=module', '-e', script],
            cwd=ROOT / 'tools/vibe_remotion', input=json.dumps({
                'plan': plan, 'assets': {key: str(path) for key, path in assets.items()},
                'duration': self.context['duration'], 'fps': 30,
            }, ensure_ascii=False), text=True, encoding='utf-8', capture_output=True,
            timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_schema_capacities_share_bounded_budgets(self):
        self.assertEqual((MAX_INVENTORY_TEXTS, MAX_LABELS, MAX_SUBJECTS, MAX_DECORATIONS,
                          MAX_LAYERS, MAX_MOTION_GROUPS), (32, 48, 8, 16, 128, 72))
        cases = ((Inventory, 'texts', MAX_INVENTORY_TEXTS),
                 (Inventory, 'subjects', MAX_SUBJECTS),
                 (Inventory, 'decorations', MAX_DECORATIONS),
                 (layered.Design, 'labels', MAX_LABELS),
                 (layered.Design, 'subjects', MAX_SUBJECTS),
                 (assembly.Assembly, 'labels', MAX_LABELS),
                 (assembly.Assembly, 'subjects', MAX_SUBJECTS),
                 (assembly.Assembly, 'decorations', MAX_DECORATIONS),
                 (assembly.MotionSchedule, 'groups', MAX_MOTION_GROUPS))
        for model, name, expected in cases:
            with self.subTest(model=model.__name__, field=name):
                self.assertEqual(model.model_json_schema()['properties'][name]['maxItems'], expected)
        self.assertLessEqual(1 + MAX_SUBJECTS + MAX_DECORATIONS + 2 * MAX_LABELS, MAX_LAYERS)
        self.assertEqual(MAX_MOTION_GROUPS, MAX_SUBJECTS + MAX_DECORATIONS + MAX_LABELS)

    def test_48_labels_with_plates_preserve_every_text_and_unique_id_through_node(self):
        value, assets, plan = self.compile()
        expected_texts = [row['text'] for row in self.raw['labels']]
        self.assertEqual(len(plan['elements']), 121)
        self.assertEqual([row['text'] for row in plan['elements'] if row['type'] == 'text'], expected_texts)
        self.assertEqual(len({row['id'] for row in plan['elements']}), 121)
        self.assertEqual(len([row for row in plan['elements'] if ':plate_' in row['id']]), MAX_LABELS)
        inventory = {'subjects': [{'embedded_text': []} for _ in range(MAX_SUBJECTS)],
                     'labels': [{'text': text} for text in expected_texts]}
        self.assertTrue(assembly.validate_content(value, inventory))
        result = self.node_validate(plan, assets)
        self.assertTrue(result['ok'], result.get('error'))
        self.assertEqual(len(result['elements']), 121)
        self.assertEqual([row['id'] for row in result['elements']], [row['id'] for row in plan['elements']])
        self.assertEqual([row['text'] for row in result['elements'] if row['type'] == 'text'], expected_texts)

    def test_renderer_accepts_128_elements_and_rejects_129_without_truncating(self):
        _, assets, plan = self.compile()
        for index in range(len(plan['elements']), MAX_LAYERS):
            plan['elements'].append(dict(id=f'extra_{index}', type='rect', box=[0, 0, 8, 8], fill='#203040'))
        before = copy.deepcopy(plan)
        result = self.node_validate(plan, assets)
        self.assertTrue(result['ok'], result.get('error'))
        self.assertEqual(len(result['elements']), MAX_LAYERS)
        self.assertEqual(plan, before)
        plan['elements'].append(dict(id='overflow', type='rect', box=[0, 0, 8, 8], fill='#203040'))
        result = self.node_validate(plan, assets)
        self.assertFalse(result['ok'])
        self.assertIn(str(MAX_LAYERS), result['error'])
        self.assertEqual(len(plan['elements']), MAX_LAYERS + 1)

    def test_72_distinct_motion_groups_cover_all_layers_and_73_are_rejected(self):
        _, _, plan = self.compile()
        expected = assembly.plan_groups(plan)
        self.assertEqual(len(expected), MAX_MOTION_GROUPS)
        rows = [dict(id=identity, at=(index % 12) * .4, enter=.6, exit=.3, motion='fade')
                for index, identity in enumerate(expected)]
        schedule = assembly.MotionSchedule.model_validate({'groups': rows})
        animated = assembly.apply_motion(plan, schedule, self.context)
        self.assertEqual(set(assembly.plan_groups(animated)), set(expected))
        self.assertEqual(len(animated['elements']), len(plan['elements']))
        with self.assertRaises(ValueError):
            assembly.MotionSchedule.model_validate({'groups': rows + [dict(id='overflow', at=0)]})

    def test_legacy_design_has_room_for_32_texts_and_16_independent_plates(self):
        labels = []
        for index in range(MAX_LABELS):
            labels.append(dict(box=[.1, .1, .9, .9], at=0,
                               **({'text': f'Label {index}', 'color': '#FFFFFF', 'font_size': 12}
                                  if index < MAX_INVENTORY_TEXTS else
                                  {'text': '', 'plate': {'color': '#203040'}})))
        raw = dict(background='extract', background_color='#102030', subjects=[], labels=labels)
        value = layered.Design.model_validate(raw)
        self.assertEqual(len(value.labels), MAX_LABELS)
        self.assertEqual(sum(bool(label.text) for label in value.labels), MAX_INVENTORY_TEXTS)
        with self.assertRaises(ValueError):
            layered.Design.model_validate({**raw, 'labels': labels + [labels[0]]})
        with self.assertRaises(ValueError):
            assembly.Assembly.model_validate({**self.raw, 'labels': self.raw['labels'] + [self.raw['labels'][0]]})


if __name__ == '__main__':
    unittest.main()
