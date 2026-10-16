import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
from controlled_probe import execute_probe,lease,main


class ControlledProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=pathlib.Path(self.temp.name)
        self.script=self.root/'probe_cloud_sources.py'
        self.state=self.root/'state'

    def fixture(self,code):
        self.script.write_text(code,encoding='utf8')

    def test_cooldown_survives_new_child_process_and_replay_refused(self):
        self.fixture("import os,json,pathlib; p=pathlib.Path(os.environ['PROBE_STATE_PATH']); s=json.loads(p.read_text()); s['fapi.binance.com']={'until':9999999999,'http_status':418}; p.write_text(json.dumps(s)); print('CRYPTODATA_PROBE_REPORT='+json.dumps({'status':'FAIL','cooldowns':s}))")
        execute_probe(self.script,self.state,'first')
        self.fixture("import os,json,pathlib; s=json.loads(pathlib.Path(os.environ['PROBE_STATE_PATH']).read_text()); assert s['fapi.binance.com']['until']==9999999999; print('CRYPTODATA_PROBE_REPORT='+json.dumps({'status':'FAIL','cooldowns':s}))")
        r=execute_probe(self.script,self.state,'second')
        self.assertEqual(r['cooldowns']['fapi.binance.com']['http_status'],418)
        with self.assertRaises(FileExistsError):execute_probe(self.script,self.state,'first')

    def test_concurrent_and_abandoned_lock_fail_closed(self):
        self.fixture("raise AssertionError('child must not run')")
        with lease(self.state,'owner'):
            with self.assertRaises(FileExistsError):execute_probe(self.script,self.state,'other')
        (self.state/'probe.lock').write_text('{"owner":"crashed"}')
        with self.assertRaises(FileExistsError):execute_probe(self.script,self.state,'after-crash')

    def test_timeout_stops_child_retains_receipt_and_rejects_retry(self):
        self.fixture('import time; time.sleep(30)')
        with self.assertRaises(TimeoutError):execute_probe(self.script,self.state,'timeout',timeout=.05)
        self.assertFalse((self.state/'probe.lock').exists())
        receipt=json.loads((self.state/'runs/timeout/receipt.json').read_text())
        self.assertEqual(receipt['status'],'FAILED')
        with self.assertRaises(FileExistsError):execute_probe(self.script,self.state,'timeout')

    def test_false_pass_on_child_failure_is_rejected(self):
        self.fixture("print('CRYPTODATA_PROBE_REPORT={\"status\":\"PASS\"}'); raise SystemExit(2)")
        with self.assertRaises(ValueError):execute_probe(self.script,self.state,'bad-exit')

    def test_missing_report_is_failure_and_seed_persists(self):
        self.fixture("print('no final report')")
        with self.assertRaises(ValueError):
            execute_probe(self.script,self.state,'missing',environ={'PROBE_COOLDOWNS_JSON':'{"fapi.binance.com":{"until":9999999999}}'})
        self.assertEqual(json.loads((self.state/'rate-cooldowns.json').read_text())['fapi.binance.com']['until'],9999999999)

    def test_bad_run_id_and_relative_state_do_not_launch(self):
        for root,run_id in ((self.state,'../escape'),(pathlib.Path('relative'),'valid')):
            with self.assertRaises(ValueError):execute_probe(self.script,root,run_id)

    def test_cli_future_or_expired_window_never_starts_process(self):
        base=['--state-dir',str(self.state),'--run-id','cli','--sources','okx_spot']
        for bounds in (['2099-01-01T00:00:00+00:00','2099-01-02T00:00:00+00:00'],
                       ['2000-01-01T00:00:00+00:00','2000-01-02T00:00:00+00:00']):
            with patch('controlled_probe.execute_probe') as launch:
                with self.assertRaises(ValueError):main(base+['--not-before',bounds[0],'--deadline',bounds[1]])
                launch.assert_not_called()

    def test_cli_requires_timezone(self):
        with patch('controlled_probe.execute_probe') as launch:
            with self.assertRaises(ValueError):
                main(['--state-dir',str(self.state),'--run-id','cli','--sources','okx_spot',
                      '--not-before','2000-01-01T00:00:00','--deadline','2099-01-01T00:00:00'])
            launch.assert_not_called()


if __name__=='__main__':unittest.main()
