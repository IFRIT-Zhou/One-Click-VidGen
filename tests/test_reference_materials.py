import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
import module4_video_render as visual
import director_prompt_editor as prompt_editor
from backend.app import reference_materials as materials
from backend.app.main import GenerateRequest
from backend.app.visual_editor import VisualEditor
from backend.app import gemini_client
from backend.app import main as main_api
from scene_reference_coordinator import bind_scene_references


class ReferenceMaterialsTests(unittest.TestCase):
    def test_six_uploads_keep_labels_after_deletion(self):
        request = GenerateRequest(reference_image_ids=['a.png', 'c.png'],
            reference_image_labels={'a.png': '图1', 'c.png': '图3'},
            reference_image_notes={'c.png': '这是配音编辑界面'}).model_dump()
        self.assertEqual([row['label'] for row in materials.request_reference_catalog(request)], ['图1', '图3'])
        self.assertEqual(len(materials.request_reference_catalog({'reference_image_ids': [str(i) for i in range(6)]})), 6)

    def test_only_selected_materials_sent_and_prompt_numbers_remapped(self):
        catalog = {'图1': 'person.png', '图2': 'home.png', '图3': 'voice.png'}
        item = {'macro_scene_id': 'poster_001', 'image_prompt': '图3的配音界面', 'reference_image_ids': ['图3']}
        bound = materials.bind_material_references([item], catalog)[0]
        self.assertEqual(bound['reference_image_paths'], ['voice.png'])
        self.assertEqual(bound['reference_materials'][0]['label'], '图3')
        self.assertTrue(bound['image_prompt'].startswith('图1的配音界面'))
        response = SimpleNamespace(ok=True, json=lambda: {'taskId': 'test-task'})
        with patch.object(visual, '_reference_image_url', side_effect=lambda config, path=None: path) as uploads, patch.object(
            visual, '_request_with_cloud_refresh', return_value=response
        ) as request:
            visual._submit_poster_request(bound, {'endpoint': 'https://example.test/text-to-image', 'api_key': 'test', 'ratio': '2:1', 'resolution': '1k'}, object())
        self.assertEqual(request.call_args.kwargs['json']['imageUrls'], ['voice.png'])
        self.assertEqual(uploads.call_count, 1)
        self.assertEqual(item['image_prompt'], '图3的配音界面')
        self.assertEqual(materials.bind_material_references([bound], catalog)[0], bound)

    def test_empty_selection_does_not_inherit_protagonist_image(self):
        item = materials.bind_material_references([{'macro_scene_id': 'poster_001', 'image_prompt': '房间', 'reference_image_ids': [], 'character_ids': ['主角']}], {'图1': 'person.png'})[0]
        with patch.object(visual, '_reference_image_url') as upload, patch.object(visual, '_request_with_cloud_refresh', return_value=SimpleNamespace(ok=True, json=lambda: {'taskId': 't'})) as request:
            visual._submit_poster_request(item, {'endpoint': 'https://example.test/text-to-image', 'api_key': 'test', 'ratio': '2:1', 'resolution': '1k'}, object())
        upload.assert_not_called()
        self.assertNotIn('imageUrls', request.call_args.kwargs['json'])

    def test_three_materials_can_share_one_scene_reference(self):
        item = {'image_prompt': '图3的物件和图5图6在房间', 'reference_image_ids': ['图3', '图5', '图6']}
        mapping = materials.bind_material_references([item, item], {'图3': 'a.png', '图5': 'b.png', '图6': 'c.png'})
        with tempfile.TemporaryDirectory() as directory:
            scene = Path(directory) / 'scene.png'
            scene.write_bytes(b'image')
            result = bind_scene_references(mapping, {'scenes': [{'scene_id': 'location_1', 'members': [0, 1], 'reference_prompt': '房间', 'reason': '同一房间'}]}, {'location_1': scene}, {})
        self.assertEqual(len(result[0]['reference_image_paths']), 4)
        self.assertEqual(result[0]['scene_reference']['input_number'], 4)
        self.assertEqual(result[0]['reference_materials'][2]['label'], '图6')

    def test_analysis_sends_image_and_reuses_content_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'a.png'
            Image.new('RGB', (8, 8), 'white').save(image)
            with patch.object(materials, 'generate_gemini_text', return_value='{"kind":"interface","description":"配音编辑界面"}') as call:
                first = materials.analyze_reference(image)
                second = materials.analyze_reference(image)
            self.assertEqual(first, second)
            self.assertEqual(call.call_count, 1)
            self.assertTrue(call.call_args.kwargs['image_data']['data'])

    def test_interface_is_not_dropped_for_no_character_shot_in_both_modes(self):
        metadata = [{'label': '图2', 'kind': 'interface', 'description': '配音编辑界面'}]
        for mode in ('stable', 'enhanced_beta'):
            with patch.dict(os.environ, {'DIRECTOR_STRATEGY': mode, 'USER_REFERENCE_IMAGE_PATHS_JSON': '["ui.png"]', 'USER_REFERENCE_IMAGE_METADATA_JSON': json.dumps(metadata)}):
                self.assertEqual(visual._synchronized_reference_image_ids({'reference_image_ids': ['图2']}, '界面', '界面', {}, []), ['图2'])
                self.assertIn('界面/产品/环境参考可用于无人', visual._reference_image_instruction())

    def test_user_presenter_required_every_image_replaces_default_and_is_bound(self):
        request = {
            'reference_image_ids': ['presenter.png'],
            'reference_image_labels': {'presenter.png': '图1'},
            'reference_image_notes': {'presenter.png': '她是讲解员，每张图都要有她'},
        }
        bible = materials.reference_character_bible(request)
        self.assertIn('图1', bible)
        self.assertIn('不得改用内置默认角色', bible)
        self.assertNotIn('红色围巾', visual.build_visual_prompt_system(
            content_mode=visual.CONTENT_MODE_SCIENCE, global_character_prompt=bible))
        metadata = materials.request_reference_catalog(request)
        with patch.dict(os.environ, {
            'USER_REFERENCE_IMAGE_PATHS_JSON': '["presenter.png"]',
            'USER_REFERENCE_IMAGE_METADATA_JSON': json.dumps(metadata, ensure_ascii=False),
        }):
            self.assertEqual(materials.required_every_shot_labels(characters_only=True), ['图1'])
            self.assertEqual(visual._synchronized_reference_image_ids(
                {'reference_image_ids': []}, '普通科普画面', '普通科普画面', {}, []), ['图1'])
            instruction = visual._reference_image_instruction()
        self.assertIn('所有镜头必须选择这些编号', instruction)

    def test_empty_character_field_does_not_resurrect_science_default(self):
        prompt = visual.build_visual_prompt_system(
            content_mode=visual.CONTENT_MODE_SCIENCE, global_character_prompt='')
        self.assertIn('不启用模式默认角色', prompt)
        self.assertNotIn('红色围巾', prompt)

    def test_job_creation_uses_explicit_material_presenter_instead_of_science_default(self):
        payload = GenerateRequest(
            project_name='参考图测试', script='这是一段用于验证科普讲解员的测试文案。',
            skip_tts=True, source_audio_id='existing.wav', content_mode='science_explainer',
            global_character_prompt='', reference_image_ids=['presenter.png'],
            reference_image_labels={'presenter.png': '图1'},
            reference_image_notes={'presenter.png': '讲解员，每张图都使用'},
        )
        job = SimpleNamespace(snapshot=lambda: {'ok': True})
        with patch.object(main_api, 'require_user', return_value={'id': 1}), patch.object(
            main_api, '_required_job_config_error', return_value=None
        ), patch.object(main_api.store, 'create', return_value=job) as create, patch.object(main_api.store, 'run_async'):
            main_api.create_job(payload, SimpleNamespace())
        request = create.call_args.args[0]
        self.assertIn('图1', request['global_character_prompt'])
        self.assertNotIn('红色围巾', request['global_character_prompt'])
        self.assertNotIn('红色围巾', request['visual_prompt_system'])

    def test_required_presenter_overrides_automatic_empty_scene(self):
        scenes = [{'slide_id': 'scene_001', 'start': 0, 'end': 5,
                   'text_content': '金属导热很快', 'visual_summary': '金属导热示意'}]
        mapping = [{'includes_slides': ['scene_001'], 'image_prompt': '金属导热示意',
                    'character_ids': [], 'reference_image_ids': [], 'human_presence': 'none'}]
        metadata = [{'label': '图1', 'kind': 'unknown', 'description': '她是讲解员，每张图都要有她'}]
        with patch.dict(os.environ, {
            'USER_REFERENCE_IMAGE_PATHS_JSON': '["presenter.png"]',
            'USER_REFERENCE_IMAGE_METADATA_JSON': json.dumps(metadata, ensure_ascii=False),
            'VISUAL_STYLE_PROMPT': '',
        }, clear=False):
            result = visual._finalize_mapping(mapping, scenes, {})
        self.assertEqual(result[0]['reference_image_ids'], ['图1'])
        self.assertNotIn('纯场景或静物画面，无人物出镜', result[0]['image_prompt'])

    def test_invalid_or_too_many_selected_references_are_rejected(self):
        for selected in (['图9'], ['图1', '图2', '图3', '图4']):
            with self.assertRaises(ValueError):
                materials.bind_material_references([{'image_prompt': '镜头', 'reference_image_ids': selected}], {f'图{i}': f'{i}.png' for i in range(1, 7)})

    def test_empty_scene_respects_user_material_purpose_not_initial_type(self):
        metadata = [{'label': '图1', 'kind': 'character', 'description': '只参考人物背后的出租屋，不参考人物'},
                    {'label': '图2', 'kind': 'character', 'description': '这是女主角'}]
        item = {'image_prompt': '出租屋静物', 'character_ids': ['女主角'], 'reference_image_ids': ['图1', '图2'],
                'visual_design': {'message': '局促的生活空间'}}
        empty = {'index': 0, 'image_prompt': '出租屋静物', 'human_presence': 'none', 'conflicts': []}
        checked = {**empty, 'empty_scene_confirmed': True, 'evidence': '只展示房间，无人物动作', 'retained_reference_image_ids': ['图1']}
        with patch.dict(os.environ, {'USER_REFERENCE_IMAGE_METADATA_JSON': json.dumps(metadata)}), patch.object(
            prompt_editor, 'generate_gemini_text', side_effect=[json.dumps({'items': [empty]}), json.dumps({'items': [checked]})]
        ) as call:
            result = prompt_editor.finalize_prompts([item], [], {})
        self.assertEqual(result[0]['reference_image_ids'], ['图1'])
        self.assertEqual(result[0]['character_ids'], [])
        self.assertIn('只参考人物背后的出租屋', call.call_args.kwargs['user_prompt'])

    def test_archived_non_scene_references_resolve_inside_output(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            assets = project / 'other' / 'scene_references'
            assets.mkdir(parents=True)
            (assets / 'ui.png').write_bytes(b'ui image')
            (project / 'other' / '画面映射.json').write_text(json.dumps([{
                'macro_scene_id': 'poster_001', 'image_prompt': '界面', 'reference_binding_version': 1,
                'reference_image_paths': ['Z:/deleted_workspace/ui.png'],
                'reference_materials': [{'label': '图5', 'input_number': 1, 'description': '软件界面'}]
            }]), encoding='utf-8')
            result = VisualEditor()._load_mapping(project)[0]
            self.assertEqual(Path(result['reference_image_paths'][0]).resolve(), (assets / 'ui.png').resolve())
            self.assertEqual(result['reference_materials'][0]['label'], '图5')

    def test_vision_payloads_support_all_existing_language_protocols(self):
        for protocol in ('openai', 'anthropic', 'gemini'):
            response_data = {'choices': [{'message': {'content': 'ok'}}], 'content': [{'type': 'text', 'text': 'ok'}], 'candidates': [{'content': {'parts': [{'text': 'ok'}]}}]}
            with self.subTest(protocol=protocol), patch.dict(os.environ, {'TEST_REFERENCE_API_KEY': 'test'}), patch.object(gemini_client, '_provider', return_value='test'), patch.object(
                gemini_client, 'language_provider_config', return_value={'protocol': protocol, 'key_env': 'TEST_REFERENCE_API_KEY', 'label': 'test'}
            ), patch.object(gemini_client, 'language_base_url', return_value='https://example.test'), patch.object(gemini_client, '_gemini_models', return_value=['test']), patch.object(
                gemini_client.requests, 'post', return_value=SimpleNamespace(ok=True, json=lambda: response_data)
            ) as post:
                result = gemini_client.generate_gemini_text(system_prompt='Describe', user_prompt='Image', image_data={'mime_type': 'image/jpeg', 'data': 'AAA'})
                payload = post.call_args.kwargs['json']
                self.assertEqual(result, 'ok')
                self.assertIn('AAA', json.dumps(payload))
                if protocol == 'openai':
                    self.assertEqual(payload['messages'][1]['content'][1]['type'], 'image_url')
                elif protocol == 'anthropic':
                    self.assertEqual(payload['messages'][0]['content'][0]['type'], 'image')
                else:
                    self.assertEqual(payload['contents'][0]['parts'][1]['inlineData']['data'], 'AAA')

    def test_manual_redraw_replaces_and_archives_automatic_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / 'image').mkdir()
            (project / 'other' / 'scene_references').mkdir(parents=True)
            original = project / 'other' / 'scene_references' / 'old.png'
            original.write_bytes(b'old reference')
            manual = project / 'manual.png'
            manual.write_bytes(b'manual reference')
            (project / 'image' / 'poster_001.jpg').write_bytes(b'old image')
            mapping_path = project / 'other' / '画面映射.json'
            mapping_path.write_text(json.dumps([{'macro_scene_id': 'poster_001', 'image_prompt': '场景', 'reference_binding_version': 1,
                'reference_image_ids': ['图3'], 'reference_image_paths': [str(original)],
                'reference_materials': [{'label': '图3', 'description': '旧界面'}]}]), encoding='utf-8')
            editor = VisualEditor()
            captured = {}
            def render(item, pool):
                captured.update(item)
                target = Path(item['_output_path'])
                target.write_bytes(b'new image')
                return target
            with patch.object(editor, 'output_dir', return_value=project), patch.object(editor, '_log'), patch('backend.app.visual_editor.JOBS_DIR', project / 'jobs'), patch.object(
                visual, '_provider_configs', return_value=[{'api_key': 'test', 'endpoint': '/generate', 'ratio': '2:1', 'resolution': '1k'}]
            ), patch.object(visual, '_render_poster_with_retry', side_effect=render):
                editor.redraw(job=SimpleNamespace(id='ref-redraw', user_id=1, request={}), macro_id='poster_001', prompt='【参考图编号】旧编号说明\n展示手动素材', reference_upload_paths=[str(manual)])
                for _ in range(200):
                    status = editor.status('ref-redraw')['image_tasks'].get('poster_001', {})
                    if status.get('status') != 'running':
                        break
                    time.sleep(0.01)
            self.assertEqual(status.get('status'), 'completed', status)
            self.assertEqual(len(captured['reference_image_paths']), 1)
            self.assertEqual(captured['image_prompt'].count('【参考图编号】'), 1)
            self.assertEqual(Path(captured['reference_image_paths'][0]).read_bytes(), b'manual reference')
            saved = editor._load_mapping(project)[0]
            self.assertEqual(saved['reference_image_ids'], [])
            self.assertEqual(saved['reference_materials'][0]['description'], '本次手动重绘参考')
            self.assertEqual(Path(saved['reference_image_paths'][0]).read_bytes(), b'manual reference')
            self.assertEqual(original.read_bytes(), b'old reference')


if __name__ == '__main__':
    unittest.main()
