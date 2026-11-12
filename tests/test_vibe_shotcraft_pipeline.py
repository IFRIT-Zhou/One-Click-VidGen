"""Independent Shotcraft data and orchestration checks; no paid or live-project calls."""
import copy
import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException, Request
from pydantic import ValidationError

from backend.app import vibe_cloud as cloud
from backend.app import vibe_shotcraft_plan as plans
from backend.app import vibe_shotcraft as shotcraft
from backend.app.vibe_source_style import SourceStyle
import test_vibe_cloud as legacy_cloud


def source_style():
    return dict(source='core', style_notes='原图深蓝背景、浅色正文与青色强调',
                palette=dict(background='#154060', ink='#FFFFFF', muted='#C6D8E4',
                             surface='#214A66', line='#58768D', accent='#78D7CC'),
                background=dict(kind='technical_grid'))


def proposal():
    return dict(summary='按原始证据收束', source_style=source_style(),
                visual_basis=dict(mode='image_led', style_notes='完整展示原始参考图，不重画证据',
                    elements=[dict(label='原始结果', source='ref_03', kind='evidence',
                                   treatment='preserve')]),
                scene=dict(canvas=[1920, 1080], palette=source_style()['palette'],
                    background=source_style()['background'], typography='sans',
                    stages=[dict(id='result', recipe='outro-group-photo-launch', at=0,
                        headline='研究结果', items=[dict(id='original', title='原始材料',
                            body='保留原貌', asset_id='ref_03', motif='none')])]))


class FixtureLibrary:
    """Complete, nonexecuted source contexts with a shared dependency."""
    manifest = {'upstream': {'commit': 'offline-pinned-commit'}}

    def compact_catalog(self, ready_only=True, **kwargs):
        return {'complete': True, 'items': [{'id': recipe, 'ready': True} for recipe in plans.RECIPES]}

    def read_recipe(self, identity, **kwargs):
        return {'complete': True, 'id': identity, 'text': 'Offline complete recipe contract'}

    def read_sources(self, identity, **kwargs):
        return {'complete': True, 'id': identity, 'files': [
            {'path': identity + '.tsx', 'text': 'Offline nonexecuted source context', 'complete': True},
            {'path': 'shared/camera.ts', 'text': 'Offline complete camera dependency', 'complete': True}]}


class ShotcraftContextBoundaryTests(unittest.TestCase):
    def test_eligible_catalog_never_exposes_unimplemented_ready_recipes(self):
        library = FixtureLibrary()
        catalog = library.compact_catalog()
        catalog['items'].append({'id': 'future-untrusted-recipe', 'ready': True})
        with patch.object(library, 'compact_catalog', return_value=catalog):
            actual = shotcraft.eligible_catalog(library, 60)
        self.assertEqual([row['id'] for row in actual['items']], list(plans.RECIPES))
        self.assertEqual(actual['total_styles'], len(plans.RECIPES))

    def test_director_thinking_maps_to_deepseek_low_effort_without_reasoning_leak(self):
        client = legacy_cloud.client
        response = legacy_cloud.CloudClientTests.stream([{'choices': [{'delta': {
            'content': '{"passed":true}', 'reasoning_content': 'offline-private-reasoning'}, 'finish_reason': 'stop'}]}])
        config = dict(base_url='https://example.invalid', api_key='offline-fake-key', model='deepseek-flash')
        with patch.object(client.requests, 'post', return_value=response) as post:
            result, usage = client.chat_json(config, 'offline', {}, {}, lambda: False,
                                            max_tokens=16000, thinking=True)
        body = post.call_args.kwargs['json']
        self.assertEqual(body['max_tokens'], 16000)
        self.assertEqual(body['thinking'], {'type': 'enabled'})
        self.assertEqual(body['reasoning_effort'], 'low')
        self.assertNotIn('offline-private-reasoning', str((result, usage)))
        self.assertEqual(post.call_count, 1)

    def test_eligible_catalog_filters_exact_duration_boundaries(self):
        library = FixtureLibrary()
        source = library.compact_catalog()
        with patch.object(library, 'compact_catalog', return_value=source):
            for duration, expected in [
                (1.69, []), (1.7, ['word-relay-filmstrip']),
                (2.49, ['word-relay-filmstrip']),
                (2.5, ['word-relay-filmstrip', 'outro-group-photo-launch']),
                (3.99, ['word-relay-filmstrip', 'outro-group-photo-launch']),
                (4, ['spotlight-hero-card', 'word-relay-filmstrip', 'outro-group-photo-launch', 'marker-underline-title']),
            ]:
                with self.subTest(duration=duration):
                    actual = shotcraft.eligible_catalog(library, duration)
                    self.assertEqual([row['id'] for row in actual['items']], expected)
                    self.assertEqual(actual['total_styles'], len(expected))
                    self.assertEqual(actual['returned_styles'], len(expected))
                    self.assertEqual(actual['omitted_styles'], 0)
                    self.assertTrue(actual['complete'])
        self.assertEqual(source, FixtureLibrary().compact_catalog(), 'catalog must not be mutated')

    def test_recipe_context_includes_dependencies_once_across_recipes(self):
        library = FixtureLibrary()
        selected = list(plans.RECIPES[:2])
        with patch.object(library, 'read_sources', wraps=library.read_sources) as sources:
            result = shotcraft.recipe_context(library, selected)
        self.assertTrue(all(call.kwargs['include_dependencies'] is True for call in sources.call_args_list))
        paths = [row['path'] for recipe in result for row in recipe['implementations']]
        self.assertEqual(paths.count('shared/camera.ts'), 1)
        self.assertEqual(set(paths), {selected[0] + '.tsx', selected[1] + '.tsx', 'shared/camera.ts'})
        self.assertEqual(result[1]['shared_sources_already_included'], ['shared/camera.ts'])
        self.assertTrue(all(row['text'] for recipe in result for row in recipe['implementations']))

    def test_recipe_context_rejects_incomplete_card_or_implementation(self):
        for method in ('read_recipe', 'read_sources'):
            with self.subTest(method=method):
                library = FixtureLibrary()
                incomplete = getattr(library, method)(plans.RECIPES[0])
                incomplete['complete'] = False
                with patch.object(library, method, return_value=incomplete), self.assertRaisesRegex(ValueError, '截断'):
                    shotcraft.recipe_context(library, [plans.RECIPES[0]])

    def test_recipe_context_budget_counts_unique_sources_without_truncation(self):
        library = MagicMock()
        selected = list(plans.RECIPES[:2])
        metadata_size = sum(len(json.dumps(shotcraft.capabilities_for(identity), ensure_ascii=False)) for identity in selected)
        library.read_recipe.side_effect = lambda identity, **kwargs: {
            'complete': True, 'text': 'c' * (50000 - (metadata_size if identity == selected[0] else 0))}
        def sources(identity, **kwargs):
            return {'complete': True, 'files': [
                {'path': identity + '.tsx', 'text': 'x' * 50000},
                {'path': 'shared.ts', 'text': 's' * 50000}]}
        library.read_sources.side_effect = sources
        result = shotcraft.recipe_context(library, selected)
        size = sum(len(item['card']['text']) + len(json.dumps(item['capabilities'], ensure_ascii=False))
            + sum(len(source['text']) for source in item['implementations']) for item in result)
        self.assertEqual(size, 250000)
        library.read_recipe.side_effect = lambda identity, **kwargs: {
            'complete': True, 'text': 'c' * (50000 - (metadata_size - 1 if identity == selected[0] else 0))}
        with self.assertRaisesRegex(ValueError, '上下文预算'):
            shotcraft.recipe_context(library, list(plans.RECIPES[:2]))


