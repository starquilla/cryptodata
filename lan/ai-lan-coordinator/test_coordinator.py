import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest

from coordinator import Client, Rejected, Store, execute_once, make_server


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = [1000.0]
        self.store = Store(self.root / 'queue.sqlite', clock=lambda: self.now[0])

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, key='one', **kwargs):
        return self.store.enqueue(key, 'hash_text', {'text': 'hello LAN'}, **kwargs)

    def test_idempotency_survives_restart_and_rejects_changed_request(self):
        first = self.add()
        restarted = Store(self.root / 'queue.sqlite', clock=lambda: self.now[0])
        self.assertEqual(first, restarted.enqueue('one', 'hash_text', {'text': 'hello LAN'}))
        with self.assertRaises(Rejected):
            restarted.enqueue('one', 'hash_text', {'text': 'changed'})
        self.assertEqual(1, len(restarted.snapshot()['jobs']))

    def test_competing_claims_only_one_owns_resource(self):
        self.add('a', resource='shared')
        self.add('b', resource='shared')
        barrier = threading.Barrier(2)
        results = []
        def claim(name):
            barrier.wait()
            results.append(self.store.claim(name, ['hash_text']))
        threads = [threading.Thread(target=claim, args=(name,)) for name in ('A', 'B')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(1, sum(job is not None for job in results))

    def test_disconnect_read_recovery_fences_old_owner(self):
        self.add()
        old = self.store.claim('A', ['hash_text'], lease=2)
        self.now[0] += 3
        new = Store(self.root / 'queue.sqlite', clock=lambda: self.now[0]).claim('B', ['hash_text'], lease=2)
        self.assertEqual(old['id'], new['id'])
        self.assertGreater(new['fence'], old['fence'])
        with self.assertRaises(Rejected):
            self.store.complete('A', old['id'], old['fence'], {'old': True})
        self.store.complete('B', new['id'], new['fence'], {'good': True})
        self.assertEqual('succeeded', self.store.snapshot()['jobs'][0]['state'])

    def test_uncertain_write_blocks_resource_until_human_evidence(self):
        # Synthetic write metadata only: NO write adapter is enabled in release registry.
        self.store.registry['write_fixture'] = {'enabled': True, 'effect': 'write'}
        first = self.store.enqueue('write', 'write_fixture', {}, resource='database')
        self.add('next', resource='database')
        self.store.claim('A', ['write_fixture'], lease=2)
        self.now[0] += 3
        self.assertIsNone(self.store.claim('B', ['hash_text', 'write_fixture']))
        states = {job['id']: job['state'] for job in self.store.snapshot()['jobs']}
        self.assertEqual('needs_review', states[first])
        with self.assertRaises(Rejected):
            self.store.reconcile(first, 'failed', '')
        self.store.reconcile(first, 'failed', 'Operator verified target unchanged; fixture only')
        self.assertIsNotNone(self.store.claim('B', ['hash_text']))

    def test_retry_limit_stops_read_crash_loop(self):
        self.add(max_attempts=1)
        self.store.claim('A', ['hash_text'], lease=2)
        self.now[0] += 3
        self.assertIsNone(self.store.claim('B', ['hash_text']))
        self.assertEqual('failed', self.store.snapshot()['jobs'][0]['state'])

    def test_dependency_and_target_gate(self):
        first = self.add(target='A')
        second = self.add('two', target='B', dependency=first)
        self.assertIsNone(self.store.claim('B', ['hash_text']))
        job = self.store.claim('A', ['hash_text'])
        self.store.complete('A', job['id'], job['fence'], {'ok': 1})
        self.assertIsNone(self.store.claim('A', ['hash_text']))
        self.assertEqual(second, self.store.claim('B', ['hash_text'])['id'])

    def test_cooldown_survives_restart_and_cannot_shorten(self):
        self.add(resource='provider:shared-egress')
        self.store.cooldown('provider:shared-egress', 1100, 'test')
        self.store.cooldown('provider:shared-egress', 1050, 'shorter must not reduce deadline')
        restarted = Store(self.root / 'queue.sqlite', clock=lambda: self.now[0])
        self.now[0] = 1075
        self.assertIsNone(restarted.claim('A', ['hash_text']))
        self.now[0] = 1101
        self.assertIsNotNone(restarted.claim('A', ['hash_text']))

    def test_duplicate_ack_is_idempotent_but_conflict_rejected(self):
        self.add()
        job = self.store.claim('A', ['hash_text'])
        self.store.complete('A', job['id'], job['fence'], {'v': 1})
        ack = self.store.complete('A', job['id'], job['fence'], {'v': 1})
        self.assertTrue(ack['duplicate_ack'])
        with self.assertRaises(Rejected):
            self.store.complete('A', job['id'], job['fence'], {'v': 2})

    def test_enrollment_single_use_expiry_capability_and_authentication(self):
        code = self.store.prepare_enrollment('A', ['hash_text'])
        with self.assertRaises(Rejected):
            self.store.enroll('A', 'wrong')
        token = self.store.enroll('A', code)
        self.assertEqual(['hash_text'], self.store.authenticate('A', token))
        with self.assertRaises(Rejected):
            self.store.enroll('A', code)
        with self.assertRaises(Rejected):
            self.store.authenticate('A', 'wrong')
        code = self.store.prepare_enrollment('B', ['hash_text'], ttl=2)
        self.now[0] += 3
        with self.assertRaises(Rejected):
            self.store.enroll('B', code)
        with self.assertRaises(Rejected):
            self.store.prepare_enrollment('C', ['codex'])

    def test_production_tools_and_unbounded_payload_disabled(self):
        for tool in ('codex', 'gemini', 'cryptodata', 'shell'):
            with self.assertRaises(Rejected):
                self.store.enqueue(tool, tool, {})
        with self.assertRaises(Rejected):
            self.store.enqueue('big', 'hash_text', {'text': 'a' * 17000})


class HTTPSIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'queue.sqlite')
        self.server, self.pin = make_server(self.store, self.root / 'tls', port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'https://127.0.0.1:' + str(self.server.server_port)
        self.clients = {}
        for name in ('PC1_SIM', 'PC2_SIM'):
            code = self.store.prepare_enrollment(name, ['hash_text', 'text_stats'])
            token = Client(self.url, self.pin).post('/enroll', {'name': name, 'code': code})['token']
            self.clients[name] = Client(self.url, self.pin, {'name': name, 'token': token})

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def test_real_https_two_simulated_workers_dependency_and_checksum(self):
        text = 'Two workers one durable workflow'
        a = self.store.enqueue('flow-a', 'hash_text', {'text': text}, target='PC1_SIM')
        b = self.store.enqueue('flow-b', 'text_stats', {'text': text}, target='PC2_SIM', dependency=a)
        self.assertIsNone(execute_once(self.clients['PC2_SIM'], self.root / 'b'))
        self.assertEqual(a, execute_once(self.clients['PC1_SIM'], self.root / 'a')['id'])
        self.assertEqual(b, execute_once(self.clients['PC2_SIM'], self.root / 'b')['id'])
        jobs = {j['id']: j for j in self.store.snapshot()['jobs']}
        self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), json.loads(jobs[a]['result'])['sha256'])
        self.assertEqual(5, json.loads(jobs[b]['result'])['words'])
        self.assertTrue(all(j['state'] == 'succeeded' for j in jobs.values()))

    def test_pin_mismatch_rejected_before_enrollment(self):
        code = self.store.prepare_enrollment('untrusted', ['hash_text'])
        with self.assertRaises(Rejected):
            Client(self.url, '0' * 64).post('/enroll', {'name': 'untrusted', 'code': code})
        self.assertNotIn('untrusted', [w['name'] for w in self.store.snapshot()['workers']])

    def test_completed_but_response_lost_replays_outbox_without_reexecution(self):
        self.store.enqueue('lost-ack', 'hash_text', {'text': 'saved result'}, target='PC1_SIM')
        real = self.clients['PC1_SIM']
        class DropResponse:
            identity = real.identity
            def post(self, path, value):
                result = real.post(path, value)
                if path == '/complete':
                    raise OSError('injected disconnect AFTER server committed completion')
                return result
        outbox = self.root / 'outbox'
        with self.assertRaises(OSError):
            execute_once(DropResponse(), outbox)
        self.assertEqual(1, len(list(outbox.glob('*.pending.json'))))
        self.assertIsNone(execute_once(real, outbox))
        snapshot = self.store.snapshot()
        self.assertEqual(1, snapshot['jobs'][0]['attempt'])
        self.assertEqual(1, sum(e['kind'] == 'completed' for e in snapshot['events']))
        self.assertEqual(0, len(list(outbox.glob('*.pending.json'))))

    def test_stale_outbox_retained_and_worker_stops_for_review(self):
        self.store.enqueue('stale', 'hash_text', {'text': 'saved result'}, target='PC1_SIM')
        real = self.clients['PC1_SIM']
        class DropBeforeCommit:
            identity = real.identity
            def post(self, path, value):
                if path == '/complete':
                    raise OSError('injected disconnect BEFORE completion reached server')
                return real.post(path, value)
        outbox = self.root / 'stale-outbox'
        with self.assertRaises(OSError):
            execute_once(DropBeforeCommit(), outbox)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET lease_until=0 WHERE idem='stale'")
        with self.assertRaises(Rejected):
            execute_once(real, outbox)
        self.assertEqual(1, len(list(outbox.glob('*.pending.json'))))
        self.assertEqual(1, len(list(outbox.glob('*.review.json'))))


if __name__ == '__main__':
    unittest.main(verbosity=2)
