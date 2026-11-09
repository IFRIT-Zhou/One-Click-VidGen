"""Offline capability truth and selector integration; no model/render billing."""
import copy
import json
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend.app import vibe_shotcraft_capabilities as tags
from backend.app import vibe_shotcraft as worker
from backend.app.vibe_shotcraft_library import ShotcraftLibrary
from backend.app.vibe_shotcraft_plan import Item, Stage, RECIPE_RULES
from test_vibe_shotcraft_pipeline import FixtureLibrary


class ShotcraftCapabilityTests(unittest.TestCase):
    def test_exactly_twelve_runtime_adapters_have_valid_labels(self):
        self.assertTrue(tags.validate_capability_catalog())
        self.assertEqual(len(tags.CATALOG['styles']), 12)
        self.assertEqual(set(tags.CATALOG['styles']), set(RECIPE_RULES))
        self.assertIsNone(tags.capabilities_for('archived-but-not-adapted'))

    def test_invalid_registry_or_unknown_labels_fail_preflight(self):
        for mutate in (
            lambda data: data['styles'].pop('card-stack'),
            lambda data: data['styles']['card-stack'].update(content_tags=['imaginary_capability']),
            lambda data: data['styles']['card-stack'].update(primary_images_at_once=4),
            lambda data: data['styles']['marker-underline-title'].update(evidence_support='conditional'),
            lambda data: data['styles']['card-stack'].update(avoid_for=[]),
        ):
            with self.subTest(mutate=mutate):
                invalid = copy.deepcopy(tags.CATALOG)
                mutate(invalid)
                with self.assertRaises(ValueError):
                    tags.validate_capability_catalog(invalid)
        with patch.object(worker, 'validate_capability_catalog', side_effect=ValueError('bad tags')), \
             patch.object(worker, 'ShotcraftLibrary') as library, \
             self.assertRaisesRegex(ValueError, '未调用模型'):
            worker.preflight_library({'canvas': [1920, 1080], 'duration': 13})
        library.assert_not_called()

    def test_hard_limits_follow_python_contract_not_editorial_tags(self):
        stage_fields = Stage.model_json_schema()['properties']
        item_fields = Item.model_json_schema()['properties']
        for identity, rule in RECIPE_RULES.items():
            with self.subTest(identity=identity):
                cap = tags.capabilities_for(identity)
                limits = cap['hard_constraints']
                self.assertEqual(limits['items'], {'minimum': rule['min_items'], 'maximum': rule['max_items']})
                self.assertEqual(limits['min_frames'], rule['min_frames'])
                self.assertEqual(limits['max_stage_seconds'], rule.get('max_seconds'))
                text = limits['text_max_chars']
                self.assertEqual(text['headline'], min(stage_fields['headline']['maxLength'], rule.get('headline_max', 28)))
                self.assertEqual(text['item_title'], item_fields['title']['maxLength'])
                for name in ('eyebrow', 'footer'):
                    self.assertEqual(text[name], stage_fields[name]['maxLength'])
                self.assertEqual(text['stage_body'], stage_fields['body']['maxLength'])
                base = {'id': 'stage', 'recipe': identity, 'at': 0, 'headline': '真实标题', 'items': [
                    {'id': f'item{i}', 'title': '真实条目', 'body': '文' * text['item_body'],
                     **({'asset_id': 'original'} if rule.get('image_required') else {})}
                    for i in range(rule['min_items'])]}
                Stage.model_validate(base)
                base['items'][0]['body'] += '文'
                with self.assertRaises(ValidationError):
                    Stage.model_validate(base)

    def test_material_tags_distinguish_model_input_from_display_slots(self):
        marker = tags.capabilities_for('marker-underline-title')
        self.assertEqual(marker['image_slots']['maximum'], 0)
        self.assertEqual(marker['material_tags'], ['text_only'])
        self.assertEqual(marker['reference_inputs'], 'multiple_for_understanding_not_display_slots')
        scan = tags.capabilities_for('scanline-annotate-focus')
        self.assertEqual(scan['image_slots'], {'minimum': 1, 'maximum': 1, 'per_item_maximum': 1})
        self.assertNotIn('text_only', scan['material_tags'])
        for identity in ('word-relay-filmstrip', 'carousel-3d'):
            cap = tags.capabilities_for(identity)
            self.assertEqual(cap['image_slots']['maximum'], 4)
            self.assertEqual(cap['primary_images_at_once'], 1)
            self.assertIn('multiple_images', cap['material_tags'])
        self.assertIn('core', tags.selection_taxonomy()['policy']['materials'])

    def test_numeric_cards_do_not_claim_native_chart_rendering(self):
        for identity in RECIPE_RULES:
            cap = tags.capabilities_for(identity)
            self.assertFalse(cap['native_data_charts'])
            self.assertEqual(cap['numeric_rendering'], 'text_only')
        self.assertIn('numeric_comparison', tags.capabilities_for('quad-split-parallel-scenes')['content_tags'])

    def test_evidence_support_is_conditional_and_not_a_rendering_gate(self):
        self.assertEqual(tags.capabilities_for('scanline-annotate-focus')['evidence_support'], 'conditional')
        self.assertEqual(tags.capabilities_for('title-demote-to-label')['evidence_support'], 'conditional')
        for identity in ('card-stack', 'quad-split-parallel-scenes', 'document-typewriter-reveal'):
            self.assertEqual(tags.capabilities_for(identity)['evidence_support'], 'not_recommended')
        # Soft tags must not change existing valid data or discard a candidate.
        actual = worker.eligible_catalog(FixtureLibrary(), 13)
        self.assertEqual(len(actual['items']), 12)
        self.assertIn('scanline-annotate-focus', [row['id'] for row in actual['items']])

    def test_timing_budget_does_not_claim_outro_covers_long_shot(self):
        self.assertEqual(tags.capabilities_for('carousel-3d', 4.98)['max_items_within_available_time'], 0)
        self.assertEqual(tags.capabilities_for('carousel-3d', 5)['max_items_within_available_time'], 2)
        self.assertEqual(tags.capabilities_for('carousel-3d', 7.5)['max_items_within_available_time'], 3)
        self.assertEqual(tags.capabilities_for('word-relay-filmstrip', 5.1)['max_items_within_available_time'], 3)
        outro = tags.capabilities_for('outro-group-photo-launch', 13)
        self.assertNotIn('whole_shot_item_ceiling', outro)
        self.assertEqual(outro['hard_constraints']['max_stage_seconds'], 6)
        self.assertIn('not permission to fill the whole shot', tags.selection_taxonomy()['policy']['timing'])

    def test_different_names_can_share_a_motion_family(self):
        self.assertEqual(tags.motion_families(['word-relay-filmstrip', 'carousel-3d']), ['sequential_gallery'])
        self.assertEqual(tags.motion_families(['unknown']), [])
        self.assertNotEqual(tags.motion_families(['marker-underline-title']), ['sequential_gallery'])

    def test_capabilities_and_taxonomy_are_not_mutable_global_state(self):
        before = copy.deepcopy(tags.CATALOG)
        tags.capabilities_for('card-stack')['content_tags'].clear()
        tags.selection_taxonomy()['tag_labels']['content'].clear()
        self.assertEqual(tags.CATALOG, before)

    def test_selector_and_director_receive_same_audited_capabilities(self):
        catalog = worker.eligible_catalog(FixtureLibrary(), 13)
        self.assertEqual(catalog['capability_version'], tags.VERSION)
        for row in catalog['items']:
            self.assertEqual(row['capabilities'], tags.capabilities_for(row['id'], 13))
        cards = worker.recipe_context(FixtureLibrary(), ['card-stack', 'scanline-annotate-focus'])
        self.assertTrue(all(card['capabilities'] == tags.capabilities_for(card['id']) for card in cards))

    def test_truncated_catalog_is_never_relabelled_complete(self):
        library = FixtureLibrary()
        incomplete = {'complete': False, 'items': [], 'omitted_styles': 12}
        with patch.object(library, 'compact_catalog', return_value=incomplete), self.assertRaisesRegex(ValueError, '截断'):
            worker.eligible_catalog(library, 13)

    def test_catalog_and_detail_expose_tags_without_claiming_archived_capabilities(self):
        library = object.__new__(ShotcraftLibrary)
        library.manifest = {'upstream': {'commit': 'offline'}, 'categories': {'test': {}}}
        library.styles = {identity: {'id': identity, 'card_id': 'test', 'category': 'test',
            'label': identity, 'description': 'offline test', 'demo_paths': []}
            for identity in [*RECIPE_RULES, 'archived-only']}
        library.runtime = {identity: {'status': 'supported'} for identity in RECIPE_RULES}
        def readiness(style):
            return {'status': 'supported' if style['id'] in RECIPE_RULES else 'archived_not_ready',
                    'ready': style['id'] in RECIPE_RULES}
        with patch.object(library, '_readiness', side_effect=readiness):
            catalog = library.compact_catalog(ready_only=True)
            self.assertTrue(catalog['complete'])
            self.assertEqual(len(catalog['items']), 12)
            self.assertLess(len(json.dumps(catalog, ensure_ascii=False)), 30000)
            for row in catalog['items']:
                self.assertEqual(row['capabilities'], library.get_style(row['id'])['capabilities'])
            self.assertNotIn('capabilities', library.get_style('archived-only'))
            archived = next(row for row in library.compact_catalog()['items'] if row['id'] == 'archived-only')
            self.assertNotIn('capabilities', archived)


if __name__ == '__main__':
    unittest.main()
