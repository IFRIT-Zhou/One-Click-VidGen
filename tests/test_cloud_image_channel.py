import unittest
from unittest.mock import patch, MagicMock
from pydantic import ValidationError
from backend.app.cloud_image_channel import configure_channel, generation_payload, REGULAR_SIZES
from backend.app.image_studio import ImageRequest, config_for
from backend.app.main import GenerateRequest
from backend.app.video_studio import _image_configs


class ImageChannelTests(unittest.TestCase):
    def test_ican_reference_upload_failure_never_falls_back_to_data_uri(self):
        import tempfile
        from pathlib import Path
        import module4_video_render as visual
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reference.png'
            path.write_bytes(b'fake image')
            response = MagicMock(ok=False)
            response.json.return_value = {'error': 'upload failed'}
            config = configure_channel(dict(api_key='test', cloud_pool='1', cloud_base_url='https://example'), 'ican')
            with patch.object(visual, '_request_with_cloud_refresh', return_value=response):
                with self.assertRaises(visual.RunningHubReferenceUploadError):
                    visual._reference_image_url(config, str(path))

    def test_edit_existing_project_preserves_channel_and_size(self):
        import tempfile
        from pathlib import Path
        from backend.app import video_studio as studio
        with tempfile.TemporaryDirectory() as directory:
            record = dict(id='test', revision=1, status='draft', settings={'name':'test'}, logs=[], shots=[], creation_parameters={})
            data = studio.ProjectSettingsEdit(revision=1, name='test', parameters={
                'method':'ican', 'size':'1440x2560', 'use_cloud_image_pool':True})
            with patch.object(studio, 'require_user', return_value={'id':1}), \
                 patch.object(studio, 'directory', return_value=Path(directory)), \
                 patch.object(studio, 'read', return_value=record), patch.object(studio, 'save'):
                studio.edit_project_settings('test', data, MagicMock())
            self.assertEqual(record['creation_parameters']['method'], 'ican')
            self.assertEqual(record['creation_parameters']['size'], '1440x2560')
            self.assertTrue(record['creation_parameters']['use_cloud_image_pool'])

    def test_shared_pool_does_not_reuse_another_channel_or_size(self):
        from module4_video_render import shared_runninghub_account_pool
        base = dict(api_key='test', endpoint='https://example/generate', cloud_pool='1',
                    cloud_base_url='https://example')
        configs = [configure_channel(base, 'running'), configure_channel(base, 'ican'),
                   configure_channel(base, 'ican', '1440x2560')]
        pools = [shared_runninghub_account_pool([config], namespace='channel-test') for config in configs]
        self.assertEqual(len({id(pool) for pool in pools}), 3)

    def test_all_regular_sizes_and_default(self):
        base = dict(cloud_pool='1', cloud_base_url='https://pool.example/api/v1')
        for size in REGULAR_SIZES:
            config = configure_channel(base, 'ican', size)
            payload = generation_payload(dict(prompt='tree', aspectRatio='2:1', resolution='4k',
                imageUrls=['reference'], clientJobId='stable-task-123'), config)
            self.assertEqual(payload['size'], size)
            self.assertEqual(payload['method'], 'ican')
            self.assertEqual(payload['model'], 'gpt-image-2.5')
            self.assertNotIn('aspectRatio', payload)
            self.assertNotIn('resolution', payload)
            self.assertEqual(payload['clientJobId'], 'stable-task-123')
            self.assertEqual(payload['imageUrls'], ['reference'])
            self.assertTrue(config['upload_url'].endswith('?provider=ican'))
        self.assertEqual(configure_channel(base, 'ican')['size'], '2560x1440')

    def test_running_and_custom_preserve_original_parameters(self):
        payload = dict(prompt='tree', aspectRatio='9:16', resolution='2k')
        self.assertEqual(generation_payload(payload, {}), payload)
        actual = generation_payload(payload, dict(cloud_pool='1'))
        self.assertEqual(actual, {**payload, 'method':'running', 'provider':'runninghub'})

    def test_invalid_sizes_rejected_at_local_boundary(self):
        for size in ['3072x1728', '3072x2560', '3840x2160', '2048x2048', 'bogus']:
            with self.assertRaises(ValidationError):
                ImageRequest(prompt='tree', method='ican', size=size)
        self.assertEqual(ImageRequest(prompt='tree').size, '2560x1440')
        self.assertEqual(GenerateRequest.model_fields['size'].default, '2560x1440')

    def test_studio_and_video_configs_preserve_selected_size(self):
        client = MagicMock()
        client.image_pool_runtime.return_value = dict(base_url='https://pool.example/api/v1',
                                                     access_token='token', refresh_token='refresh')
        with patch('backend.app.cloud_client.cloud_client_for', return_value=client):
            config, _ = config_for(1, ImageRequest(prompt='tree', provider='pool', method='ican', size='1440x2560'))
            configs = _image_configs(dict(creation_parameters=dict(use_cloud_image_pool=True,
                method='ican',size='1440x2560')), user_id=1)
        for item in [config, configs[0]]:
            self.assertEqual(item['size'], '1440x2560')
            self.assertEqual(item['method'], 'ican')
