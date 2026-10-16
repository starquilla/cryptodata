import copy
import unittest
from cloud_launch_gate import launch_gate


class LaunchGateTests(unittest.TestCase):
    def setUp(self):
        self.now = 10000
        self.obs = dict(observed_at=10000, not_before=9999, deadline=20000,
                        max_duration_seconds=500, next_pc_collection=19000,
                        cooldowns={}, no_pc_collector=True, no_other_probe=True,
                        no_github_run=True, lock_acquired=True,
                        cooldown_observed=True, free_plan=True, auto_deploy_off=True,
                        operation='deploy_reviewed_commit', suspended=False,
                        probe_mode='sample', reviewed_commit='a'*40,
                        requested_commit='a'*40, binance_pause_seconds=2)

    def test_complete_evidence_allows_only_decision(self):
        self.assertTrue(launch_gate(self.obs, self.now)['allowed'])

    def test_not_before_and_newer_cooldown(self):
        self.obs['not_before'] = self.now + 1
        self.obs['cooldowns'] = {'fapi.binance.com': {'until': self.now+100}}
        r = launch_gate(self.obs, self.now)
        self.assertIn('before_not_before', r['reasons'])
        self.assertIn('cooldown:fapi.binance.com', r['reasons'])

    def test_missing_stale_and_future_evidence_blocked(self):
        for stamp in (self.now-61, self.now+1):
            self.obs['observed_at'] = stamp
            self.assertFalse(launch_gate(self.obs, self.now)['allowed'])
        self.assertFalse(launch_gate({}, self.now)['allowed'])

    def test_overlap_unknown_and_unheld_lock_blocked(self):
        for key in ('no_pc_collector', 'no_other_probe', 'no_github_run', 'lock_acquired'):
            obs = copy.deepcopy(self.obs)
            obs[key] = None
            self.assertFalse(launch_gate(obs, self.now)['allowed'])

    def test_resume_old_and_suspended_blocked(self):
        self.obs.update(operation='resume', suspended=True)
        self.assertFalse(launch_gate(self.obs, self.now)['allowed'])

    def test_pc_start_and_deadline_budget_blocked(self):
        for key in ('next_pc_collection', 'deadline'):
            obs = copy.deepcopy(self.obs)
            obs[key] = self.now+620
            self.assertFalse(launch_gate(obs, self.now)['allowed'])

    def test_wrong_commit_mode_and_fast_pacing_blocked(self):
        for key, value in (('requested_commit', 'b'*40), ('probe_mode', 'coverage'),
                           ('binance_pause_seconds', 1.9)):
            obs = copy.deepcopy(self.obs)
            obs[key] = value
            self.assertFalse(launch_gate(obs, self.now)['allowed'])

    def test_nonfinite_times_cooldown_and_pacing_blocked(self):
        for key in ('observed_at', 'deadline', 'binance_pause_seconds'):
            obs = copy.deepcopy(self.obs)
            obs[key] = float('nan')
            self.assertFalse(launch_gate(obs, self.now)['allowed'])
        self.obs['cooldowns'] = {'fapi.binance.com': {'until': float('inf')}}
        self.assertFalse(launch_gate(self.obs, self.now)['allowed'])


if __name__ == '__main__':
    unittest.main()
