import unittest
from unittest.mock import patch
import probe_asset_coverage as coverage


class CoverageSettingsTests(unittest.TestCase):
    def test_only_planned_round_counts(self):
        for value in ('0','-1','2','4','1.5','garbage'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                coverage.run_settings({'PROBE_ROUNDS':value})
        for value in ('1','3'):
            self.assertEqual(coverage.run_settings({'PROBE_ROUNDS':value})[0],int(value))

    def test_pacing_cannot_be_lowered_below_two(self):
        for value in ('0','0.1','1.99','2'):
            self.assertEqual(coverage.run_settings({'BINANCE_FUTURES_PAUSE_SECONDS':value})[1],2)
        self.assertEqual(coverage.run_settings({'BINANCE_FUTURES_PAUSE_SECONDS':'5'})[1],5)

    def test_nonfinite_negative_and_invalid_pacing_rejected(self):
        for value in ('NaN','Infinity','-Infinity','-1','bad'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                coverage.run_settings({'BINANCE_FUTURES_PAUSE_SECONDS':value})

    def test_bad_settings_fail_before_job_loading_or_network(self):
        with patch.dict(coverage.os.environ, {'PROBE_ROUNDS':'0'}), \
             patch.object(coverage,'jobs') as jobs, \
             patch.object(coverage.urllib.request,'urlopen') as network:
            with self.assertRaises(ValueError):coverage.main()
            jobs.assert_not_called()
            network.assert_not_called()

    def test_empty_jobs_never_reports_pass_or_calls_network(self):
        with patch.dict(coverage.os.environ, {'PROBE_ROUNDS':'1','BINANCE_FUTURES_PAUSE_SECONDS':'2'}), \
             patch.object(coverage,'jobs',return_value=([],{})), \
             patch.object(coverage.urllib.request,'urlopen') as network:
            with self.assertRaises(ValueError):coverage.main()
            network.assert_not_called()


if __name__=='__main__':unittest.main()
