import base64
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from auto_handoff import prepare, tick, load, pack, PC1, PC2
from coordinator import Store, run_tool, write_json


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.file = self.root/'real.jsonl'
        self.file.write_text('OI dữ liệu thực\n'*2000, encoding='utf-8')
        self.folder = prepare(self.root/'inbox', [self.file], session='test-handoff')
        self.store = Store(self.root/'queue.sqlite')

    def tearDown(self):
        self.tmp.cleanup()

    def work(self, worker, corrupt=False):
        count = 0
        while True:
            job = self.store.claim(worker, ['hash_text'])
            if not job:
                return count
            result = run_tool(job['tool'], job['payload'])
            if corrupt:
                result['sha256'] = '0'*64
            self.store.complete(worker, job['id'], job['fence'], result)
            count += 1

    def test_roundtrip_real_utf8_exact_bytes(self):
        files, blocks = pack([self.file])
        blob = ''.join(b['text'] for b in blocks)
        self.assertEqual(gzip.decompress(base64.b64decode(blob)), self.file.read_bytes())
        self.assertEqual(files[0]['bytes'], self.file.stat().st_size)

    def test_two_distinct_workers_and_restart_resume(self):
        self.assertEqual(tick(self.store,self.folder)['status'], 'WAITING_PC2')
        self.assertEqual(self.work(PC1), 0)
        self.assertGreater(self.work(PC2), 0)
        self.store = Store(self.root/'queue.sqlite')
        self.assertEqual(tick(self.store,self.folder)['status'], 'WAITING_PC1_RETURN')
        self.assertEqual(self.work(PC2), 0)
        self.assertEqual(self.work(PC1), 1)
        self.assertEqual(tick(self.store,self.folder)['status'], 'PASS')
        self.assertTrue((self.folder/'receipt.json').is_file())

    def test_repeat_tick_does_not_duplicate_or_accept_without_pc2(self):
        first = tick(self.store,self.folder)
        second = tick(self.store,self.folder)
        self.assertEqual(first['job_ids'], second['job_ids'])
        self.assertEqual(second['pc2_verified'], 0)

    def test_bad_hash_holds_before_return_handoff(self):
        tick(self.store,self.folder)
        self.work(PC2, corrupt=True)
        state = tick(self.store,self.folder)
        self.assertEqual(state['status'], 'HOLD')
        self.assertNotIn('return_job',state)

    def test_expired_session_does_not_enqueue(self):
        plan = load(self.folder/'plan.json')
        plan['deadline'] = 1
        write_json(self.folder/'plan.json',plan)
        self.assertEqual(tick(self.store,self.folder)['status'], 'TIMED_OUT')
        self.assertEqual(len(self.store.snapshot()['jobs']), 0)

    def test_timeout_cancels_queued_read_jobs(self):
        tick(self.store,self.folder)
        plan = load(self.folder/'plan.json')
        plan['deadline'] = 1
        write_json(self.folder/'plan.json',plan)
        self.assertEqual(tick(self.store,self.folder)['status'], 'TIMED_OUT')
        self.assertIsNone(self.store.claim(PC2,['hash_text']))

    def test_lost_ack_is_idempotent(self):
        tick(self.store,self.folder)
        job = self.store.claim(PC2,['hash_text'])
        result = run_tool(job['tool'],job['payload'])
        self.store.complete(PC2,job['id'],job['fence'],result)
        ack = self.store.complete(PC2,job['id'],job['fence'],result)
        self.assertTrue(ack['duplicate_ack'])
        self.assertEqual(self.store.snapshot()['jobs'][0]['attempt'],1)

    def test_timeout_prevents_running_lease_retry(self):
        tick(self.store,self.folder)
        job = self.store.claim(PC2,['hash_text'])
        plan = load(self.folder/'plan.json')
        plan['deadline'] = 1
        write_json(self.folder/'plan.json',plan)
        self.assertEqual(tick(self.store,self.folder)['status'], 'TIMED_OUT')
        with self.store.db() as db:
            db.execute('UPDATE jobs SET lease_until=1 WHERE id=?',(job['id'],))
        self.assertIsNone(self.store.claim(PC2,['hash_text']))
        final = next(j for j in self.store.snapshot()['jobs'] if j['id']==job['id'])
        self.assertEqual(final['state'],'failed')
        self.assertEqual(final['attempt'],1)

    def test_private_and_duplicate_input_rejected(self):
        private = self.root/'private'
        private.mkdir()
        secret = private/'test.txt'
        secret.write_text('not a credential')
        with self.assertRaises(ValueError):
            pack([secret])
        with self.assertRaises(ValueError):
            pack([self.file,self.file])


if __name__ == '__main__':
    unittest.main(verbosity=2)