class ShotcraftPlanContractTests(unittest.TestCase):
    def validate(self, raw=None, duration=2.5, selected=('outro-group-photo-launch',)):
        return plans.validate_proposal(plans.ShotcraftProposal.model_validate(raw or proposal()),
            canvas=[1920, 1080], duration=duration, cues={'s1': 0, 's2': 1},
            assets={'core', 'ref_03'}, selected=selected)

    def test_legacy_cloud_default_and_explicit_template_mode(self):
        self.assertEqual(cloud.CloudRequest(confirm_cloud=True).template_mode, 'semantic')
        self.assertEqual(cloud.CloudRequest(confirm_cloud=True, template_mode='shotcraft').template_mode, 'shotcraft')
        for mode in ('custom-code', '', None, True):
            with self.subTest(mode=mode), self.assertRaises(ValidationError):
                cloud.CloudRequest(confirm_cloud=True, template_mode=mode)

    def test_image_card_needs_no_production_caption(self):
        raw = proposal()
        raw['scene']['stages'][0]['items'][0].update(title='', body='')
        self.validate(raw)
        del raw['scene']['stages'][0]['items'][0]['asset_id']
        with self.assertRaisesRegex(ValidationError, '标题'):
            self.validate(raw)

    def test_production_labels_never_reach_renderer(self):
        for label in ('机构署名', '主体占位', '图片占位', '内容收束'):
            raw = proposal()
            raw['scene']['stages'][0]['items'][0]['title'] = label
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, '制作标签'):
                self.validate(raw)

    def test_original_crop_binds_to_real_source_and_is_used(self):
        raw = proposal()
        raw['crops'] = [dict(id='photo', source='core', box=[.1, .2, .6, .8])]
        raw['visual_basis']['elements'][0].update(source='core', output_asset_id='photo')
        raw['scene']['stages'][0]['items'][0].update(asset_id='photo', title='', body='')
        before = copy.deepcopy(raw)
        self.validate(raw)
        self.assertEqual(raw, before)
        for change in ('missing_source', 'overwrite', 'duplicate', 'unused', 'false_provenance', 'whole_poster', 'nan', 'outside', 'reversed', 'reserved'):
            bad = copy.deepcopy(raw)
            crop = bad['crops'][0]
            if change == 'missing_source': crop['source'] = 'unknown'
            elif change == 'overwrite': crop['id'] = 'core'
            elif change == 'duplicate': bad['crops'].append(copy.deepcopy(crop))
            elif change == 'unused': bad['scene']['stages'][0]['items'][0]['asset_id'] = 'ref_03'
            elif change == 'false_provenance': bad['visual_basis']['elements'][0]['source'] = 'ref_03'
            elif change == 'whole_poster': crop['box'] = [0, 0, 1, 1]
            elif change == 'nan': crop['box'][0] = float('nan')
            elif change == 'outside': crop['box'][0] = -.1
            elif change == 'reversed': crop['box'] = [.8, .2, .1, .7]
            elif change == 'reserved': crop['id'] = 'render_proof'
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(bad)
        raw['crops'] *= 5
        with self.assertRaises(ValidationError):
            self.validate(raw)

    def test_unknown_or_unselected_recipe_never_compiles(self):
        for identity in ('unknown', '../spotlight-hero-card', 'timeline-cards'):
            raw = proposal(); raw['scene']['stages'][0]['recipe'] = identity
            with self.subTest(identity=identity), self.assertRaises(ValidationError):
                self.validate(raw)
        with self.assertRaisesRegex(ValueError, '未选择'):
            self.validate(selected=['spotlight-hero-card'])

    def test_model_code_and_remote_asset_fields_are_not_execution_inputs(self):
        for target, key, value in (
            ('proposal', 'code', 'process.exit(0)'),
            ('scene', 'source', 'https://example.invalid/template.tsx'),
            ('item', 'url', 'file:///C:/private.png'),
            ('item', 'css', {'background': 'url(https://example.invalid)'}),
        ):
            raw = proposal()
            item = raw if target == 'proposal' else raw['scene'] if target == 'scene' else raw['scene']['stages'][0]['items'][0]
            item[key] = value
            with self.subTest(target=target, key=key), self.assertRaises(ValidationError):
                self.validate(raw)

    def test_frame_coverage_uses_original_duration_without_mutating_cues_or_plan(self):
        raw = proposal()
        stages = raw['scene']['stages']
        stages[0]['recipe'] = 'spotlight-hero-card'
        stages.append({**copy.deepcopy(stages[0]), 'id': 'relay', 'recipe': 'word-relay-filmstrip', 'at': 4.4})
        stages.append({**copy.deepcopy(stages[0]), 'id': 'end', 'recipe': 'outro-group-photo-launch', 'at': 299 / 30})
        source = copy.deepcopy(raw)
        cues = {'s1': 0, 's2': 4.4, 's3': 299 / 30}
        frozen_cues = copy.deepcopy(cues)
        duration = 404 / 30
        plan = plans.ShotcraftPlan.model_validate(raw['scene'])
        compiled = plans.compile_shotcraft_plan(plan, cues, duration, {'core', 'ref_03'})
        self.assertEqual(compiled['duration'], duration)
        self.assertEqual(compiled['fps'], 30)
        self.assertEqual(round(compiled['duration'] * compiled['fps']), 404)
        self.assertEqual([(s['start'], s['end']) for s in compiled['stages']],
                         [(0, 4.4), (4.4, 299 / 30), (299 / 30, duration)])
        self.assertEqual(cues, frozen_cues)
        self.assertEqual(raw, source)

    def test_insufficient_reading_time_is_rejected_not_compressed(self):
        for recipe, duration in [('spotlight-hero-card', 3.96), ('outro-group-photo-launch', 2.49)]:
            raw = proposal(); raw['scene']['stages'][0]['recipe'] = recipe
            with self.subTest(recipe=recipe), self.assertRaises(ValueError):
                self.validate(raw, duration, selected=[recipe])
        raw = proposal(); stage = raw['scene']['stages'][0]
        stage['recipe'] = 'word-relay-filmstrip'
        stage['items'].append({**stage['items'][0], 'id': 'second'})
        with self.assertRaises(ValueError):
            self.validate(raw, duration=3.3, selected=['word-relay-filmstrip'])

    def test_outro_maximum_is_six_seconds_per_stage_without_shortening_narration(self):
        self.assertEqual(self.validate(duration=6)['duration'], 6)
        with self.assertRaisesRegex(ValueError, '收束最多6秒'):
            self.validate(duration=6.01)
        raw = proposal()
        hero = copy.deepcopy(raw['scene']['stages'][0])
        hero.update(id='intro', recipe='spotlight-hero-card')
        raw['scene']['stages'][0]['at'] = 4
        raw['scene']['stages'].insert(0, hero)
        compiled = self.validate(raw, duration=10,
            selected=['spotlight-hero-card', 'outro-group-photo-launch'])
        self.assertEqual(compiled['duration'], 10)
        self.assertEqual([(s['start'], s['end']) for s in compiled['stages']], [(0, 4), (4, 10)])

    def test_no_image_native_scene_cannot_keep_medical_illustration_label(self):
        native = proposal()
        native['visual_basis'] = dict(mode='native', style_notes='本镜只呈现研究价值文字', elements=[
            dict(label='研究价值', source='core', kind='text', treatment='native')])
        native['scene']['stages'][0]['items'][0].pop('asset_id')
        for key in ('eyebrow', 'footer'):
            raw = copy.deepcopy(native)
            raw['scene']['stages'][0][key] = '医学示意'
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, '本次无医学图像入画'):
                self.validate(raw)
        native['scene']['stages'][0]['footer'] = 'DOI:10.1234/ABC-2026.0005'
        compiled = self.validate(native)
        self.assertEqual(compiled['stages'][0]['footer'], native['scene']['stages'][0]['footer'])

    def test_original_image_scene_is_not_blocked_by_medical_illustration_label(self):
        raw = proposal()
        raw['scene']['stages'][0].update(eyebrow='医学示意', footer='医学示意 · 原始材料')
        compiled = self.validate(raw)
        self.assertEqual(compiled['stages'][0]['eyebrow'], '医学示意')
        self.assertEqual(compiled['stages'][0]['items'][0]['asset_id'], 'ref_03')

    def test_unknown_asset_and_core_poster_cannot_masquerade_as_original_subject(self):
        for identity in ('missing-ref', 'core'):
            raw = proposal(); raw['scene']['stages'][0]['items'][0]['asset_id'] = identity
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                self.validate(raw)

    def test_evidence_cannot_be_redrawn_or_falsely_claimed_preserved(self):
        for treatment in ('native', 'missing-binding', 'derived-asset'):
            raw = proposal(); element = raw['visual_basis']['elements'][0]
            if treatment == 'native':
                element['treatment'] = 'native'
            elif treatment == 'missing-binding':
                raw['scene']['stages'][0]['items'][0].pop('asset_id')
            else:
                element['output_asset_id'] = 'generated-image'
            with self.subTest(treatment=treatment), self.assertRaises(ValueError):
                self.validate(raw)
        self.assertEqual(self.validate()['stages'][0]['items'][0]['asset_id'], 'ref_03')

    def test_core_only_evidence_requires_original_even_if_model_proposes_omission(self):
        raw = proposal()
        raw['visual_basis'] = dict(mode='native', style_notes='海报证据无法保真重排', elements=[
            dict(label='证据截图', source='core', kind='evidence', treatment='omit', reason='无法单独取得原图')])
        raw['scene']['stages'][0]['items'][0].pop('asset_id')
        with self.assertRaisesRegex(ValueError, '独立原素材'):
            self.validate(raw)

    def test_first_source_palette_and_background_stay_frozen_during_repairs(self):
        raw = proposal(); original = copy.deepcopy(raw)
        frozen = SourceStyle.model_validate(source_style())
        for omitted in (False, True):
            repair = copy.deepcopy(raw)
            repair['scene']['palette']['background'] = '#FFFFFF'
            repair['scene']['background'] = {'kind': 'paper'}
            if omitted:
                repair.pop('source_style')
            else:
                repair['source_style']['palette']['background'] = '#FFFFFF'
            before = copy.deepcopy(repair)
            bound = plans.bind_style(repair, frozen)
            self.assertEqual(bound['source_style'], source_style())
            self.assertEqual(bound['scene']['palette'], source_style()['palette'])
            self.assertEqual(bound['scene']['background'], source_style()['background'])
            self.assertEqual(repair, before)
        self.assertEqual(raw, original)

    def test_review_image_budget_accepts_32_and_rejects_33_before_provenance_work(self):
        raw = plans.ShotcraftProposal.model_validate(proposal())
        assets = {'core', 'ref_03'} | {f'extra_{index}' for index in range(25)}
        compiled = plans.validate_proposal(raw, canvas=[1920, 1080], duration=2.5,
            cues={}, assets=assets, selected=['outro-group-photo-launch'])
        self.assertEqual(len(compiled['stages']), 1)
        with patch.object(plans, 'validate_visual_basis') as provenance, self.assertRaisesRegex(ValueError, '33张'):
            plans.validate_proposal(raw, canvas=[1920, 1080], duration=2.5,
                cues={}, assets=assets | {'one_more'}, selected=['outro-group-photo-launch'])
        provenance.assert_not_called()

    def test_outro_body_limit_reports_stage_item_and_exact_character_count(self):
        raw = proposal()
        raw['scene']['stages'][0]['items'][0]['body'] = 'D' * 22
        self.validate(raw)
        text = 'DOI:10.1234/ABC-2026.0005'
        raw['scene']['stages'][0]['items'][0]['body'] = text
        before = copy.deepcopy(raw)
        with self.assertRaises(ValidationError) as error:
            self.validate(raw)
        message = str(error.exception)
        for detail in ('result', 'original.body', f'实长{len(text)}字符', '最大22', text, 'DOI不要截断'):
            self.assertIn(detail, message)
        self.assertEqual(raw, before)


