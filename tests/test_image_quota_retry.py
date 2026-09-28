import unittest
from unittest.mock import patch

import module4_video_render as visual


class ImageQuotaRetryTests(unittest.TestCase):
    def setUp(self):
        self.state = patch.object(visual, '_POWER_EXHAUSTED_ACCOUNT_KEYS', set())
        self.pools = patch.object(visual, '_SHARED_ACCOUNT_POOLS', {})
        self.state.start()
        self.pools.start()
        self.addCleanup(self.state.stop)
        self.addCleanup(self.pools.stop)
        self.configs = [{'api_key': 'quota-retry-test', 'account_label': 'test', 'cloud_pool': '1'}]

    def test_explicit_retry_recovers_but_same_attempt_stays_blocked(self):
        pool = visual.shared_runninghub_account_pool(self.configs, namespace='video_storyboard')
        pool.mark_power_exhausted(pool.acquire())
        with self.assertRaises(visual.RunningHubAllAccountsPowerInsufficient):
            pool.acquire()
        retry = visual.shared_runninghub_account_pool(
            self.configs, namespace='video_storyboard', retry_power_exhausted=True,
        )
        self.assertIs(retry, pool)
        lease = retry.acquire()
        retry.mark_power_exhausted(lease)
        with self.assertRaises(visual.RunningHubAllAccountsPowerInsufficient):
            retry.acquire()

    def test_new_redraw_namespace_can_recover_global_quota_mark(self):
        pool = visual.shared_runninghub_account_pool(self.configs, namespace='video_storyboard')
        pool.mark_power_exhausted(pool.acquire())
        redraw = visual.shared_runninghub_account_pool(
            self.configs, namespace='video_storyboard_redraw', retry_power_exhausted=True,
        )
        lease = redraw.acquire()
        redraw.release(lease)
        self.assertNotIn('quota-retry-test', visual._POWER_EXHAUSTED_ACCOUNT_KEYS)

    def test_retry_preserves_active_leases_and_other_accounts(self):
        pool = visual.RunningHubAccountPool(self.configs, per_key_concurrency=2)
        active = pool.acquire()
        failed = pool.acquire()
        pool.mark_power_exhausted(failed)
        visual._POWER_EXHAUSTED_ACCOUNT_KEYS.add('another-user')
        pool.retry_power_exhausted_accounts()
        self.assertEqual(pool._inflight['quota-retry-test'], 1)
        self.assertIn('quota-retry-test', pool._power_exhausted)
        pool.release(active)
        pool.retry_power_exhausted_accounts()
        self.assertIn('another-user', visual._POWER_EXHAUSTED_ACCOUNT_KEYS)
        lease = pool.acquire()
        pool.release(lease)


if __name__ == '__main__':
    unittest.main()
