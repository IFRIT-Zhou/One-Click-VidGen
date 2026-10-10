import os
import unittest
from unittest.mock import patch

import module4_video_render as visual


class CloudImageConcurrencyTests(unittest.TestCase):
    def test_cloud_default_is_ten(self):
        with patch.dict(os.environ, {}, clear=True):
            configs = [{'cloud_pool': '1', 'api_key': 'test'}]
            self.assertEqual(visual._poster_worker_count(configs, 19), 10)
            self.assertEqual(visual._poster_worker_count(configs, 3), 3)
            self.assertEqual(visual.RunningHubAccountPool(configs)._configured_capacity, 10)

    def test_cloud_override_and_regular_api_independent(self):
        with patch.dict(os.environ, {'CLOUD_IMAGE_POOL_CONCURRENCY': '6',
                                     'RUNNINGHUB_PER_KEY_CONCURRENCY': '2'}, clear=True):
            self.assertEqual(visual._poster_worker_count([{'cloud_pool': '1'}], 19), 6)
            self.assertEqual(visual._poster_worker_count([{}], 19), 2)

    def test_invalid_cloud_setting_uses_default(self):
        with patch.dict(os.environ, {'CLOUD_IMAGE_POOL_CONCURRENCY': 'invalid'}, clear=True):
            self.assertEqual(visual._poster_worker_count([{'cloud_pool': '1'}], 19), 10)

    def test_explicit_retry_clears_only_cloud_rejection(self):
        cloud = {'cloud_pool': '1', 'api_key': 'cloud-test'}
        regular = {'api_key': 'regular-test'}
        pool = visual.RunningHubAccountPool([cloud, regular])
        pool.mark_access_denied(cloud)
        pool.mark_access_denied(regular)
        pool.retry_power_exhausted_accounts()
        self.assertNotIn('cloud-test', pool._access_denied)
        self.assertIn('regular-test', pool._access_denied)
