"""Offline layered-trial safety checks; synthetic images and mocked model calls only."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

try:
    from tools import vibe_layered_trial as trial
except ImportError:
    raise unittest.SkipTest('Private layered prototype is not distributed; production layered workflow has separate tests')


class VibeLayeredTrialTests(unittest.TestCase):
    API_KEY = 'offline-test-secret-must-never-be-written'

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ocv-layered-offline-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        assets = self.root / 'assets'
        assets.mkdir()
        Image.new('RGB', (96, 54), (38, 66, 91)).save(assets / 'background.png')
        Image.new('RGB', (96, 54), (238, 230, 211)).save(assets / 'core.png')
        for name, size, colour in (
            ('professor', (64, 64), (180, 48, 40, 255)),
            ('gold_patch', (80, 30), (198, 153, 64, 255)),
        ):
            image = Image.new('RGBA', size, (0, 0, 0, 0))
            ImageDraw.Draw(image).rectangle(
                (size[0] // 4, size[1] // 4, size[0] * 3 // 4, size[1] * 3 // 4),
                fill=colour,
            )
            image.save(assets / (name + '.png'))
        self.raw_context = {
            'project_id': 'offline-project', 'shot_id': 'offline-shot',
            'canvas': [1920, 1080], 'duration': 13.8, 'fps': 30,
            'narration': '机构甲和机构乙联合开展研究。',
            'subtitles': [{'id': 'cue-1', 'start': 0, 'end': 13.8,
                           'text': '机构甲和机构乙联合开展研究。'}],
            'verified_text': ['机构甲', '机构乙'],
            'source_images': {'core': 'assets/core.png'},
            'assets': {name: f'assets/{name}.png'
                       for name in ('background', 'professor', 'gold_patch')},
            'aesthetic_brief': '背景常驻、主体与真实金贴片独立入场、机构文字原生排版。',
        }
        self.context_path = self.root / 'context.json'
        self.context_path.write_text(json.dumps(self.raw_context, ensure_ascii=False), encoding='utf-8')
        self.output = self.root / 'trial-output'

    def config(self):
        return {'base_url': 'https://offline.example.invalid',
                'model': 'offline-vision', 'api_key': self.API_KEY}

    def plan(self):
        return {
            'version': 'ocv-layered-v1', 'canvas': [1920, 1080],
            'background': '#F3EEE4', 'fade_in': 0, 'fade_out': 0,
            'elements': [
                {'id': 'background', 'type': 'image', 'role': 'background',
                 'box': [0, 0, 1920, 1080], 'at': 0, 'end': 13.8,
                 'enter': 0, 'exit': 0, 'motion': 'none',
                 'asset_id': 'background', 'fit': 'cover'},
                {'id': 'professor', 'type': 'image', 'box': [60, 60, 900, 1020],
                 'at': .1, 'enter': .6, 'asset_id': 'professor', 'fit': 'contain'},
                {'id': 'gold_patch', 'type': 'image', 'box': [130, 790, 710, 160],
                 'at': .6, 'enter': .45, 'asset_id': 'gold_patch', 'fit': 'contain'},
                {'id': 'institution', 'type': 'text', 'box': [1040, 210, 760, 580],
                 'at': 1.2, 'text': '机构甲\n机构乙', 'font_size': 56,
                 'bold': True, 'fill': '#172F3A'},
            ],
        }

    def proposal(self):
        return {'summary': '独立背景、人物、金贴片与机构文字',
                'plan': self.plan(), 'design_notes': ['按字幕时间依次出现。']}

    def normalized_input(self):
        return {'plan': self.plan(), 'assets': {
            key: str((self.context_path.parent / value).resolve())
            for key, value in self.raw_context['assets'].items()
        }, 'duration': 13.8, 'fps': 30}

    def assert_no_secret_on_disk(self):
        secret = self.API_KEY.encode('utf-8')
        for path in self.output.rglob('*') if self.output.exists() else []:
            if path.is_file():
                self.assertNotIn(secret, path.read_bytes(), str(path))

    def test_context_paths_are_resolved_against_context_not_current_directory(self):
        original = self.context_path.read_bytes()
        context = trial.load_context(self.context_path)
        self.assertEqual(context['canvas'], [1920, 1080])
        self.assertEqual(context['duration'], 13.8)
        self.assertEqual(context['subtitles'], self.raw_context['subtitles'])
        for field in ('assets', 'source_images'):
            for name, file in context[field].items():
                self.assertEqual(Path(file), (self.root / self.raw_context[field][name]).resolve())
        self.assertEqual(self.context_path.read_bytes(), original)

    def test_schema_exposes_bound_asset_ids_and_the_layered_plan_version(self):
        context = trial.load_context(self.context_path)
        schema = trial.proposal_schema(context)
        self.assertIsInstance(schema, dict)
        serialized = json.dumps(schema, ensure_ascii=False)
        for token in ('ocv-layered-v1', 'background', 'professor', 'gold_patch', 'plan', 'elements'):
            self.assertIn(token, serialized)
        self.assertNotIn(self.API_KEY, serialized)

    def test_new_composition_uses_this_shots_assets_canvas_and_no_mandatory_rows(self):
        ctx=copy.deepcopy(self.raw_context)
        ctx.update(canvas=[720,1280], assets={'background':'assets/background.png','campus':'assets/core.png'},
                   opaque_foregrounds=['campus'])
        ctx['reference_layout']={
            'version':'ocv-reference-layout-v1','background_color':'#112233','ink':'#FAFAFA','exit':.3,
            'subjects':[{'slot':'architecture','asset_id':'campus','box':[40,250,640,480]}],
            'labels':[{'slot':'caption','text':'机构甲','box':[50,350,500,72],'font_size':48,'align':'right'}],
        }
        self.context_path.write_text(json.dumps(ctx,ensure_ascii=False),encoding='utf8')
        prepared=trial.prepare_trial(self.context_path,self.output)
        self.assertEqual(prepared['payload']['display_asset_ids'],['background','campus'])
        self.assertEqual(prepared['payload']['row_order'],[])
        self.assertNotIn('四行',prepared['system'])
        cues=[{'slot':sid,'at':0,'enter':.5,'motion':'fade'} for sid in ('architecture','caption')]
        wrapper=trial.validate_proposal({'summary':'建筑与机构名','cues':cues},prepared['context'])
        target=self.output/'ready.json'
        trial._save_preview_input(target,wrapper)
        self.assertEqual(json.loads(target.read_text(encoding='utf8'))['plan']['canvas'],ctx['canvas'])
        audit=json.loads((self.output/'text_legibility.json').read_text(encoding='utf8'))
        self.assertEqual(audit['protected'],['caption'])
        self.assertNotIn('readability_halo',wrapper['plan']['elements'][-1])

    def test_prepare_flattens_model_views_on_pale_blue_without_changing_render_alpha(self):
        originals = {name: (self.root / relative).read_bytes()
                     for name, relative in self.raw_context['assets'].items()}
        with patch.object(trial, 'get_config') as config, patch.object(trial, 'chat_json') as chat:
            prepared = trial.prepare_trial(self.context_path, self.output)
        config.assert_not_called()
        chat.assert_not_called()
        for filename in ('context.snapshot.json', 'request.json', 'manifest.json'):
            self.assertTrue((self.output / filename).is_file(), filename)
        self.assertTrue((self.output / 'model_visuals').is_dir())
        self.assertIn('context', prepared)
        self.assertIn('payload', prepared)
        self.assertIn('images', prepared)
        self.assertIsInstance(prepared['images'], dict)
        for name in ('professor', 'gold_patch'):
            view = Path(prepared['images']['asset_' + name])
            self.assertEqual(view.suffix.lower(), '.jpg')
            self.assertEqual(view.parent, self.output / 'model_visuals')
            with Image.open(view) as image:
                self.assertEqual(image.mode, 'RGB')
                corner = image.getpixel((0, 0))
                for actual, expected in zip(corner, (230, 239, 246)):
                    self.assertLessEqual(abs(actual - expected), 5)
            original = self.root / self.raw_context['assets'][name]
            self.assertEqual(original.read_bytes(), originals[name])
            with Image.open(original) as image:
                self.assertEqual(image.mode, 'RGBA')
                self.assertEqual(image.getpixel((0, 0))[3], 0)
        for name, data in originals.items():
            self.assertEqual(hashlib.sha256((self.root / self.raw_context['assets'][name]).read_bytes()).digest(),
                             hashlib.sha256(data).digest())

    def test_frozen_request_never_mixes_updated_prompt_with_old_schema(self):
        trial.prepare_trial(self.context_path, self.output)
        saved = (self.output / 'request.json').read_bytes()
        with patch.object(trial, 'SYSTEM', 'next prompt version'):
            with self.assertRaisesRegex(ValueError, 'prompt changed'):
                trial.prepare_trial(self.context_path, self.output)
            fresh = trial.prepare_trial(self.context_path, self.root / 'new-job')
        self.assertEqual((self.output / 'request.json').read_bytes(), saved)
        self.assertEqual(fresh['system'], 'next prompt version')

    def test_validate_accepts_envelope_and_bare_plan_but_binds_only_local_assets(self):
        context = trial.load_context(self.context_path)
        for proposal in (self.proposal(), self.plan()):
            with self.subTest(envelope='plan' in proposal):
                original = copy.deepcopy(proposal)
                result = trial.validate_proposal(proposal, context)
                self.assertEqual(proposal, original)
                self.assertEqual(result['duration'], 13.8)
                self.assertEqual(result['fps'], 30)
                self.assertEqual(result['assets'], self.normalized_input()['assets'])
                self.assertEqual(result['plan']['version'], 'ocv-layered-v1')
                self.assertEqual(result['plan']['elements'][1]['end'], 13.8)
        invalid = self.proposal()
        invalid['assets'] = {'professor': 'https://example.invalid/replacement.png'}
        with self.assertRaises((ValueError, RuntimeError)):
            trial.validate_proposal(invalid, context)
        invalid = self.proposal()
        invalid['plan']['elements'][3]['style'] = {'position': 'fixed'}
        with self.assertRaises((ValueError, RuntimeError)):
            trial.validate_proposal(invalid, context)

    def test_missing_cloud_confirmation_cannot_even_read_the_key(self):
        with patch.object(trial, 'get_config') as config, patch.object(trial, 'chat_json') as chat:
            with self.assertRaises(ValueError):
                trial.run_model_attempt(self.context_path, self.output)
        config.assert_not_called()
        chat.assert_not_called()
        self.assert_no_secret_on_disk()

    def test_one_generate_and_two_explicit_repairs_preserve_artifacts_without_secrets(self):
        usage = {'model': 'offline-vision', 'prompt_tokens': 21, 'completion_tokens': 32}
        with patch.object(trial, 'get_config', return_value=self.config()) as config, \
             patch.object(trial, 'chat_json', return_value=(self.proposal(), usage)) as chat:
            results = [trial.run_model_attempt(self.context_path, self.output, confirm_cloud=True)]
            self.assertEqual(chat.call_count, 1)
            with self.assertRaises(ValueError):
                trial.run_model_attempt(self.context_path, self.output, confirm_cloud=True)
            self.assertEqual(chat.call_count, 1)
            self.assertEqual(config.call_count, 1)
            with self.assertRaises(ValueError):
                trial.run_model_attempt(self.context_path, self.output, repair=True,
                                        feedback='', confirm_cloud=True)
            self.assertEqual(chat.call_count, 1)
            self.assertEqual(config.call_count, 1)
            for feedback in ('机构字号增大，保持原文字。', '人物与金贴片不要遮挡机构名。'):
                results.append(trial.run_model_attempt(self.context_path, self.output,
                                                       repair=True, feedback=feedback,
                                                       confirm_cloud=True))
            self.assertEqual(chat.call_count, 3)
            with self.assertRaises(ValueError):
                trial.run_model_attempt(self.context_path, self.output, repair=True,
                                        feedback='不允许第三轮。', confirm_cloud=True)
            self.assertEqual(chat.call_count, 3)
            self.assertEqual(config.call_count, 3)
        for index, result in enumerate(results):
            self.assertEqual(result['status'], 'validated')
            directory = Path(result['attempt_dir'])
            self.assertEqual(directory.name, f'attempt_{index:02d}')
            for filename in ('proposal.json', 'usage.json', 'input.json'):
                self.assertTrue((directory / filename).is_file(), filename)
            self.assertEqual(json.loads((directory / 'proposal.json').read_text(encoding='utf-8')),
                             self.proposal())
            saved=json.loads((directory/'input.json').read_text(encoding='utf8'))
            report=json.loads((directory/'text_legibility.json').read_text(encoding='utf8'))
            self.assertEqual(report['protected'],['institution'])
            for e in saved['plan']['elements']:
                if e['id']=='institution':
                    self.assertIn('readability_halo',e)
                    del e['readability_halo']
            self.assertEqual(saved,trial._normalize_render_input(self.normalized_input()))
        state = json.loads((self.output / 'state.json').read_text(encoding='utf-8'))
        self.assertEqual(len(state['attempts']), 3)
        self.assert_no_secret_on_disk()

    def test_invalid_proposal_is_saved_once_with_error_and_never_automatically_retried(self):
        with patch.object(trial, 'get_config', return_value=self.config()), \
             patch.object(trial, 'chat_json', return_value=({'plan': {'invalid': True}}, {'model_calls': 1})) as chat, \
             patch.object(trial, 'validate_proposal', side_effect=ValueError('Invalid layer geometry')):
            result = trial.run_model_attempt(self.context_path, self.output, confirm_cloud=True)
        chat.assert_called_once()
        self.assertEqual(result['status'], 'failed')
        directory = Path(result['attempt_dir'])
        for filename in ('proposal.json', 'usage.json', 'error.json'):
            self.assertTrue((directory / filename).is_file(), filename)
        self.assertFalse((directory / 'input.json').exists())
        state = json.loads((self.output / 'state.json').read_text(encoding='utf-8'))
        self.assertEqual(len(state['attempts']), 1)
        self.assert_no_secret_on_disk()

    def test_provider_exception_is_not_retried_and_its_key_is_redacted_on_disk(self):
        with patch.object(trial, 'get_config', return_value=self.config()), \
             patch.object(trial, 'chat_json', side_effect=RuntimeError('transport failure: ' + self.API_KEY)) as chat:
            result = trial.run_model_attempt(self.context_path, self.output, confirm_cloud=True)
        chat.assert_called_once()
        self.assertEqual(result['status'], 'failed')
        self.assertTrue((Path(result['attempt_dir']) / 'error.json').is_file())
        self.assert_no_secret_on_disk()

    def test_missing_configured_key_fails_without_a_model_call(self):
        config = self.config()
        config['api_key'] = ''
        with patch.object(trial, 'get_config', return_value=config), \
             patch.object(trial, 'chat_json') as chat:
            result = trial.run_model_attempt(self.context_path, self.output, confirm_cloud=True)
        self.assertEqual(result['status'], 'failed')
        chat.assert_not_called()
        self.assert_no_secret_on_disk()

    def test_background_rejects_even_one_nonopaque_pixel_before_any_model_call(self):
        path = self.root / self.raw_context['assets']['background']
        for alpha in (254, 128, 0):
            with self.subTest(alpha=alpha):
                image = Image.new('RGBA', (96, 54), (38, 66, 91, 255))
                image.putpixel((0, 0), (38, 66, 91, alpha))
                image.save(path)
                with patch.object(trial, 'get_config') as config, patch.object(trial, 'chat_json') as chat:
                    with self.assertRaises(ValueError):
                        trial.run_model_attempt(self.context_path, self.root / f'bad-background-{alpha}',
                                                confirm_cloud=True)
                config.assert_not_called()
                chat.assert_not_called()

    def test_foreground_cutouts_reject_opaque_and_fully_empty_images(self):
        cases = (
            ('rgb', 'RGB', (180, 48, 40)),
            ('opaque_rgba', 'RGBA', (180, 48, 40, 255)),
            ('empty_rgba', 'RGBA', (180, 48, 40, 0)),
        )
        for name in ('professor', 'gold_patch'):
            path = self.root / self.raw_context['assets'][name]
            original = path.read_bytes()
            try:
                for label, mode, colour in cases:
                    with self.subTest(asset=name, case=label):
                        Image.new(mode, (64, 64), colour).save(path)
                        with patch.object(trial, 'get_config') as config, patch.object(trial, 'chat_json') as chat:
                            with self.assertRaises(ValueError):
                                trial.run_model_attempt(self.context_path, self.root / f'bad-{name}-{label}',
                                                        confirm_cloud=True)
                        config.assert_not_called()
                        chat.assert_not_called()
            finally:
                path.write_bytes(original)

    def test_proposal_elements_and_image_asset_ids_are_type_checked_before_node(self):
        context = trial.load_context(self.context_path)
        for value in (None, {}, 'not-an-array', 4):
            with self.subTest(elements=value):
                proposal = self.proposal()
                proposal['plan']['elements'] = value
                with patch.object(trial.subprocess, 'run') as node:
                    with self.assertRaises(ValueError):
                        trial.validate_proposal(proposal, context)
                node.assert_not_called()
        for value in (None, ['professor'], {'id': 'professor'}, 3, True):
            with self.subTest(asset_id=value):
                proposal = self.proposal()
                proposal['plan']['elements'][1]['asset_id'] = value
                with patch.object(trial.subprocess, 'run') as node:
                    with self.assertRaises(ValueError):
                        trial.validate_proposal(proposal, context)
                node.assert_not_called()

    def test_background_role_cannot_be_assigned_to_a_foreground_asset(self):
        context = trial.load_context(self.context_path)
        for index, role in ((0, 'content'), (0, None), (1, 'background'), (2, 'background')):
            with self.subTest(index=index, role=role):
                proposal = self.proposal()
                if role is None:
                    proposal['plan']['elements'][index].pop('role', None)
                else:
                    proposal['plan']['elements'][index]['role'] = role
                with patch.object(trial.subprocess, 'run') as node:
                    with self.assertRaises(ValueError):
                        trial.validate_proposal(proposal, context)
                node.assert_not_called()

    def test_prepared_context_change_is_rejected_before_reading_a_key(self):
        trial.prepare_trial(self.context_path, self.output)
        state = (self.output / 'state.json').read_bytes()
        changed = copy.deepcopy(self.raw_context)
        changed['aesthetic_brief'] = '准备后修改的动效要求。'
        self.context_path.write_text(json.dumps(changed, ensure_ascii=False), encoding='utf-8')
        with patch.object(trial, 'get_config') as config, patch.object(trial, 'chat_json') as chat:
            with self.assertRaises(ValueError):
                trial.run_model_attempt(self.context_path, self.output, confirm_cloud=True)
        config.assert_not_called()
        chat.assert_not_called()
        self.assertEqual((self.output / 'state.json').read_bytes(), state)
        self.assertFalse((self.output / 'attempt_00').exists())

    def test_prepared_asset_or_source_bytes_change_is_rejected_without_api_access(self):
        for group, name in (('assets', 'professor'), ('source_images', 'core')):
            with self.subTest(group=group):
                output = self.root / ('changed-' + group)
                trial.prepare_trial(self.context_path, output)
                state = (output / 'state.json').read_bytes()
                path = self.root / self.raw_context[group][name]
                original = path.read_bytes()
                try:
                    with Image.open(path) as source:
                        image = source.copy()
                    image.putpixel((image.width // 2, image.height // 2),
                                   (30, 100, 150, 255) if image.mode == 'RGBA' else (30, 100, 150))
                    image.save(path)
                    self.assertNotEqual(hashlib.sha256(path.read_bytes()).digest(),
                                        hashlib.sha256(original).digest())
                    with patch.object(trial, 'get_config') as config, patch.object(trial, 'chat_json') as chat:
                        with self.assertRaises(ValueError):
                            trial.run_model_attempt(self.context_path, output, confirm_cloud=True)
                    config.assert_not_called()
                    chat.assert_not_called()
                    self.assertEqual((output / 'state.json').read_bytes(), state)
                    self.assertFalse((output / 'attempt_00').exists())
                finally:
                    path.write_bytes(original)

    def test_existing_runner_lock_is_preserved_and_blocks_key_and_model_access(self):
        trial.prepare_trial(self.context_path, self.output)
        state = (self.output / 'state.json').read_bytes()
        lock = self.output / '.runner.lock'
        lock.write_text('another-offline-worker', encoding='utf-8')
        with patch.object(trial, 'get_config') as config, patch.object(trial, 'chat_json') as chat:
            with self.assertRaises(ValueError):
                trial.run_model_attempt(self.context_path, self.output, confirm_cloud=True)
        config.assert_not_called()
        chat.assert_not_called()
        self.assertEqual(lock.read_text(encoding='utf-8'), 'another-offline-worker')
        self.assertEqual((self.output / 'state.json').read_bytes(), state)
        self.assertFalse((self.output / 'attempt_00').exists())


if __name__ == '__main__':
    unittest.main()
