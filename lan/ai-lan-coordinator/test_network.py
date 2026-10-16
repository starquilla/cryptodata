import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from coordinator import Client, Store, Rejected, make_server, execute_once, write_json
from network import Discovery, allowed, discover, storage_guard, StoragePaused


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TEMP'))
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'queue.sqlite')
        self.server, self.pin = make_server(self.store, self.root / 'tls', host='0.0.0.0', port=0,
                                            allowed_peers=['127.0.0.0/8'])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.discovery = Discovery(['127.0.0.0/8'], 0, self.server.server_port, self.pin, bind='127.0.0.1')
        self.discovery.start()
        self.config = {'allowed_networks': ['127.0.0.0/8'], 'bootstrap_addresses': [],
                       'discovery': {'enabled': True, 'port': self.discovery.port, 'targets': ['127.0.0.1']}}

    def tearDown(self):
        self.discovery.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def test_failed_hostname_recovers_with_nonce_discovery_and_pinned_https(self):
        with patch('coordinator.resolve_ipv4', return_value=[]):
            client = Client('https://missing.invalid:8840', self.pin, config=self.config)
            self.assertEqual('0.3', client.post('/probe', {})['version'])
            self.assertEqual(('127.0.0.1', self.server.server_port), client.last_endpoint)

    def test_stale_cached_ip_re_resolves_hostname_to_new_ip(self):
        client = Client('https://pc1.invalid:' + str(self.server.server_port), self.pin, config=self.config)
        client.last_endpoint = ('127.0.0.2', 1)
        with patch('coordinator.resolve_ipv4', return_value=['127.0.0.1']):
            self.assertEqual('0.3', client.post('/probe', {})['version'])
        self.assertEqual('127.0.0.1', client.last_endpoint[0])

    def test_new_worker_source_within_lan_is_not_blocked_by_old_exact_ip(self):
        self.assertTrue(allowed('192.168.1.123', ['192.168.1.0/24']))
        self.assertFalse(allowed('192.168.10.123', ['192.168.1.0/24']))
        self.assertFalse(allowed('8.8.8.8', ['192.168.1.0/24']))
        with self.assertRaises(Rejected):
            Client('https://127.0.0.1:' + str(self.server.server_port), self.pin).post('/claim', {})

    def test_forged_hint_cannot_bypass_tls_pin_or_send_enrollment(self):
        code = self.store.prepare_enrollment('fake', ['inventory'])
        with patch('coordinator.resolve_ipv4', return_value=[]), patch('coordinator.discover', return_value=[('127.0.0.1',self.server.server_port)]):
            with self.assertRaises(Rejected):
                Client('https://missing.invalid', '0'*64, config=self.config).post('/enroll', {'name':'fake','code':code})
        self.assertEqual([], self.store.snapshot()['workers'])

    def test_discovery_ignores_wrong_service_nonce_or_pin(self):
        self.assertEqual([], discover(self.config, 'f'*64, timeout=.2))

    def test_disk_low_blocks_new_claim_and_keeps_existing_completion_replayable(self):
        code = self.store.prepare_enrollment('A', ['inventory'])
        token = self.store.enroll('A', code)
        client = Client('https://127.0.0.1:' + str(self.server.server_port), self.pin, {'name':'A','token':token})
        self.store.enqueue('one','inventory',{})
        job = self.store.claim('A',['inventory'])
        result = {'hostname':'fixture'}
        self.store.complete('A',job['id'],job['fence'],result)
        outbox = self.root/'outbox'
        write_json(outbox/'one.pending.json',{'worker':'A','completion':{'id':job['id'],'fence':job['fence'],'result':result,'success':True}})
        with self.assertRaises(StoragePaused):
            execute_once(client,outbox,lambda: storage_guard(self.root,{'storage':{'minimum_disk_free_bytes':10**30}}))
        self.assertFalse(list(outbox.glob('*.pending.json')))
        self.assertTrue(list(outbox.glob('*.acked')))

    def test_project_limit_and_wrong_drive_fail_closed_without_deletion(self):
        sample = self.root/'keep.bin'
        sample.write_bytes(b'0123456789')
        with self.assertRaises(StoragePaused):
            storage_guard(self.root,{'storage':{'project_limit_bytes':10,'pause_at_bytes':5,'minimum_disk_free_bytes':0}})
        self.assertTrue(sample.exists())
        with self.assertRaises(StoragePaused):
            storage_guard(self.root,{'storage':{'required_drive_by_host':{socket.gethostname():'Z:'}}})


if __name__ == '__main__':
    unittest.main(verbosity=2)