class ShotcraftPipelineTests(unittest.TestCase):
    # Reuse the isolated files/transport fixture, not its test methods. Calling
    # synchronous routes directly avoids Windows sandbox socketpair limitations
    # in TestClient's async portal while exercising the same route/worker code.
    persist = legacy_cloud.VibeCloudTests.persist
    render = staticmethod(legacy_cloud.VibeCloudTests.render)
    material = staticmethod(legacy_cloud.VibeCloudTests.material)
    response = staticmethod(legacy_cloud.VibeCloudTests.response)

    def setUp(self):
        legacy_cloud.VibeCloudTests.setUp(self)
        self.request = Request({'type': 'http', 'headers': []})
        # Archive hashes/completeness have their own integration suite. This
        # transport fixture is intentionally independent of a 150-style install.
        library = patch.object(shotcraft, 'ShotcraftLibrary', FixtureLibrary)
        library.start(); self.addCleanup(library.stop)

    def start(self, **data):
        return cloud.start(self.identity, 'shot1', cloud.CloudRequest(
            confirm_cloud=True, template_mode='shotcraft', **data), self.request)

    def finish(self):
        worker = self.workers[-1]
        worker.target(*worker.args)
        return cloud.latest(self.identity, 'shot1', self.request)

    def selection(self):
        return dict(recipes=['outro-group-photo-launch'], reason='短镜头保留原始证据后收束',
                    source_style=source_style())

    def test_template_background_uses_project_route_once_and_reaches_renderer(self):
        before = (self.project / 'record.json').read_bytes()
        route = {'key':'a'*64,'label':'个人 API'}
        def generate(*args, **kwargs):
            self.assertIn('完全移除前景人物', args[1])
            self.assertEqual(kwargs['image_route'], route)
            self.assertEqual(kwargs['image_config'], {'test':True})
            kwargs['on_submitted']('offline-background-task')
            return {'path':self.project/'ref3.png'}
        with patch('backend.app.vibe_image_route.resolve', return_value=({'test':True}, route)), \
             patch.object(shotcraft, 'generate_asset', side_effect=generate) as image, \
             patch.object(cloud, 'chat_json', side_effect=self.answers(
                 self.selection(), proposal(), {'passed':True,'issues':[]})), \
             patch.object(cloud, 'render_scene', side_effect=self.render) as render:
            self.start(max_image_requests=1, image_route_key='a'*64)
            task = self.finish()
        self.assertEqual(task['status'], 'completed', task)
        image.assert_called_once()
        self.assertIn('template_background', render.call_args.kwargs['assets'])
        self.assertEqual(task['usage']['image_requests'], 1)
        self.assertEqual((self.project/'record.json').read_bytes(), before)

    def answers(self, *entries):
        return [self.response(entry) for entry in entries]

    def assert_not_adopted(self, before):
        self.assertEqual((self.project / 'record.json').read_bytes(), before)
        cloud.generate_asset.assert_not_called()
        self.assertFalse(cloud._active)

    def test_success_preserves_audio_original_assets_and_requires_separate_adoption(self):
        before = (self.project / 'record.json').read_bytes()
        original_audio = (self.project / 'narration.wav').read_bytes()
        original_image = (self.project / 'ref3.png').read_bytes()
        with patch.object(cloud, 'chat_json', side_effect=self.answers(
                self.selection(), proposal(), {'passed': True, 'issues': []})) as model, \
             patch.object(shotcraft, 'neighbor_templates', return_value=[{'shot_id': 'neighbor',
                'position': 'previous', 'recipes': ['carousel-3d'], 'source': 'completed_preview'}]):
            self.start(max_image_requests=0)
            task = self.finish()
            self.assertEqual(task['status'], 'completed', task)
            receipt = task['preview']
            self.assertEqual(model.call_count, 3)
            director_payload = model.call_args_list[1].args[2]
            selector_payload = model.call_args_list[0].args[2]
            self.assertEqual(selector_payload['template_taxonomy']['version'], shotcraft.CAPABILITY_VERSION)
            self.assertEqual(selector_payload['neighbor_templates'][0]['motion_families'], ['sequential_gallery'])
            self.assertTrue(all(row['capabilities']['content_tags'] for row in selector_payload['catalog']['items']))
            self.assertEqual(director_payload['selected_recipes'][0]['capabilities']['content_tags'], ['summary'])
            self.assertEqual(model.call_args_list[2].args[2]['template_capabilities']['outro-group-photo-launch']['evidence_support'], 'not_recommended')
            self.assertTrue(director_payload['selected_recipes'][0]['card']['complete'])
            self.assertTrue(all(source['complete'] for source in director_payload['selected_recipes'][0]['implementations']))
            self.assertEqual(director_payload['frozen_source_style'], source_style())
            self.assertEqual(model.call_args_list[1].kwargs['max_tokens'], 16000)
            self.assertTrue(model.call_args_list[1].kwargs['thinking'])
            self.assertTrue(all(call.args[0]['model'] == self.config['model'] for call in model.call_args_list))
            for _ in range(3):
                cloud.latest(self.identity, 'shot1', self.request)
            self.assertEqual(model.call_count, 3, 'polling cannot spend another model call')
        self.assertEqual(receipt['template_mode'], 'shotcraft')
        self.assertEqual(receipt['template_ids'], ['outro-group-photo-launch'])
        self.assertEqual(receipt['capability_version'], shotcraft.CAPABILITY_VERSION)
        self.assertEqual(receipt['selection_reason'], self.selection()['reason'])
        self.assertEqual(receipt['motion_families'], ['gather'])
        self.assertEqual(receipt['planner'], 'ds_api')
        self.assertEqual(receipt['renderer'], 'ocv-remotion-shotcraft-v1')
        self.assertEqual(receipt['metrics']['frames'], 75)
        self.assertEqual(receipt['metrics']['duration_seconds'], 2.5)
        self.assertEqual(receipt['source_style'], source_style())
        self.assertEqual(len(task['usage']['model_calls']), 3)
        self.assertEqual(task['usage']['image_requests'], 0)
        self.assertEqual(receipt['max_image_requests'], 0)
        render = cloud.render_scene.call_args.kwargs
        self.assertEqual(render['duration'], 2.5)
        self.assertEqual(render['start'], 0)
        self.assertEqual(render['assets']['ref_03'].read_bytes(), original_image)
        self.assertEqual((self.project / 'narration.wav').read_bytes(), original_audio)
        self.assertTrue(cloud.previews.latest(self.identity, 'shot1', self.request)['available'])
        for file in (self.project / 'assets').rglob('*.json'):
            self.assertNotIn(self.config['api_key'], file.read_text(encoding='utf-8'))
        self.assert_not_adopted(before)

    def test_crop_reaches_renderer_and_review_without_generating_or_adopting(self):
        from PIL import Image
        before = (self.project / 'record.json').read_bytes()
        raw = proposal()
        raw['crops'] = [dict(id='photo', source='ref_03', box=[0, 0, .5, .5])]
        raw['visual_basis']['elements'][0]['output_asset_id'] = 'photo'
        raw['scene']['stages'][0]['items'][0].update(asset_id='photo', title='', body='')
        with patch.object(cloud, 'chat_json', side_effect=self.answers(
                self.selection(), raw, {'passed': True, 'issues': []})) as model:
            self.start()
            task = self.finish()
        self.assertEqual(task['status'], 'completed', task)
        rendered = cloud.render_scene.call_args.kwargs['assets']
        with Image.open(rendered['ref_03']) as original, Image.open(rendered['photo']) as cropped:
            import math
            expected = original.convert('RGBA').crop((0, 0, math.ceil(original.width/2), math.ceil(original.height/2)))
            self.assertEqual(cropped.size, expected.size)
            self.assertEqual(cropped.tobytes(), expected.tobytes())
        self.assertIn('photo', model.call_args_list[2].args[3])
        self.assertIn('ref_03', model.call_args_list[2].args[3])
        self.assertNotIn('photo', model.call_args_list[1].args[3])
        self.assertEqual(task['preview']['material_policy'], 'original_pixels_with_crops')
        self.assertEqual(task['usage']['image_requests'], 0)
        self.assert_not_adopted(before)

    def test_crop_ids_are_data_not_windows_filenames(self):
        raw = proposal()
        raw['crops'] = [dict(id='photo:hospital', source='ref_03', box=[0, 0, .5, .5])]
        raw['visual_basis']['elements'][0]['output_asset_id'] = 'photo:hospital'
        raw['scene']['stages'][0]['items'][0]['asset_id'] = 'photo:hospital'
        prepared = shotcraft.prepare_original_crops(plans.ShotcraftProposal.model_validate(raw),
            {'ref_03': self.project/'ref3.png'}, self.project/'crop_test')
        self.assertEqual(prepared['photo:hospital'].name, 'crop_00.png')
        self.assertTrue(prepared['photo:hospital'].is_file())

    def test_source_style_stays_frozen_across_visual_repair(self):
        before = (self.project / 'record.json').read_bytes()
        repair = proposal()
        repair['source_style']['palette']['background'] = '#FFFFFF'
        repair['scene']['palette']['background'] = '#FFFFFF'
        repair['scene']['background'] = {'kind': 'paper'}
        untouched = copy.deepcopy(repair)
        with patch.object(cloud, 'chat_json', side_effect=self.answers(self.selection(), proposal(),
                {'passed': False, 'issues': ['第一轮标签遮挡']}, repair, {'passed': True})) as model:
            self.start(); task = self.finish()
        self.assertEqual(task['status'], 'completed', task)
        self.assertEqual(model.call_count, 5)
        self.assertEqual(cloud.render_scene.call_count, 2)
        for call in cloud.render_scene.call_args_list:
            scene = call.kwargs['plan'].model_dump(mode='json')
            self.assertEqual(scene['palette'], source_style()['palette'])
            self.assertEqual(scene['background'], source_style()['background'])
        self.assertEqual(repair, untouched)
        self.assertEqual(task['preview']['source_style'], source_style())
        self.assert_not_adopted(before)

    def test_invalid_recipe_selection_is_bounded_before_design_or_render(self):
        before = (self.project / 'record.json').read_bytes()
        invalid = {**self.selection(), 'recipes': ['unknown-custom-code']}
        with patch.object(cloud, 'chat_json', return_value=self.response(invalid)) as model:
            self.start(); task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(model.call_count, 2)
        cloud.render_scene.assert_not_called()
        self.assertFalse(cloud.previews._pointer(self.project, 'shot1').is_file())
        self.assert_not_adopted(before)

    def test_too_short_selection_is_repaired_before_any_director_call(self):
        before = (self.project / 'record.json').read_bytes()
        impossible = {**self.selection(), 'recipes': ['spotlight-hero-card']}
        with patch.object(cloud, 'chat_json', side_effect=self.answers(
                impossible, self.selection(), proposal(), {'passed': True})) as model:
            self.start(); task = self.finish()
        self.assertEqual(task['status'], 'completed', task)
        self.assertEqual(model.call_count, 4)
        self.assertEqual([call.args[1] for call in model.call_args_list],
                         [shotcraft.SELECTION, shotcraft.SELECTION, shotcraft.DIRECTOR, shotcraft.REVIEW])
        first_catalog = model.call_args_list[0].args[2]['catalog']
        self.assertNotIn('spotlight-hero-card', [row['id'] for row in first_catalog['items']])
        self.assertIn('时长无法容纳', model.call_args_list[1].args[2]['feedback'])
        self.assertEqual(cloud.render_scene.call_count, 1)
        self.assert_not_adopted(before)

    def test_repeated_too_short_selection_stops_before_design(self):
        before = (self.project / 'record.json').read_bytes()
        impossible = {**self.selection(), 'recipes': ['spotlight-hero-card']}
        with patch.object(cloud, 'chat_json', return_value=self.response(impossible)) as model:
            self.start(); task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(model.call_count, 2)
        self.assertIn('时长无法容纳', task['message'])
        cloud.render_scene.assert_not_called()
        self.assert_not_adopted(before)

    def test_new_eligible_recipe_gets_full_context_and_requires_another_design_before_render(self):
        before = (self.project / 'record.json').read_bytes()
        changed = proposal(); changed['scene']['stages'][0]['recipe'] = 'word-relay-filmstrip'
        with patch.object(cloud, 'chat_json', side_effect=self.answers(
                self.selection(), changed, changed, {'passed': True})) as model, \
             patch.object(shotcraft, 'recipe_context', wraps=shotcraft.recipe_context) as contexts:
            submitted = self.start(); task = self.finish()
        self.assertEqual(task['status'], 'completed', task)
        self.assertEqual(model.call_count, 4)
        self.assertEqual([call.args[1] for call in model.call_args_list],
                         [shotcraft.SELECTION, shotcraft.DIRECTOR, shotcraft.DIRECTOR, shotcraft.REVIEW])
        first = model.call_args_list[1].args[2]
        retry = model.call_args_list[2].args[2]
        self.assertEqual([card['id'] for card in first['selected_recipes']], ['outro-group-photo-launch'])
        self.assertIn('word-relay-filmstrip', [card['id'] for card in retry['selected_recipes']])
        self.assertIn('完整说明与实现已补入', retry['feedback'])
        self.assertIn('两次修正额度', retry['feedback'])
        self.assertTrue(any(source['path'] == 'word-relay-filmstrip.tsx'
            for card in retry['selected_recipes'] for source in card['implementations']))
        self.assertEqual(contexts.call_count, 2)
        self.assertEqual(cloud.render_scene.call_count, 1, 'unread first draft must not render')
        stored = json.loads((cloud._job(self.project, submitted['task_id']).parent / 'selected_recipes.json').read_text(encoding='utf-8'))
        self.assertIn('word-relay-filmstrip', [card['id'] for card in stored])
        self.assertEqual(task['preview']['template_ids'], ['word-relay-filmstrip'])
        self.assert_not_adopted(before)

    def test_recipe_discovered_on_final_design_does_not_extend_repair_budget(self):
        before = (self.project / 'record.json').read_bytes()
        invalid = proposal(); invalid['scene']['stages'][0]['items'][0]['asset_id'] = 'missing-ref'
        changed = proposal(); changed['scene']['stages'][0]['recipe'] = 'word-relay-filmstrip'
        with patch.object(cloud, 'chat_json', side_effect=self.answers(
                self.selection(), invalid, invalid, changed)) as model:
            submitted = self.start(); task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(model.call_count, 4)
        self.assertEqual(task['attempt'], 2)
        self.assertIn('已达到两次修正上限', task['message'])
        cloud.render_scene.assert_not_called()
        stored = json.loads((cloud._job(self.project, submitted['task_id']).parent / 'selected_recipes.json').read_text(encoding='utf-8'))
        self.assertIn('word-relay-filmstrip', [card['id'] for card in stored])
        self.assertFalse(cloud.previews._pointer(self.project, 'shot1').is_file())
        self.assert_not_adopted(before)

    def test_ineligible_new_recipe_is_not_hydrated_or_rendered(self):
        before = (self.project / 'record.json').read_bytes()
        changed = proposal(); changed['scene']['stages'][0]['recipe'] = 'spotlight-hero-card'
        with patch.object(cloud, 'chat_json', side_effect=self.answers(
                self.selection(), changed, changed, changed)) as model, \
             patch.object(shotcraft, 'recipe_context', wraps=shotcraft.recipe_context) as contexts:
            self.start(); task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(model.call_count, 4)
        self.assertEqual(contexts.call_count, 1, 'only the initially selected recipe is read for the director')
        cloud.render_scene.assert_not_called()
        self.assert_not_adopted(before)

    def test_excessive_review_images_stop_before_render_not_after_billing_review(self):
        before = (self.project / 'record.json').read_bytes()
        assets = {'core': self.project / 'core.png', 'ref_03': self.project / 'ref3.png'}
        assets.update({f'extra_{index}': self.project / 'ref3.png' for index in range(26)})
        with patch.object(cloud, 'collect_assets', return_value=assets), \
             patch.object(cloud, 'chat_json', side_effect=self.answers(
                self.selection(), proposal(), proposal(), proposal())) as model:
            self.start(); task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertIn('33张完整审查图片', task['message'])
        self.assertEqual(model.call_count, 4)
        self.assertNotIn(shotcraft.REVIEW, [call.args[1] for call in model.call_args_list])
        cloud.render_scene.assert_not_called()
        self.assert_not_adopted(before)

    def test_missing_or_invalid_item_proof_is_not_silently_skipped(self):
        before = (self.project / 'record.json').read_bytes()
        for name in ('missing-item.png', '../escape.png', 'not-a-png.jpg', ''):
            with self.subTest(name=name):
                def render(**kwargs):
                    metrics = self.render(**kwargs)
                    metrics['item_frames'] = [{'id': 'original', 'file': name}]
                    return metrics
                with patch.object(cloud, 'chat_json', side_effect=self.answers(self.selection(), proposal())) as model, \
                     patch.object(cloud, 'render_scene', side_effect=render):
                    self.start(); task = self.finish()
                self.assertEqual(task['status'], 'failed', task)
                self.assertIn('核查帧缺失或路径无效', task['message'])
                self.assertEqual(model.call_count, 2)
                self.assertFalse(cloud.previews._pointer(self.project, 'shot1').is_file())
                self.assert_not_adopted(before)

    def test_valid_item_proof_is_forwarded_to_visual_review(self):
        before = (self.project / 'record.json').read_bytes()
        def render(**kwargs):
            metrics = self.render(**kwargs)
            metrics['item_frames'] = [{'id': 'original', 'file': 'detail_end.png'}]
            return metrics
        with patch.object(cloud, 'chat_json', side_effect=self.answers(
                self.selection(), proposal(), {'passed': True})) as model, \
             patch.object(cloud, 'render_scene', side_effect=render):
            self.start(); task = self.finish()
        self.assertEqual(task['status'], 'completed', task)
        self.assertIn('render_detail_end', model.call_args_list[2].args[3])
        self.assertEqual(model.call_args_list[2].args[2]['metrics']['item_frames'][0]['id'], 'original')
        self.assert_not_adopted(before)

    def test_context_overflow_stops_after_selection_without_director_or_render(self):
        before = (self.project / 'record.json').read_bytes()
        with patch.object(shotcraft, 'recipe_context', side_effect=ValueError('超过本次上下文预算，不能截断模板')), \
             patch.object(cloud, 'chat_json', return_value=self.response(self.selection())) as model:
            self.start(); task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertIn('上下文预算', task['message'])
        self.assertEqual(model.call_count, 1)
        cloud.render_scene.assert_not_called()
        self.assert_not_adopted(before)

    def test_character_count_error_reaches_next_director_feedback_verbatim(self):
        before = (self.project / 'record.json').read_bytes()
        bad = proposal()
        text = 'DOI:10.1234/ABC-2026.0005'
        bad['scene']['stages'][0]['items'][0]['body'] = text
        with patch.object(cloud, 'chat_json', side_effect=self.answers(
                self.selection(), bad, proposal(), {'passed': True})) as model:
            self.start(); task = self.finish()
        self.assertEqual(task['status'], 'completed', task)
        self.assertEqual(model.call_count, 4)
        feedback = model.call_args_list[2].args[2]['feedback']
        for detail in ('result', 'original.body', f'实长{len(text)}字符', text, 'DOI不要截断'):
            self.assertIn(detail, feedback)
        self.assertEqual(cloud.render_scene.call_count, 1)
        self.assert_not_adopted(before)

    def test_invalid_proposals_stop_after_three_designs_without_publishing(self):
        before = (self.project / 'record.json').read_bytes()
        bad = proposal(); bad['scene']['stages'][0]['items'][0]['asset_id'] = 'invented-image'
        with patch.object(cloud, 'chat_json', side_effect=self.answers(self.selection(), bad, bad, bad)) as model:
            self.start(); task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(model.call_count, 4, 'one selection plus at most three designs')
        self.assertEqual(len(task['usage']['model_calls']), 4)
        cloud.render_scene.assert_not_called()
        self.assertFalse(cloud.previews._pointer(self.project, 'shot1').is_file())
        self.assert_not_adopted(before)

    def test_eight_call_budget_includes_selection_repair_and_three_visual_reviews(self):
        before = (self.project / 'record.json').read_bytes()
        answers = [{'recipes': ['unknown']}, self.selection()]
        for _ in range(3):
            answers.extend([proposal(), {'passed': False, 'issues': ['原证据文字遮挡']}])
        with patch.object(cloud, 'chat_json', side_effect=self.answers(*answers)) as model:
            self.start(); task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(model.call_count, 8)
        self.assertEqual(len(task['usage']['model_calls']), 8)
        self.assertEqual(cloud.render_scene.call_count, 3)
        self.assertEqual(task['usage']['image_requests'], 0)
        self.assertFalse(cloud.previews._pointer(self.project, 'shot1').is_file())
        self.assert_not_adopted(before)

    def test_stop_before_worker_makes_no_cloud_call(self):
        before = (self.project / 'record.json').read_bytes()
        with patch.object(cloud, 'chat_json') as model:
            submitted = self.start()
            cloud.stop(self.identity, 'shot1', submitted['task_id'], self.request)
            task = self.finish()
        self.assertEqual(task['status'], 'stopped', task)
        model.assert_not_called()
        cloud.render_scene.assert_not_called()
        self.assert_not_adopted(before)

    def test_stop_after_inflight_selection_prevents_design_render_and_publish(self):
        before = (self.project / 'record.json').read_bytes()
        def answer(*args, **kwargs):
            cloud._active[(str(self.project), 'shot1')][1].set()
            return self.response(self.selection())
        with patch.object(cloud, 'chat_json', side_effect=answer) as model:
            self.start(); task = self.finish()
        self.assertEqual(task['status'], 'stopped', task)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(len(task['usage']['model_calls']), 1)
        cloud.render_scene.assert_not_called()
        self.assertFalse(cloud.previews._pointer(self.project, 'shot1').is_file())
        self.assert_not_adopted(before)

    def test_stop_during_render_does_not_review_or_publish_partial_preview(self):
        before = (self.project / 'record.json').read_bytes()
        def render(**kwargs):
            result = self.render(**kwargs)
            cloud._active[(str(self.project), 'shot1')][1].set()
            return result
        with patch.object(cloud, 'chat_json', side_effect=self.answers(self.selection(), proposal())) as model, \
             patch.object(cloud, 'render_scene', side_effect=render):
            self.start(); task = self.finish()
        self.assertEqual(task['status'], 'stopped', task)
        self.assertEqual(model.call_count, 2)
        self.assertFalse(cloud.previews._pointer(self.project, 'shot1').is_file())
        self.assert_not_adopted(before)

    def test_render_failure_preserves_formal_record_and_does_not_adopt(self):
        before = (self.project / 'record.json').read_bytes()
        with patch.object(cloud, 'chat_json', side_effect=self.answers(self.selection(), proposal())) as model, \
             patch.object(cloud, 'render_scene', side_effect=RuntimeError('离线模拟渲染失败')):
            self.start(); task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(model.call_count, 2)
        self.assertIn('渲染失败', task['message'])
        self.assertFalse(cloud.previews._pointer(self.project, 'shot1').is_file())
        self.assert_not_adopted(before)

    def test_requested_original_evidence_stops_without_render_or_extra_design(self):
        before = (self.project / 'record.json').read_bytes()
        answer = proposal(); answer['scene'] = None
        answer['questions'] = ['请提供不含排版的原始证据截图']
        with patch.object(cloud, 'chat_json', side_effect=self.answers(self.selection(), answer)) as model:
            self.start(); task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(task['stage'], 'needs_input')
        self.assertEqual(model.call_count, 2)
        cloud.render_scene.assert_not_called()
        self.assert_not_adopted(before)

    def test_upstream_failure_records_possible_charge_without_switch_or_retry(self):
        before = (self.project / 'record.json').read_bytes()
        with patch.object(cloud, 'chat_json', side_effect=shotcraft.CloudCallError('离线模拟上游中断')) as model:
            self.start(); task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(task['usage']['model_calls'][0]['status'], 'unconfirmed')
        self.assertTrue(task['usage']['model_calls'][0]['may_charge'])
        cloud.render_scene.assert_not_called()
        self.assert_not_adopted(before)

    def test_changed_subtitles_after_review_reject_stale_preview(self):
        def answer(config, system, payload, images, cancelled, **kwargs):
            if system == shotcraft.SELECTION:
                return self.response(self.selection())
            if system == shotcraft.DIRECTOR:
                return self.response(proposal())
            self.record['scenes'][0]['text'] = '测试期间改过的最终字幕。'
            self.persist()
            return self.response({'passed': True})
        with patch.object(cloud, 'chat_json', side_effect=answer) as model:
            self.start(); task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(model.call_count, 3)
        self.assertIn('未发布过期预览', task['message'])
        self.assertFalse(cloud.previews._pointer(self.project, 'shot1').is_file())
        cloud.generate_asset.assert_not_called()
        self.assertFalse(cloud._active)

    def test_model_call_guard_refuses_a_ninth_request(self):
        before = (self.project / 'record.json').read_bytes()
        submitted = self.start()
        path = cloud._job(self.project, submitted['task_id'])
        task = cloud._read_job(self.project, submitted['task_id'])
        task['usage']['model_calls'] = [{'status': 'completed'} for _ in range(8)]
        cloud._write(path, task)
        with patch.object(cloud, 'chat_json') as model:
            task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertIn('8次模型调用上限', task['message'])
        model.assert_not_called()
        self.assert_not_adopted(before)

    def test_missing_library_fails_before_task_or_cloud_bill(self):
        with patch.object(shotcraft, 'preflight_library', side_effect=ValueError('模板库缺失')), \
             patch.object(cloud, 'chat_json') as model:
            with self.assertRaises(HTTPException) as error:
                self.start()
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.workers, [])
        self.assertFalse(cloud._active)
        model.assert_not_called()

    def test_manual_template_choice_is_persisted_and_sent_to_both_model_stages(self):
        chosen = ['outro-group-photo-launch']
        with patch.object(cloud, 'chat_json', side_effect=self.answers(
                self.selection(), proposal(), {'passed': True, 'issues': []})) as model:
            queued = self.start(template_ids=chosen)
            task = self.finish()
        self.assertEqual(queued['requested_template_ids'], chosen)
        self.assertEqual(task['status'], 'completed', task)
        self.assertEqual(task['requested_template_ids'], chosen)
        self.assertEqual(task['preview']['requested_template_ids'], chosen)
        for index in (0, 1):
            payload = model.call_args_list[index].args[2]
            self.assertEqual(payload['template_selection']['requested_template_ids'], chosen)
            self.assertTrue(payload['template_selection']['must_use_all'])
            self.assertFalse(payload['template_selection']['allow_other_templates'])
        self.assertEqual([item['id'] for item in model.call_args_list[0].args[2]['catalog']['items']], chosen)

    def test_manual_too_long_template_never_queues_or_bills(self):
        before = (self.project/'record.json').read_bytes()
        with patch.object(cloud, 'chat_json') as model, self.assertRaises(HTTPException) as error:
            self.start(template_ids=['card-stack'])
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn('未调用模型', error.exception.detail)
        self.assertEqual(self.workers, [])
        model.assert_not_called()
        self.assert_not_adopted(before)

    def test_manual_selection_cannot_be_substituted_by_selector(self):
        wrong = {**self.selection(), 'recipes': ['word-relay-filmstrip']}
        with patch.object(cloud, 'chat_json', return_value=self.response(wrong)) as model:
            self.start(template_ids=['outro-group-photo-launch'])
            task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertEqual(model.call_count, 2)
        cloud.render_scene.assert_not_called()

    def test_manual_selector_must_keep_every_requested_template(self):
        import wave
        with wave.open(str(self.project/'narration.wav'), 'wb') as audio:
            audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\x15\x00' * 128000)
        self.shot['end'] = 6
        self.cues[-1]['end'] = 6
        self.persist()
        with patch.object(cloud, 'chat_json', return_value=self.response(self.selection())) as model:
            self.start(template_ids=['outro-group-photo-launch', 'word-relay-filmstrip'])
            task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertIn('全部模板', task['message'])
        self.assertEqual(model.call_count, 2)
        cloud.render_scene.assert_not_called()

    def test_manual_selection_cannot_expand_when_director_changes_its_mind(self):
        changed = proposal()
        changed['scene']['stages'][0]['recipe'] = 'word-relay-filmstrip'
        with patch.object(cloud, 'chat_json', side_effect=self.answers(
                self.selection(), changed, changed, changed)) as model, \
             patch.object(shotcraft, 'recipe_context', wraps=shotcraft.recipe_context) as contexts:
            self.start(template_ids=['outro-group-photo-launch'])
            task = self.finish()
        self.assertEqual(task['status'], 'failed', task)
        self.assertIn('用户选择', task['message'])
        self.assertEqual(model.call_count, 4)
        self.assertEqual(contexts.call_count, 1)
        cloud.render_scene.assert_not_called()

    def test_shotcraft_capability_required_before_queue_and_again_before_paid_call(self):
        requirements = dict(required_renderer=plans.RENDERER_VERSION, required_plan_version=plans.VERSION)
        with patch.object(cloud, 'preflight_remotion') as preflight, \
             patch.object(cloud, 'chat_json', side_effect=self.answers(
                 self.selection(), proposal(), {'passed': True, 'issues': []})):
            submitted = self.start()
            self.assertEqual(submitted['renderer'], plans.RENDERER_VERSION)
            preflight.assert_called_once_with(**requirements)
            task = self.finish()
        self.assertEqual(task['status'], 'completed', task)
        self.assertEqual(preflight.call_count, 2)
        self.assertTrue(callable(preflight.call_args.kwargs['cancelled']))
        for key, value in requirements.items():
            self.assertEqual(preflight.call_args.kwargs[key], value)

    def test_old_renderer_capability_failure_never_queues_or_calls_model(self):
        before = (self.project / 'record.json').read_bytes()
        with patch.object(cloud, 'preflight_remotion', side_effect=RuntimeError(
                '请更新渲染组件；本次未调用云端模型')) as preflight, \
             patch.object(cloud, 'chat_json') as model:
            with self.assertRaises(HTTPException) as error:
                self.start()
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn('请更新渲染组件', error.exception.detail)
        preflight.assert_called_once_with(required_renderer=plans.RENDERER_VERSION,
                                          required_plan_version=plans.VERSION)
        self.assertEqual(self.workers, [])
        model.assert_not_called()
        self.assert_not_adopted(before)

    def test_legacy_omitted_mode_still_uses_semantic_worker(self):
        before = (self.project / 'record.json').read_bytes()
        semantic = legacy_cloud.VibeCloudTests.proposal(self)
        with patch.object(shotcraft, 'run_worker') as new_worker, \
             patch.object(shotcraft, 'preflight_library') as library, \
             patch.object(cloud, 'chat_json', side_effect=self.answers(semantic, {'passed': True})) as model:
            cloud.start(self.identity, 'shot1', cloud.CloudRequest(confirm_cloud=True), self.request)
            task = self.finish()
        self.assertEqual(task['status'], 'completed', task)
        self.assertEqual(task['template_mode'], 'semantic')
        self.assertEqual(task['preview']['renderer'], 'ocv-remotion-v1')
        self.assertEqual(model.call_count, 2)
        new_worker.assert_not_called(); library.assert_not_called()
        self.assert_not_adopted(before)


if __name__ == '__main__':
    unittest.main()
